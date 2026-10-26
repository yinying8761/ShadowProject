"""
Tests for McpManager — MCP client connection, tool registration, dispatch,
result formatting, and graceful degradation.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tool_registry import ToolRegistry
from services import mcp_manager as mcp_manager_module
from services.mcp_manager import (
    McpManager,
    mcp_tool_name,
    _format_mcp_result,
)
from mcp.types import CallToolResult, TextContent


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> ToolRegistry:
    """Fresh isolated ToolRegistry for each test."""
    return ToolRegistry()


@pytest.fixture
def manager(registry: ToolRegistry) -> McpManager:
    """McpManager with a fresh isolated registry."""
    return McpManager(registry)


def _make_text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    """Helper: build a CallToolResult with a single TextContent block."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        isError=is_error,
    )


def _make_mock_session(tools: list[dict]) -> AsyncMock:
    """Create a mock ClientSession that returns *tools* from list_tools."""
    from mcp.types import Tool

    session = AsyncMock()
    mcp_tools = [
        Tool(
            name=t["name"],
            description=t.get("description", ""),
            inputSchema=t.get("inputSchema", {"type": "object", "properties": {}}),
        )
        for t in tools
    ]
    session.list_tools = AsyncMock(return_value=MagicMock(tools=mcp_tools))
    session.call_tool = AsyncMock(
        return_value=_make_text_result("ok")
    )
    session.initialize = AsyncMock()
    return session


# ── Naming ─────────────────────────────────────────────────────────────────


class TestToolName:
    def test_basic(self):
        assert mcp_tool_name("gh", "issue") == "mcp__gh__issue"

    def test_underscores_in_server_name(self):
        assert mcp_tool_name("my_server", "do_thing") == "mcp__my_server__do_thing"


# ── Result formatting ──────────────────────────────────────────────────────


class TestFormatMcpResult:
    def test_single_text_block(self):
        result = _make_text_result("hello")
        assert _format_mcp_result(result) == "hello"

    def test_multiple_text_blocks_joined(self):
        result = CallToolResult(
            content=[
                TextContent(type="text", text="line1"),
                TextContent(type="text", text="line2"),
            ],
            isError=False,
        )
        assert _format_mcp_result(result) == "line1\nline2"

    def test_error_result_wraps_in_json(self):
        result = _make_text_result("something broke", is_error=True)
        formatted = _format_mcp_result(result)
        data = json.loads(formatted)
        assert data["error"] == "something broke"

    def test_empty_content(self):
        result = CallToolResult(content=[], isError=False)
        assert _format_mcp_result(result) == ""


# ── connect_all — config parsing ───────────────────────────────────────────


class TestReconnectAll:
    @pytest.mark.asyncio
    async def test_missing_config_is_a_no_op(self, manager: McpManager, tmp_path):
        result = await manager.reconnect_all(tmp_path / "nope.json")

        assert result == {"connected": 0, "failed": 0, "tools": 0}

    @pytest.mark.asyncio
    async def test_dead_servers_are_revived(self, manager: McpManager, tmp_path):
        manager._connections["gh"] = {"session": None, "tool_names": [], "cfg": {}}
        manager._ping = AsyncMock(return_value=False)
        manager._reconnect = AsyncMock(return_value=True)

        await manager.reconnect_all(tmp_path / "nope.json")

        manager._reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_healthy_servers_are_not_dropped(self, manager: McpManager, tmp_path):
        """A reconnect must never disconnect: healthy servers stay connected."""
        manager._connections["gh"] = {"session": None, "tool_names": [], "cfg": {}}
        manager._ping = AsyncMock(return_value=True)
        manager.disconnect_all = AsyncMock()

        config = tmp_path / "mcp.json"
        config.write_text(
            json.dumps({"servers": [{"name": "gh", "command": "x"}]}), encoding="utf-8"
        )

        await manager.reconnect_all(config)

        manager.disconnect_all.assert_not_called()
        assert manager.connected_servers == ["gh"]


class TestConnectAllConfig:
    @pytest.mark.asyncio
    async def test_no_config_file(self, manager: McpManager):
        result = await manager.connect_all("nonexistent.json")
        assert result == {"connected": 0, "failed": 0, "tools": 0}

    @pytest.mark.asyncio
    async def test_empty_servers(self, manager: McpManager, tmp_path: Path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"servers": []}))
        result = await manager.connect_all(str(cfg))
        assert result == {"connected": 0, "failed": 0, "tools": 0}

    @pytest.mark.asyncio
    async def test_bad_json(self, manager: McpManager, tmp_path: Path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{not valid json")
        result = await manager.connect_all(str(cfg))
        assert result == {"connected": 0, "failed": 0, "tools": 0}

    @pytest.mark.asyncio
    async def test_skips_unnamed_server(self, manager: McpManager, tmp_path: Path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"servers": [{"transport": "stdio", "command": "echo"}]}))
        result = await manager.connect_all(str(cfg))
        assert result["failed"] == 1
        assert result["connected"] == 0


# ── Tool registration via mocked MCP session ───────────────────────────────


class TestRegisterMcpTools:
    @pytest.mark.asyncio
    async def test_registers_tools_with_prefix(
        self, registry: ToolRegistry, manager: McpManager
    ):
        """Full flow: mock the transport + session, verify tools appear in registry."""
        session = _make_mock_session([
            {"name": "read", "description": "Read a file"},
            {"name": "write", "description": "Write a file"},
        ])

        # Patch the internal _connect_stdio directly
        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            result = await manager.connect_all(
                _write_temp_config(
                    {
                        "servers": [
                            {
                                "name": "fs",
                                "transport": "stdio",
                                "command": "cat",
                            }
                        ]
                    }
                )
            )

        assert result["connected"] == 1
        assert result["tools"] == 2

        # Check registry
        defs = {d["name"]: d for d in registry.get_tool_definitions()}
        assert "mcp__fs__read" in defs
        assert "mcp__fs__write" in defs
        assert defs["mcp__fs__read"]["description"] == "[MCP:fs] Read a file"
        assert registry.needs_approval("mcp__fs__read") is True

    @pytest.mark.asyncio
    async def test_tool_without_description(self, registry: ToolRegistry, manager: McpManager):
        session = _make_mock_session([{"name": "bare"}])

        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config(
                    {"servers": [{"name": "x", "transport": "stdio", "command": "echo"}]}
                )
            )

        defs = {d["name"]: d for d in registry.get_tool_definitions()}
        assert "mcp__x__bare" in defs

    @pytest.mark.asyncio
    async def test_tool_with_minimal_schema(self, registry: ToolRegistry, manager: McpManager):
        # MCP spec requires inputSchema to be a valid JSON Schema object
        # (never None).  Verify a minimal schema passes through correctly.
        session = _make_mock_session(
            [{"name": "minimal", "inputSchema": {"type": "object", "properties": {}, "required": []}}]
        )

        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config(
                    {"servers": [{"name": "x", "transport": "stdio", "command": "echo"}]}
                )
            )

        defs = {d["name"]: d for d in registry.get_tool_definitions()}
        schema = defs["mcp__x__minimal"]["input_schema"]
        assert schema["type"] == "object"


# ── Tool dispatch (call_tool forwarding) ────────────────────────────────────


class TestDispatchMcpTool:
    @pytest.mark.asyncio
    async def test_dispatches_to_mcp_session(self, registry: ToolRegistry, manager: McpManager):
        session = _make_mock_session([{"name": "greet"}])
        session.call_tool = AsyncMock(
            return_value=_make_text_result("hello from mcp")
        )

        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config(
                    {"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]}
                )
            )

        result = await registry.dispatch("mcp__srv__greet", {"name": "world"})
        assert result == "hello from mcp"
        session.call_tool.assert_called_once_with("greet", {"name": "world"})

    @pytest.mark.asyncio
    async def test_error_result_returns_json_error(self, registry: ToolRegistry, manager: McpManager):
        session = _make_mock_session([{"name": "failer"}])
        session.call_tool = AsyncMock(
            return_value=_make_text_result("kaputt", is_error=True)
        )

        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config(
                    {"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]}
                )
            )

        result_str = await registry.dispatch("mcp__srv__failer", {})
        data = json.loads(result_str)
        assert data["error"] == "kaputt"


# ── disconnect_all cleanup ─────────────────────────────────────────────────


class TestDisconnectAll:
    @pytest.mark.asyncio
    async def test_unregisters_mcp_tools(self, registry: ToolRegistry, manager: McpManager):
        session = _make_mock_session([{"name": "t1"}, {"name": "t2"}])

        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config(
                    {"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]}
                )
            )

        assert "mcp__srv__t1" in {d["name"] for d in registry.get_tool_definitions()}

        await manager.disconnect_all()

        assert manager.connected_servers == []
        # After disconnect, MCP tools should be gone from registry
        remaining = {d["name"] for d in registry.get_tool_definitions()}
        assert "mcp__srv__t1" not in remaining
        assert "mcp__srv__t2" not in remaining


# ── Health check & reconnect ────────────────────────────────────────────────


class TestHealthCheck:
    """Periodic health check: ping, reconnect, reset breakers (ticket #19)."""

    @pytest.mark.asyncio
    async def test_healthy_server_no_reconnect(self, registry, manager):
        session = _make_mock_session([{"name": "t1"}])
        calls = {"n": 0}

        async def fake_connect(cfg):
            calls["n"] += 1
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config({"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]})
            )

        assert calls["n"] == 1
        results = await manager.health_check()
        assert results == {"srv": True}
        assert calls["n"] == 1  # no reconnect happened

    @pytest.mark.asyncio
    async def test_unhealthy_server_reconnects(self, registry, manager):
        session1 = _make_mock_session([{"name": "t1"}])
        session2 = _make_mock_session([{"name": "t1"}])
        sessions = [session1, session2]
        calls = {"n": 0}

        async def fake_connect(cfg):
            s = sessions[calls["n"]]
            calls["n"] += 1
            return MagicMock(), MagicMock(), s

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config({"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]})
            )
            session1.list_tools.side_effect = Exception("connection lost")
            results = await manager.health_check()

        assert results == {"srv": True}
        assert calls["n"] == 2  # reconnected once
        assert "mcp__srv__t1" in {d["name"] for d in registry.get_tool_definitions()}

    @pytest.mark.asyncio
    async def test_single_server_isolation(self, registry, manager):
        s1 = _make_mock_session([{"name": "a"}])
        s2 = _make_mock_session([{"name": "b"}])
        s2_new = _make_mock_session([{"name": "b"}])
        srv2_calls = {"n": 0}

        async def fake_connect(cfg):
            name = cfg["name"]
            if name == "srv1":
                return MagicMock(), MagicMock(), s1
            s = [s2, s2_new][srv2_calls["n"]]
            srv2_calls["n"] += 1
            return MagicMock(), MagicMock(), s

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config({
                    "servers": [
                        {"name": "srv1", "transport": "stdio", "command": "echo"},
                        {"name": "srv2", "transport": "stdio", "command": "echo"},
                    ],
                })
            )
            s2.list_tools.side_effect = Exception("down")
            results = await manager.health_check()

        assert results == {"srv1": True, "srv2": True}
        names = {d["name"] for d in registry.get_tool_definitions()}
        assert "mcp__srv1__a" in names
        assert "mcp__srv2__b" in names

    @pytest.mark.asyncio
    async def test_reconnect_resets_breakers(self):
        class RecordingRegistry:
            def __init__(self):
                self.reset_calls = []

            def register(self, name, description, parameters, handler, require_approval=False, **kwargs):
                pass

            def unregister(self, name):
                pass

            def reset_breaker(self, name):
                self.reset_calls.append(name)

        reg = RecordingRegistry()
        manager = McpManager(reg)
        session1 = _make_mock_session([{"name": "t1"}])
        session2 = _make_mock_session([{"name": "t1"}])
        sessions = [session1, session2]
        calls = {"n": 0}

        async def fake_connect(cfg):
            s = sessions[calls["n"]]
            calls["n"] += 1
            return MagicMock(), MagicMock(), s

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config({"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]})
            )
            session1.list_tools.side_effect = Exception("down")
            await manager.health_check()

        assert "mcp__srv__t1" in reg.reset_calls


# ── MCP tools through a CB-enabled ToolRuntime ───────────────────────────────


class TestMcpCircuitBreakerIntegration:
    """MCP tools are protected by their own per-tool CircuitBreaker (ticket #19, reuses E3)."""

    @pytest.mark.asyncio
    async def test_mcp_tool_failures_trip_circuit_breaker(self):
        from core.circuit_breaker import OPEN
        from core.tool_runtime import ToolRuntime

        # Production wiring: McpManager is given the CB-enabled ToolRuntime,
        # so MCP tools flow through the same dispatch() that consults breakers.
        rt = ToolRuntime(
            registry=ToolRegistry(),
            enable_tracing=False,
            enable_sandbox=False,
            enable_circuit_breaker=True,
            circuit_threshold=2,
        )
        manager = McpManager(rt)

        session = _make_mock_session([{"name": "boom"}])
        session.call_tool.return_value = _make_text_result("kaputt", is_error=True)

        async def fake_connect(cfg):
            return MagicMock(), MagicMock(), session

        with patch.object(manager, "_connect_stdio", fake_connect):
            await manager.connect_all(
                _write_temp_config({"servers": [{"name": "srv", "transport": "stdio", "command": "echo"}]})
            )

        # Two consecutive failures open the breaker.
        await rt.dispatch("mcp__srv__boom", {})
        await rt.dispatch("mcp__srv__boom", {})
        assert rt._circuit_breakers["mcp__srv__boom"].state == OPEN

        # Third call is short-circuited — call_tool is NOT invoked again.
        calls_before = session.call_tool.await_count
        result = await rt.dispatch("mcp__srv__boom", {})
        assert session.call_tool.await_count == calls_before
        assert "Circuit breaker open" in json.loads(result)["error"]


# ── Failure isolation — one dead server must not kill the others ────────────


def _hang_forever_session() -> AsyncMock:
    """A session whose handshake never returns (process started, never answers)."""
    session = _make_mock_session([{"name": "never"}])

    async def _never(*args, **kwargs):
        await asyncio.sleep(3600)

    session.initialize = _never
    return session


_MINIMAL_MCP_SERVER = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("itest")


@mcp.tool()
def ping() -> str:
    """Ping."""
    return "pong"


mcp.run()
'''


class TestFailureIsolation:
    """Regression for the 2026-10-19 MCP outage.

    A server that crashed on import left the caller's anyio state dirty, so
    every *later* connect in the same task failed too — one broken server took
    the whole MCP feature down (the app logged ``0 connected, 2 failed``).
    Each server now owns its own task, and the connect timeout is an anyio
    scope inside it, so nothing is cancelled from the outside.
    """

    @pytest.mark.asyncio
    async def test_hung_server_times_out_without_harming_its_sibling(
        self, registry: ToolRegistry, manager: McpManager, monkeypatch
    ):
        monkeypatch.setattr(mcp_manager_module, "CONNECT_TIMEOUT_SEC", 0.05)
        good = _make_mock_session([{"name": "ok"}])

        async def fake_connect(cfg):
            if cfg["name"] == "hung":
                return MagicMock(), MagicMock(), _hang_forever_session()
            return MagicMock(), MagicMock(), good

        with patch.object(manager, "_connect_stdio", fake_connect):
            result = await manager.connect_all(
                _write_temp_config({
                    "servers": [
                        {"name": "hung", "transport": "stdio", "command": "echo"},
                        {"name": "good", "transport": "stdio", "command": "echo"},
                    ],
                })
            )

        assert result == {"connected": 1, "failed": 1, "tools": 1}
        assert manager.connected_servers == ["good"]

        names = {d["name"] for d in registry.get_tool_definitions()}
        assert "mcp__good__ok" in names
        assert "mcp__hung__never" not in names  # a timed-out server registers nothing

        await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_connect_after_a_timeout_still_works(
        self, registry: ToolRegistry, manager: McpManager, monkeypatch
    ):
        """The connect *after* a failure must not inherit the failure."""
        monkeypatch.setattr(mcp_manager_module, "CONNECT_TIMEOUT_SEC", 0.05)

        async def hung_connect(cfg):
            return MagicMock(), MagicMock(), _hang_forever_session()

        with patch.object(manager, "_connect_stdio", hung_connect):
            first = await manager.connect_all(
                _write_temp_config({
                    "servers": [{"name": "hung", "transport": "stdio", "command": "echo"}]
                })
            )
        assert first == {"connected": 0, "failed": 1, "tools": 0}

        good = _make_mock_session([{"name": "ok"}])

        async def good_connect(cfg):
            return MagicMock(), MagicMock(), good

        with patch.object(manager, "_connect_stdio", good_connect):
            second = await manager.connect_all(
                _write_temp_config({
                    "servers": [{"name": "good", "transport": "stdio", "command": "echo"}]
                })
            )
        assert second == {"connected": 1, "failed": 0, "tools": 1}
        assert "mcp__good__ok" in {d["name"] for d in registry.get_tool_definitions()}

        await manager.disconnect_all()


class TestRealServerIsolation:
    """The same isolation, with real subprocesses.

    The fakes above cannot see the anyio cancel-scope corruption that actually
    caused the outage (there is no task group in a fake), so this one spawns a
    real server next to a real crashing one.
    """

    @pytest.mark.asyncio
    async def test_dead_process_does_not_kill_a_real_server(
        self, registry: ToolRegistry, manager: McpManager, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(mcp_manager_module, "CONNECT_TIMEOUT_SEC", 3.0)
        server_py = tmp_path / "minimal_server.py"
        server_py.write_text(_MINIMAL_MCP_SERVER, encoding="utf-8")

        result = await manager.connect_all(
            _write_temp_config({
                "servers": [
                    {
                        # Starts and exits immediately — the shape that used to
                        # poison every server after it.
                        "name": "dead",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": ["-c", "import sys; sys.exit(1)"],
                    },
                    {
                        "name": "real",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(server_py)],
                    },
                ]
            })
        )

        assert result == {"connected": 1, "failed": 1, "tools": 1}
        assert manager.connected_servers == ["real"]
        assert "mcp__real__ping" in {d["name"] for d in registry.get_tool_definitions()}

        # End-to-end over stdio: the surviving server is actually callable.
        assert await registry.dispatch("mcp__real__ping", {}) == "pong"

        await manager.disconnect_all()
        assert manager.connected_servers == []


# ── Helpers ────────────────────────────────────────────────────────────────


_temp_counter = 0


def _write_temp_config(data: dict) -> str:
    """Write *data* as JSON to a temp file, return its path."""
    global _temp_counter
    _temp_counter += 1
    p = Path(tempfile.gettempdir()) / f"mcp_test_{_temp_counter}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)
