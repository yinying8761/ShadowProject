"""
Tests for Ticket 02 — see_screen → MCP Vision.

Covers Seam 2: MCP vision tool registration + fallback path.
"""

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
        assert "image_data" in dispatch.calls[0]["arguments"]

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
        assert args["image_data"] is not None
        assert args["prompt"] == "focus here"
        # Verify it's valid base64
        import base64
        decoded = base64.b64decode(args["image_data"])
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
        decoded = base64.b64decode(dispatch.calls[0]["arguments"]["image_data"])
        assert decoded == b"test-image"

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
