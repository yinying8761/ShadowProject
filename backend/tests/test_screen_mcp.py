"""
Tests for Ticket 02 — see_screen → MCP Vision.

Covers Seam 2: MCP vision tool registration + fallback path.
"""

import asyncio
import json

import pytest

import tools.screen_tools as st_module  # noqa: E402
from tools.screen_tools import (
    _try_mcp_vision,
    set_mcp_dispatch,
    VISION_MCP_TOOL,
)


# ── Fake MCP dispatch ────────────────────────────────────────────────────────


class FakeMcpDispatch:
    """Simulates a ToolRuntime dispatch for MCP vision calls."""

    def __init__(self, response: str | None = None):
        self._response = response
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict) -> str:
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        if self._response is not None:
            return self._response
        # Default: return a valid-looking JSON description
        return json.dumps({
            "description": "MCP says: 屏幕上显示了一个代码编辑器。",
        }, ensure_ascii=False)


class FailingMcpDispatch(FakeMcpDispatch):
    """Simulates an MCP dispatch that fails."""

    async def __call__(self, tool_name: str, arguments: dict) -> str:
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        raise RuntimeError("MCP connection lost")


class UnknownToolDispatch(FakeMcpDispatch):
    """Simulates MCP dispatch returning 'unknown tool' error."""

    async def __call__(self, tool_name: str, arguments: dict) -> str:
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)


# ── Tests for _try_mcp_vision ────────────────────────────────────────────────


class TestMcpVision:
    """_try_mcp_vision — MCP dispatch integration."""

    def setup_method(self):
        """Ensure _mcp_dispatch is cleared before each test."""
        set_mcp_dispatch(None)

    def teardown_method(self):
        set_mcp_dispatch(None)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_dispatch_set(self):
        """When _mcp_dispatch is None, _try_mcp_vision returns None."""
        result = await _try_mcp_vision(b"fake-image", "focus")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_description_when_mcp_works(self):
        """When MCP returns valid JSON with 'description', it is extracted."""
        dispatch = FakeMcpDispatch()
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", "描述一下屏幕")
        assert result is not None
        assert "MCP says" in result
        assert len(dispatch.calls) == 1
        assert dispatch.calls[0]["tool_name"] == VISION_MCP_TOOL
        # The vision server's schema wants `image` (a path/URL/base64 string).
        assert dispatch.calls[0]["arguments"]["image"].startswith("data:image/jpeg;base64,")

    @pytest.mark.asyncio
    async def test_returns_none_on_dispatch_error(self):
        """When MCP dispatch raises, _try_mcp_vision returns None."""
        dispatch = FailingMcpDispatch()
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", None)
        assert result is None
        assert len(dispatch.calls) == 1  # call was attempted

    @pytest.mark.asyncio
    async def test_returns_none_on_unknown_tool(self):
        """When MCP returns 'unknown tool' error, returns None."""
        dispatch = UnknownToolDispatch()
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_failure_envelope_is_not_a_description(self):
        """The real server shape: a failure wrapped in an ordinary tool result.

        `deepseek_vision_mcp` answers {"success": false, "result": "<429 text>",
        "error": "VISION_API_ERROR"}. Treating `result` as a description is what
        put a 429 stack into a user message and made the character recite it
        (2026-10-22).
        """
        dispatch = FakeMcpDispatch(json.dumps({
            "success": False,
            "result": (
                "视觉服务链全部失败（已请求 2/4 次）：primary: Error code: 429 - "
                "{'error': {'code': '1305', 'message': '该模型当前访问量过大'}}"
            ),
            "model": None,
            "usage": {},
            "cached": False,
            "provider": None,
            "fallback_used": False,
            "attempts": 0,
            "error": "VISION_API_ERROR",
        }, ensure_ascii=False))
        set_mcp_dispatch(dispatch)

        assert await _try_mcp_vision(b"fake-image", None) is None
        assert "429" in (st_module._last_vision_error or "")

        set_mcp_dispatch(None)

    @pytest.mark.asyncio
    async def test_success_envelope_is_returned(self):
        """The same envelope with success=true does carry the description."""
        dispatch = FakeMcpDispatch(json.dumps({
            "success": True,
            "result": "屏幕上是代码编辑器，正在编辑 screen_tools.py",
            "error": None,
        }, ensure_ascii=False))
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", None)

        assert result == "屏幕上是代码编辑器，正在编辑 screen_tools.py"

        set_mcp_dispatch(None)

    @pytest.mark.asyncio
    async def test_fallback_from_unknown_tool_json(self):
        """When MCP returns JSON with 'result' field, it is extracted."""
        dispatch = FakeMcpDispatch(
            json.dumps({"result": "屏幕内容描述"}, ensure_ascii=False)
        )
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", None)
        assert result == "屏幕内容描述"

    @pytest.mark.asyncio
    async def test_fallback_from_text_field(self):
        """When MCP returns JSON with 'text' field, it is extracted."""
        dispatch = FakeMcpDispatch(
            json.dumps({"text": "屏幕上有编辑器"}, ensure_ascii=False)
        )
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", None)
        assert result == "屏幕上有编辑器"

    @pytest.mark.asyncio
    async def test_plain_text_result_used_as_is(self):
        """Non-JSON result is used directly if long enough."""
        dispatch = FakeMcpDispatch(
            "这是一个很长的非JSON描述字符串，描述了屏幕内容"
        )
        set_mcp_dispatch(dispatch)

        result = await _try_mcp_vision(b"fake-image", None)
        assert result is not None
        assert "描述" in result

    @pytest.mark.asyncio
    async def test_base64_includes_image_data(self):
        """The image bytes are base64-encoded in the MCP call arguments."""
        dispatch = FakeMcpDispatch()
        set_mcp_dispatch(dispatch)

        await _try_mcp_vision(b"hello-world-bytes", "focus here")

        args = dispatch.calls[0]["arguments"]
        # The vision server's schema takes an image *string* (path/URL/base64),
        # not a dict of bytes: the argument key is `image`.
        assert args["image"].startswith("data:image/jpeg;base64,")
        assert args["prompt"] == "focus here"
        # Verify it's valid base64
        import base64
        decoded = base64.b64decode(args["image"].split(",", 1)[1])
        assert decoded == b"hello-world-bytes"


# ── Tests for see_screen integration ─────────────────────────────────────────


class TestSeeScreenMcpIntegration:
    """see_screen uses MCP when available, falls back otherwise."""

    def setup_method(self):
        set_mcp_dispatch(None)

    def teardown_method(self):
        set_mcp_dispatch(None)

    @pytest.mark.asyncio
    async def test_mcp_receives_image_data(self):
        """When MCP is available, see_screen passes base64 image data."""
        from tools.screen_tools import see_screen

        dispatch = FakeMcpDispatch()
        set_mcp_dispatch(dispatch)

        # Need to mock _capture_sync to avoid actual screenshot
        import tools.screen_tools as st
        original = st._capture_sync
        try:
            st._capture_sync = lambda monitor: (b"test-image", 100, 200)
            result = await see_screen(focus="用户在看什么", monitor=0)
        finally:
            st._capture_sync = original

        data = json.loads(result)
        assert data["description"] is not None
        assert data["vision_backend"] == "mcp"
        assert data["image_size"] == "100x200"

        # Verify MCP was called with base64 image
        assert len(dispatch.calls) == 1
        import base64
        image_arg = dispatch.calls[0]["arguments"]["image"]
        decoded = base64.b64decode(image_arg.split(",", 1)[1])
        assert decoded == b"test-image"

    @pytest.mark.asyncio
    async def test_slow_vision_call_times_out_without_cancelling(self, monkeypatch):
        """A hung vision call reports an error; it is abandoned, not cancelled.

        2026-10-20: the 15s sandbox timeout cancelled a hung ``call_tool`` and
        the backend went away. ``see_screen`` now keeps its own shorter
        deadline, and it waits with ``asyncio.wait`` so the SDK call is never
        cancelled from outside.
        """
        from tools.screen_tools import see_screen
        import tools.screen_tools as st
        from services.vision_service import vision_service as vs

        monkeypatch.setattr(st, "VISION_TIMEOUT_SEC", 0.05)

        async def slow_dispatch(tool_name, arguments):
            await asyncio.sleep(0.3)  # longer than the deadline
            return json.dumps({"description": "too late"})

        set_mcp_dispatch(slow_dispatch)

        original_capture, original_describe = st._capture_sync, vs.describe_image
        legacy_calls = {"n": 0}

        async def fake_describe(image_bytes, mime_type="", focus=None, max_tokens=400):
            legacy_calls["n"] += 1
            return "legacy描述"

        try:
            st._capture_sync = lambda monitor: (b"test-image", 100, 200)
            vs.describe_image = fake_describe
            data = json.loads(await see_screen(monitor=0))
        finally:
            st._capture_sync, vs.describe_image = original_capture, original_describe
            set_mcp_dispatch(None)

        assert "error" in data
        assert "没有响应" in data["error"]
        assert legacy_calls["n"] == 0
        # Let the abandoned call finish so nothing is left pending.
        await asyncio.sleep(0.35)

    @pytest.mark.asyncio
    async def test_provider_error_is_not_passed_off_as_a_description(self):
        """A 429 that arrives as plain text must not become "the screen content".

        2026-10-20: the Zhipu free vision model answered 429, the vision server
        returned that complaint as an ordinary tool result, and the old
        "non-JSON → use as-is" branch handed it to the agent as a description.
        """
        from tools.screen_tools import see_screen
        import tools.screen_tools as st
        from services.vision_service import vision_service as vs

        dispatch = FakeMcpDispatch(
            "视觉服务链全部失败（已请求 2/4 次）：primary: Error code: 429 - "
            "{'error': {'code': '1305', 'message': '该模型当前访问量过大'}}"
        )
        set_mcp_dispatch(dispatch)

        original_capture, original_describe = st._capture_sync, vs.describe_image
        legacy_calls = {"n": 0}

        async def fake_describe(image_bytes, mime_type="", focus=None, max_tokens=400):
            legacy_calls["n"] += 1
            return "legacy描述"

        try:
            st._capture_sync = lambda monitor: (b"test-image", 100, 200)
            vs.describe_image = fake_describe
            data = json.loads(await see_screen(focus="屏幕上有报错吗", monitor=0))
        finally:
            st._capture_sync, vs.describe_image = original_capture, original_describe

        assert "description" not in data
        assert "error" in data
        assert "429" in data["error"]
        assert legacy_calls["n"] == 0, "MCP 接入后不该回退到已停用的旧视觉模型"

    @pytest.mark.asyncio
    async def test_mcp_failure_does_not_fall_back_to_legacy(self):
        """Wired MCP + a failing call => explicit error, the old model untouched."""
        from tools.screen_tools import see_screen
        import tools.screen_tools as st
        from services.vision_service import vision_service as vs

        set_mcp_dispatch(FailingMcpDispatch())

        original_capture, original_describe = st._capture_sync, vs.describe_image
        legacy_calls = {"n": 0}

        async def fake_describe(image_bytes, mime_type="", focus=None, max_tokens=400):
            legacy_calls["n"] += 1
            return "legacy描述"

        try:
            st._capture_sync = lambda monitor: (b"test-image", 100, 200)
            vs.describe_image = fake_describe
            data = json.loads(await see_screen(monitor=0))
        finally:
            st._capture_sync, vs.describe_image = original_capture, original_describe

        assert "error" in data
        assert "MCP connection lost" in data["error"]
        assert legacy_calls["n"] == 0

    @pytest.mark.asyncio
    async def test_fallback_when_mcp_unavailable(self):
        """When MCP is not configured, see_screen falls back to legacy."""
        from tools.screen_tools import see_screen

        set_mcp_dispatch(None)

        # Mock both _capture_sync and vision_service
        import tools.screen_tools as st

        original_capture = st._capture_sync
        try:
            st._capture_sync = lambda monitor: (b"test-image", 100, 200)

            # Patch vision_service to return a known value
            from services.vision_service import vision_service as vs
            original_describe = vs.describe_image
            async def fake_describe(image_bytes, mime_type="", focus=None, max_tokens=400):
                return "legacy描述"
            vs.describe_image = fake_describe

            try:
                result = await see_screen(focus=None, monitor=0)
            finally:
                vs.describe_image = original_describe
        finally:
            st._capture_sync = original_capture

        data = json.loads(result)
        assert data["vision_backend"] == "legacy"
        assert data["description"] == "legacy描述"

    @pytest.mark.asyncio
    async def test_capture_failure_returns_error(self):
        """When screenshot capture fails, error is returned immediately."""
        from tools.screen_tools import see_screen
        import tools.screen_tools as st

        original = st._capture_sync
        try:
            def fail_capture(monitor):
                raise RuntimeError("no screen")
            st._capture_sync = fail_capture

            result = await see_screen(monitor=0)
        finally:
            st._capture_sync = original

        data = json.loads(result)
        assert "error" in data
        assert "no screen" in data["error"]


# ── ScreenCaptureGate: an error result is never a description ───────────────


class TestScreenCaptureGate:
    """The gate is what turns a vision result into the chat context.

    2026-10-20: a 429 stack went through this seam as a "description", landed in
    a *user* message, and the character then recited it for hours. An error
    result must come back as None (→ a neutral note in the turn), not as text.
    """

    @pytest.mark.asyncio
    async def test_error_result_is_not_returned_as_a_description(self, monkeypatch):
        from services import screen_capture_gate as gate_mod

        sent: list[dict] = []

        async def send_json(payload):
            sent.append(payload)

        async def approval_callback(tool_name, arguments):
            return True

        async def get_user_config():
            return None

        async def fake_see_screen(focus=None, monitor=0):
            return json.dumps(
                {"error": "视觉服务暂时不可用（Error code: 429）"}, ensure_ascii=False
            )

        monkeypatch.setattr(gate_mod, "see_screen", fake_see_screen)
        monkeypatch.setattr(gate_mod, "_capture_dhash_sync", lambda: "hash-1")

        gate = gate_mod.ScreenCaptureGate(
            send_json=send_json,
            approval_callback=approval_callback,
            get_user_config=get_user_config,
            fingerprints=gate_mod.ScreenFingerprintStore(maxlen=3),
        )

        assert await gate.try_capture() is None

        results = [p for p in sent if p.get("type") == "tool_result"]
        assert results, "the tool call should still be reported to the UI"
        assert results[-1]["is_error"] is True

    @pytest.mark.asyncio
    async def test_success_result_is_returned(self, monkeypatch):
        from services import screen_capture_gate as gate_mod

        async def send_json(payload):
            return None

        async def approval_callback(tool_name, arguments):
            return True

        async def get_user_config():
            return None

        async def fake_see_screen(focus=None, monitor=0):
            return json.dumps({"description": "屏幕上是代码编辑器"}, ensure_ascii=False)

        monkeypatch.setattr(gate_mod, "see_screen", fake_see_screen)
        monkeypatch.setattr(gate_mod, "_capture_dhash_sync", lambda: "hash-2")

        gate = gate_mod.ScreenCaptureGate(
            send_json=send_json,
            approval_callback=approval_callback,
            get_user_config=get_user_config,
            fingerprints=gate_mod.ScreenFingerprintStore(maxlen=3),
        )

        assert await gate.try_capture() == "屏幕上是代码编辑器"


# ── Tests for set_mcp_dispatch ───────────────────────────────────────────────


class TestSetMcpDispatch:
    """set_mcp_dispatch wires the module-level dispatch reference."""

    def setup_method(self):
        set_mcp_dispatch(None)

    def teardown_method(self):
        set_mcp_dispatch(None)

    def test_sets_dispatch(self):
        assert st_module._mcp_dispatch is None
        dispatch = FakeMcpDispatch()
        set_mcp_dispatch(dispatch)
        assert st_module._mcp_dispatch is not None

    def test_clear_dispatch(self):
        dispatch = FakeMcpDispatch()
        set_mcp_dispatch(dispatch)
        set_mcp_dispatch(None)
        assert st_module._mcp_dispatch is None
