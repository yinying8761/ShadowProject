"""
Tests for CommandExecutor — the debug console\'s whitelist commands (ticket 04).

Seam: CommandExecutor.run() over a real LogHub (pure, in-memory) with fake
MCP / config callbacks.  The security boundary is part of the contract: a
command outside the whitelist must not execute anything at all.
"""

import pytest

from services.command_executor import CommandExecutor
from services.log_hub import FileSink, LogHub


def cmd_lines(hub: LogHub) -> list[str]:
    """Only the [cmd] output, i.e. what the panel would show for a command."""
    return [e["message"] for e in hub.lines() if e["source"] == "cmd"]


class TestWhitelist:
    @pytest.mark.asyncio
    async def test_help_lists_every_command(self):
        hub = LogHub()

        await CommandExecutor(hub).run("help")

        assert cmd_lines(hub) == [
            "[cmd] available: clear, config reload, help, mcp reconnect, status"
        ]

    @pytest.mark.parametrize(
        "hostile",
        [
            "rm -rf /",
            "eval(\'1+1\')",
            "os.system(\'ls\')",
            "__import__(\'os\')",
            "clear; rm -rf /",
            "status && whoami",
        ],
    )
    @pytest.mark.asyncio
    async def test_an_unknown_command_reports_and_executes_nothing(self, hostile):
        hub = LogHub()
        called: list[str] = []

        async def reconnect() -> str:
            called.append("reconnect")
            return "must not run"

        def reload() -> str:
            called.append("reload")
            return "must not run"

        executor = CommandExecutor(hub, reconnect_mcp=reconnect, reload_config=reload)
        hub.add("keep me")

        await executor.run(hostile)

        assert called == []
        assert cmd_lines(hub)[0] == f"[cmd] unknown: {hostile}"
        assert cmd_lines(hub)[1].startswith("[cmd] available: ")
        assert "keep me" in [e["message"] for e in hub.lines()]   # not cleared either

    @pytest.mark.asyncio
    async def test_lookup_ignores_case_and_surrounding_space(self):
        hub = LogHub()

        await CommandExecutor(hub).run("  Status ")

        assert cmd_lines(hub)[0].startswith("[cmd] log buffer: ")


class TestCommands:
    @pytest.mark.asyncio
    async def test_clear_empties_the_buffer_and_says_so(self):
        hub = LogHub()
        hub.add("old line")

        await CommandExecutor(hub).run("clear")

        assert cmd_lines(hub) == ["[cmd] log buffer cleared"]
        assert [e["message"] for e in hub.lines()] == ["[cmd] log buffer cleared"]

    @pytest.mark.asyncio
    async def test_status_reports_hub_stats_and_process_info(self, tmp_path):
        log_file = tmp_path / "companion.log"
        hub = LogHub(ring_size=10, sink=FileSink(log_file))
        hub.add("one")
        hub.add("two")

        await CommandExecutor(hub, describe_model=lambda: "test-model (openai)").run("status")

        lines = cmd_lines(hub)
        assert lines[0] == "[cmd] log buffer: 2/10 lines, 0 subscriber(s)"
        assert lines[1] == f"[cmd] log file: {log_file}"
        assert lines[2] == "[cmd] model: test-model (openai)"
        assert "pid=" in lines[3] and "python=" in lines[3]

    @pytest.mark.asyncio
    async def test_a_broken_model_lookup_is_reported_not_crashed(self):
        hub = LogHub()

        def boom() -> str:
            raise AttributeError("get_provider")

        await CommandExecutor(hub, describe_model=boom).run("status")

        assert cmd_lines(hub)[2] == "[cmd] model: unknown (AttributeError)"

    @pytest.mark.asyncio
    async def test_mcp_reconnect_reports_the_callback_result(self):
        hub = LogHub()

        async def reconnect() -> str:
            return "mcp reconnect: 2 connected, 0 failed, 7 tools"

        await CommandExecutor(hub, reconnect_mcp=reconnect).run("mcp reconnect")

        assert cmd_lines(hub) == ["[cmd] mcp reconnect: 2 connected, 0 failed, 7 tools"]

    @pytest.mark.asyncio
    async def test_config_reload_reports_the_callback_result(self):
        hub = LogHub()

        await CommandExecutor(hub, reload_config=lambda: "config reloaded: model=x").run(
            "config reload"
        )

        assert cmd_lines(hub) == ["[cmd] config reloaded: model=x"]

    @pytest.mark.asyncio
    async def test_missing_callbacks_are_reported_not_crashed(self):
        hub = LogHub()
        executor = CommandExecutor(hub)

        await executor.run("mcp reconnect")
        await executor.run("config reload")

        assert cmd_lines(hub) == [
            "[cmd] mcp reconnect unavailable: no MCP manager",
            "[cmd] config reload unavailable: no config store",
        ]

    @pytest.mark.asyncio
    async def test_a_failing_callback_is_reported(self):
        hub = LogHub()

        async def boom() -> str:
            raise RuntimeError("mcp down")

        await CommandExecutor(hub, reconnect_mcp=boom).run("mcp reconnect")

        assert cmd_lines(hub) == ["[cmd] mcp reconnect failed: mcp down"]

    @pytest.mark.asyncio
    async def test_output_is_tagged_as_cmd_source(self):
        hub = LogHub()

        await CommandExecutor(hub).run("help")

        assert {e["source"] for e in hub.lines()} == {"cmd"}
