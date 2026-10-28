"""
Screen capture gate — encapsulates the screen-capture flow end-to-end.

Handles: intent detection, approval gating, screenshot execution, fingerprint
dedup, JSON result parsing, and event emission via callbacks.  No direct
WebSocket dependency.

Two entry points, one approval path:

- ``try_capture()`` — legacy **specialist** path: capture → vision model /
  MCP writes a description → return that text (used when the main model cannot
  see, and by the proactive watcher).
- ``capture_images()`` — **main-model** path (ticket #02): capture and return
  the JPEG bytes so they can ride on this turn's request. No vision model, no
  dedup (a user asking to look twice really looks twice).
"""

import asyncio
import json

from tools.screen_tools import (
    ScreenFingerprintStore,
    _capture_dhash_sync,
    _capture_sync,
    dhash_from_jpeg,
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

    # ── 共用的前后两步：审批 / 通知前端（两条路一模一样，只此一份） ──────

    async def _approve(
        self, focus: str | None, silent_approval: bool
    ) -> bool:
        """用户审批（主动陪伴可静默）。拒绝时顺手给前端一个 denied 结果。"""
        cfg = await self._get_user_config()
        effective_silent = silent_approval or (
            cfg.proactive_silent_tool_approval if cfg else False
        )
        if effective_silent:
            return True

        approved = await self._approval_callback(
            "see_screen", {"focus": focus or ""}
        )
        if not approved:
            await self._emit_tool_result(
                "see_screen", "User denied this operation.", False, denied=True
            )
        return approved

    async def _announce(self, focus: str | None) -> None:
        """告诉前端"开始调工具了"（界面上转圈的起点）。"""
        await self._send_json(
            {
                "type": "tool_use",
                "name": "see_screen",
                "arguments": {"focus": focus or ""},
            }
        )

    def _remember(self, screen_hash: int) -> None:
        """记指纹 —— 只喂给主动陪伴的去重/节流，**任何**路径都不据此跳过。"""
        self._fingerprints.add(screen_hash)

    async def _remember_capture(self, image_bytes: bytes) -> None:
        """从已拍到的这一张算指纹（不为了指纹再拍第二张）。"""
        try:
            self._remember(await asyncio.to_thread(dhash_from_jpeg, image_bytes))
        except Exception:
            pass

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
        if not await self._approve(focus, silent_approval):
            return None

        await self._announce(focus)

        try:
            result_json = await see_screen(focus=focus or None)
        except Exception as e:
            await self._emit_tool_result(
                "see_screen", f"Screen capture failed: {e}", is_error=True
            )
            return None

        # 主动陪伴的去重靠"我们自己决定要看"时记下的指纹；这里只是喂它。
        try:
            self._remember(await asyncio.to_thread(_capture_dhash_sync))
        except Exception:
            pass

        try:
            parsed = json.loads(result_json)
        except json.JSONDecodeError:
            parsed = {"description": result_json}

        description = parsed.get("description") or parsed.get("error") or ""
        is_error = "error" in parsed and "description" not in parsed

        await self._emit_tool_result("see_screen", result_json, is_error)
        return description if not is_error else None

    async def capture_images(self) -> tuple[bytes, str] | None:
        """拍一张交给**主模型**看（ticket #02）：返回 `(jpeg_bytes, mime)`。

        与 `try_capture` 走同一道审批、同一套前端事件，但**不经过任何视觉模型** ——
        主模型自己看图，少一次调用、也不丢布局/颜色这些细节。

        **不去重**：用户主动说"看看"，同一屏也要真的再看一次（指纹只记下来给主动陪伴
        的去重服务用，这里绝不因为"看过"而跳过 —— 跳过了模型手里就没有图，只能编）。
        图片只在内存里走这一轮请求：不落盘、不落库。
        """
        if not await self._approve(None, silent_approval=False):
            return None

        await self._announce(None)

        try:
            image_bytes, width, height = await asyncio.to_thread(_capture_sync, 0)
        except Exception as e:
            await self._emit_tool_result(
                "see_screen", f"Screen capture failed: {e}", is_error=True
            )
            return None

        await self._remember_capture(image_bytes)
        await self._emit_tool_result(
            "see_screen", f"已附上截图 {width}x{height}", False
        )
        return image_bytes, "image/jpeg"

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
