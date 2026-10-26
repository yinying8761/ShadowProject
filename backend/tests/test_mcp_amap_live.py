"""Live tests for the amap MCP server (opt-in).

`16 tool(s) registered` only proves the handshake worked. A server can list its
tools and still fail every single call — bad key, exhausted quota, changed
argument names — and MCP has no static contract we could check: **the wire is
the contract**. So these tests spawn the real `amap_mcp_server` process and call
the real AMap API with the key from `data/mcp_servers.json`.

They are skipped by default (they need network + quota):

    cd backend
    $env:MCP_LIVE_TESTS = 1     # PowerShell
    python -m pytest tests/test_mcp_amap_live.py -v

If the config or the key is missing, the tests skip instead of failing, so a
fresh checkout stays green.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from core.tool_runtime import ToolRuntime
from services.mcp_manager import McpManager

pytestmark = pytest.mark.skipif(
    not os.environ.get("MCP_LIVE_TESTS"),
    reason="live MCP test: set MCP_LIVE_TESTS=1 (spawns amap server, uses API quota)",
)

#: data/mcp_servers.json lives at the repo root, two levels above tests/.
CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "mcp_servers.json"

#: The 16 tools `amap-mcp-server` 0.1.11 exposes. Pinned on purpose: an upgrade
#: that renames or drops a tool silently breaks the agent's ability to use it.
AMAP_TOOLS = {
    "mcp__amap__maps_regeocode",
    "mcp__amap__maps_geo",
    "mcp__amap__maps_ip_location",
    "mcp__amap__maps_weather",
    "mcp__amap__maps_bicycling_by_address",
    "mcp__amap__maps_bicycling_by_coordinates",
    "mcp__amap__maps_direction_walking_by_address",
    "mcp__amap__maps_direction_walking_by_coordinates",
    "mcp__amap__maps_direction_driving_by_address",
    "mcp__amap__maps_direction_driving_by_coordinates",
    "mcp__amap__maps_direction_transit_integrated_by_address",
    "mcp__amap__maps_direction_transit_integrated_by_coordinates",
    "mcp__amap__maps_distance",
    "mcp__amap__maps_text_search",
    "mcp__amap__maps_around_search",
    "mcp__amap__maps_search_detail",
}


@pytest.fixture
async def runtime():
    """A ToolRuntime with only the real ``amap`` server connected."""
    if not CONFIG_PATH.exists():
        pytest.skip(f"{CONFIG_PATH} not configured")

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    servers = [s for s in cfg.get("servers", []) if s.get("name") == "amap"]
    if not servers:
        pytest.skip("no 'amap' server in data/mcp_servers.json")
    if not (servers[0].get("env") or {}).get("AMAP_MAPS_API_KEY"):
        pytest.skip("amap entry has no AMAP_MAPS_API_KEY")

    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps({"servers": servers}), encoding="utf-8")

    rt = ToolRuntime(enable_tracing=False, enable_sandbox=True, enable_circuit_breaker=False)
    manager = McpManager(rt)
    try:
        result = await manager.connect_all(path)
        if result["connected"] != 1:
            pytest.skip("amap server did not connect (see the [McpManager] log line)")
        yield rt
    finally:
        await manager.disconnect_all()
        Path(path).unlink(missing_ok=True)


class TestAmapContract:
    """The tool surface the agent actually sees."""

    async def test_every_tool_is_registered(self, runtime: ToolRuntime):
        registered = {d["name"] for d in runtime.get_tool_definitions()}
        assert AMAP_TOOLS <= registered

    async def test_mcp_tools_require_approval(self, runtime: ToolRuntime):
        # External tools must never run without the user's consent.
        for name in ("mcp__amap__maps_weather", "mcp__amap__maps_geo"):
            assert runtime.needs_approval(name) is True

    async def test_bad_arguments_are_rejected_before_the_call(
        self, runtime: ToolRuntime
    ):
        """Schema validation runs first, so a bad call costs no quota."""
        missing = json.loads(await runtime.dispatch("mcp__amap__maps_weather", {}))
        assert "Schema validation failed" in missing["error"]
        assert "'city' is a required property" in missing["error"]

        wrong_type = json.loads(
            await runtime.dispatch("mcp__amap__maps_weather", {"city": 1234})
        )
        assert "is not of type 'string'" in wrong_type["error"]


class TestAmapCalls:
    """Real calls: the things "connected" does not tell you."""

    async def test_weather_returns_a_forecast(self, runtime: ToolRuntime):
        raw = await runtime.dispatch("mcp__amap__maps_weather", {"city": "合肥"})

        assert not raw.startswith('{"error"'), raw[:200]
        data = json.loads(raw)
        assert "合肥" in data["city"]
        forecasts = data["forecasts"]
        assert forecasts, "weather returned no forecast days"
        day = forecasts[0]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", day["date"])
        assert day["dayweather"]  # e.g. 多云
        assert re.fullmatch(r"-?\d+", day["daytemp"])

    async def test_geocode_returns_coordinates(self, runtime: ToolRuntime):
        raw = await runtime.dispatch(
            "mcp__amap__maps_geo", {"address": "合肥南站", "city": "合肥"}
        )

        assert not raw.startswith('{"error"'), raw[:200]
        data = json.loads(raw)
        hits = data.get("return") or []
        assert hits, "geocode returned no match"
        location = hits[0]["location"]
        assert re.fullmatch(r"-?\d+\.\d+,-?\d+\.\d+", location), location
        assert hits[0]["city"] == "合肥市"

    async def test_text_search_returns_pois(self, runtime: ToolRuntime):
        raw = await runtime.dispatch(
            "mcp__amap__maps_text_search", {"keywords": "咖啡", "city": "合肥"}
        )

        assert not raw.startswith('{"error"'), raw[:200]
        data = json.loads(raw)
        pois = data.get("pois") or []
        assert pois, "text search returned no POI"
        assert pois[0].get("name")
