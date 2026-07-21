"""
Screen capture gate — encapsulates the screen-capture flow end-to-end.

Handles: intent detection, approval gating, screenshot execution, fingerprint
dedup, JSON result parsing, and event emission via callbacks.  No direct
WebSocket dependency.
"""

import asyncio
import json

from tools.screen_tools import (
    ScreenFingerprintStore,
    _capture_dhash_sync,
    see_screen,
)

SCREEN_KEYWORDS = [
    "看看屏幕",
    "看下屏幕",
    "看一下屏幕",
    "看屏幕",
    "看看我屏幕",
    "看看我的屏幕",
    "看下我的屏幕",
    "看一下我的屏幕",
    "看我在干",
    "看我在玩",
    "看我现在",
    "你看",
    "瞄一眼",
    "扫一眼",
]


def detects_screen_intent(text: str) -> bool:
    """Return True if `text` signals the user wants a screen capture."""
    stripped = text.strip()
    return any(kw in stripped for kw in SCREEN_KEYWORDS)


class ScreenCaptureGate:
    """Gate that controls the screen-capture lifecycle for a connection."""

    def __init__(
        self,
        send_json,
        approval_callback,
        get_user_config,
        fingerprints: ScreenFingerprintStore,
    ):
        self._send_json = send_json
        self._approval_callback = approval_callback
        self._get_user_config = get_user_config
        self._fingerprints = fingerprints

    async def try_capture(
        self,
        focus: str | None = None,
        *,
        silent_approval: bool = False,
    ) -> str | None:
        """
        Attempt a screen capture, gated by user approval (unless silent).

        Returns the vision model's description string, or None if the capture
        was denied / failed.  Emits tool_use and tool_result events through
        the send_json callback.
        """
        cfg = await self._get_user_config()
        effective_silent = silent_approval or (
            cfg.proactive_silent_tool_approval if cfg else False
        )

        # ── Approval gate ──────────────────────────────────────────
        if not effective_silent:
            approved = await self._approval_callback(
                "see_screen", {"focus": focus or ""}
            )
            if not approved:
                await self._emit_tool_result(
                    "see_screen", "User denied this operation.", False, denied=True
                )
                return None

        # ── Notify frontend that tool use started ──────────────────
        await self._send_json(
            {
                "type": "tool_use",
                "name": "see_screen",
                "arguments": {"focus": focus or ""},
            }
        )

        # ── Execute capture ────────────────────────────────────────
        try:
            result_json = await see_screen(focus=focus or None)
        except Exception as e:
            await self._emit_tool_result(
                "see_screen", f"Screen capture failed: {e}", is_error=True
            )
            return None

        # ── Record fingerprint for proactive dedup ─────────────────
        try:
            screen_hash = await asyncio.to_thread(_capture_dhash_sync)
            self._fingerprints.add(screen_hash)
        except Exception:
            pass

        # ── Parse result ───────────────────────────────────────────
        try:
            parsed = json.loads(result_json)
        except json.JSONDecodeError:
            parsed = {"description": result_json}

        description = parsed.get("description") or parsed.get("error") or ""
        is_error = "error" in parsed and "description" not in parsed

        await self._emit_tool_result("see_screen", result_json, is_error)
        return description if not is_error else None

    async def _emit_tool_result(
        self,
        name: str,
        result: str,
        is_error: bool,
        denied: bool = False,
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
