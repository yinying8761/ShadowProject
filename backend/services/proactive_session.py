"""
Proactive session — wraps the ProactiveWatcher lifecycle and all proactive
infrastructure (config refresh, context building, trigger orchestration).

Owns the watcher, the refresh loop, and the on_trigger callback.  The
WebSocket layer only calls start() / stop() / reset_idle().
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Callable

from core.agent import Agent
from services.proactive_watcher import ProactiveWatcher
from services.screen_capture_gate import ScreenCaptureGate
from tools.screen_tools import ScreenFingerprintStore, _capture_dhash_sync
from tools.time_tools import get_current_time


def build_tool_context_message(
    tool_name: str,
    content: str,
    tool_call_id: str,
    arguments: dict | None = None,
) -> list[dict]:
    """Build a synthetic assistant tool_call + tool_result message pair."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments or {}, ensure_ascii=False),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        },
    ]


class ProactiveSession:
    """Manages the full proactive-companion lifecycle for one WebSocket connection."""

    _MIN_VISION_INTERVAL: float = 300.0  # 5 min between vision calls

    def __init__(
        self,
        conversation_id: str,
        char_id_getter: Callable[[], str | None],
        agent: Agent,
        send_json,
        approval_callback,
        get_user_config,
        screen_fingerprints: ScreenFingerprintStore,
        screen_gate: ScreenCaptureGate,
    ):
        self._conversation_id = conversation_id
        self._char_id_getter = char_id_getter
        self._agent = agent
        self._send_json = send_json
        self._approval_callback = approval_callback
        self._get_user_config = get_user_config
        self._screen_fingerprints = screen_fingerprints
        self._screen_gate = screen_gate

        self._watcher: ProactiveWatcher | None = None
        self._refresh_task: asyncio.Task | None = None
        self._last_vision_time: float = 0.0

        # Cached config snapshot, refreshed every 20 s
        self._cached_config: dict = {
            "level": "medium",
            "daily_limit": 10,
            "schedule_enabled": True,
        }

    # ── Public API ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Create and start the watcher, begin config refresh loop."""
        await self._refresh_cached_config()

        print(
            f"[ProactiveSession] starting conv={self._conversation_id[:8]} "
            f"level={self._cached_config['level']}",
            flush=True,
        )

        self._watcher = ProactiveWatcher(
            get_level=self._level_getter,
            get_daily_limit=self._daily_limit_getter,
            get_schedule_enabled=self._schedule_enabled_getter,
            load_state=self._load_state,
            save_state=self._save_state,
            on_trigger=self._on_trigger,
            name=f"watcher[{self._conversation_id[:8]}]",
        )
        self._watcher.start()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Stop the watcher and cancel the config refresh loop."""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        print(f"[ProactiveSession] stopped conv={self._conversation_id[:8]}", flush=True)

    def reset_idle(self) -> None:
        """Notify the watcher that user activity occurred."""
        if self._watcher:
            self._watcher.reset_idle()

    # ── Config helpers ──────────────────────────────────────────────

    async def _refresh_cached_config(self) -> None:
        cfg = await self._get_user_config()
        if not cfg:
            return
        self._cached_config["level"] = cfg.proactive_chat_level
        self._cached_config["daily_limit"] = cfg.proactive_daily_limit
        self._cached_config["schedule_enabled"] = cfg.proactive_fixed_schedule_enabled

    def _level_getter(self) -> str:
        return str(self._cached_config["level"])

    def _daily_limit_getter(self) -> int:
        return int(self._cached_config["daily_limit"])

    def _schedule_enabled_getter(self) -> bool:
        return bool(self._cached_config["schedule_enabled"])

    async def _load_state(self) -> tuple[str, int, set[str]]:
        cfg = await self._get_user_config()
        if not cfg:
            return "", 0, set()
        slots = {s for s in (cfg.proactive_scheduled_slots or "").split(",") if s}
        return cfg.proactive_state_date or "", cfg.proactive_daily_count, slots

    async def _save_state(
        self, state_date: str, daily_count: int, scheduled_slots: set[str]
    ) -> None:
        from database import async_session
        from models.user_config import UserConfig

        async with async_session() as session:
            config = await session.get(UserConfig, 1)
            if config is None:
                config = UserConfig(id=1)
                session.add(config)
            config.proactive_state_date = state_date
            config.proactive_daily_count = daily_count
            config.proactive_scheduled_slots = ",".join(sorted(scheduled_slots))
            await session.commit()

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(20)
                await self._refresh_cached_config()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    # ── Proactive context builder ───────────────────────────────────

    async def _build_proactive_context(self) -> list[dict]:
        cfg = await self._get_user_config()
        silent_approval = cfg.proactive_silent_tool_approval if cfg else False
        auto_see_screen = cfg.proactive_auto_see_screen if cfg else True

        context: list[dict] = []

        # ── Current time ────────────────────────────────────────────
        time_result = await self._run_proactive_tool(
            "get_current_time",
            {},
            get_current_time,
            silent_approval=True,
        )
        if time_result:
            context.extend(
                build_tool_context_message(
                    "get_current_time",
                    time_result,
                    f"proactive-time-{uuid.uuid4().hex[:8]}",
                )
            )

        # ── Screen capture (with throttle + dedup) ──────────────────
        if auto_see_screen:
            now = time.monotonic()
            elapsed = now - self._last_vision_time

            if elapsed < self._MIN_VISION_INTERVAL:
                print(
                    f"[ProactiveSession] vision throttle: {elapsed:.0f}s < "
                    f"{self._MIN_VISION_INTERVAL:.0f}s, skipping screen capture",
                    flush=True,
                )
            else:
                screen_hash = await asyncio.to_thread(_capture_dhash_sync)
                is_new_scene = not self._screen_fingerprints.is_known(screen_hash)
                self._screen_fingerprints.add(screen_hash)

                if not is_new_scene:
                    print(
                        f"[ProactiveSession] screen fingerprint known "
                        f"(queue={len(self._screen_fingerprints)}), skipping vision",
                        flush=True,
                    )
                else:
                    self._last_vision_time = now
                    print(
                        f"[ProactiveSession] new screen scene detected "
                        f"(queue={len(self._screen_fingerprints)}), capturing",
                        flush=True,
                    )
                    focus = "Describe what the user is currently doing and any notable on-screen context."
                    screen_result = await self._screen_gate.try_capture(
                        focus=focus,
                        silent_approval=silent_approval,
                    )
                    if screen_result:
                        context.extend(
                            build_tool_context_message(
                                "see_screen",
                                screen_result,
                                f"proactive-screen-{uuid.uuid4().hex[:8]}",
                                {"focus": focus},
                            )
                        )

        return context

    # ── Tool runner ─────────────────────────────────────────────────

    async def _run_proactive_tool(
        self,
        tool_name: str,
        arguments: dict,
        runner,
        silent_approval: bool,
    ) -> str | None:
        # Only get_current_time flows through here; screen captures use screen_gate.
        await self._send_json(
            {
                "type": "tool_use",
                "name": tool_name,
                "arguments": arguments,
            }
        )

        try:
            result = await runner()
        except Exception as e:
            await self._emit_tool_result(tool_name, f"Error: {e}", True)
            return None

        await self._emit_tool_result(tool_name, result, False)
        return result

    async def _emit_tool_result(
        self, name: str, result: str, is_error: bool, denied: bool = False
    ) -> None:
        await self._send_json(
            {
                "type": "tool_result",
                "name": name,
                "result": result,
                "is_error": is_error,
                "denied": denied,
            }
        )

    # ── Trigger callback (registered with ProactiveWatcher) ─────────

    async def _on_trigger(self, trigger_type: str) -> None:
        char_id = self._char_id_getter()
        if not char_id:
            print("[ProactiveSession] no character_id known, skipping", flush=True)
            return

        print(
            f"[ProactiveSession] triggering type={trigger_type} char={char_id} "
            f"conv={self._conversation_id}",
            flush=True,
        )

        from database import async_session

        try:
            async with async_session() as session:
                sent_anything = False
                proactive_context = await self._build_proactive_context()

                if trigger_type == "scheduled":
                    proactive_hint = (
                        "到了平常会来找你聊天的时间点。像平时一样自然地出现，打个招呼、"
                        "关心一下用户现在在做什么，不用提时间安排、不用提工具。"
                    )
                else:
                    proactive_hint = (
                        "有一阵子没说话了。看看现在几点了，如果有屏幕画面也看看用户在干嘛——"
                        "然后随性地发起一个话题。可以分享心情、问问近况、吐槽点什么，"
                        "或者看到用户在做什么就顺着聊下去。自然就好，不用提工具。"
                    )

                async for event in self._agent.run(
                    session=session,
                    user_message=None,
                    conversation_id=self._conversation_id,
                    character_id=char_id,
                    approval_callback=self._approval_callback,
                    proactive_hint=proactive_hint,
                    force_tool_context=proactive_context,
                    suppress_tool_calls=True,
                ):
                    if event.get("type") == "proactive_skip":
                        print("[ProactiveSession] model returned __SKIP__, not sending", flush=True)
                        return
                    tagged = {**event, "proactive": True}
                    await self._send_json(tagged)
                    sent_anything = True

                print(f"[ProactiveSession] complete (sent={sent_anything})", flush=True)
                if sent_anything:
                    self.reset_idle()
        except Exception as e:
            import traceback

            print(f"[ProactiveSession] EXCEPTION: {e}\n{traceback.format_exc()}", flush=True)
