"""
Tests for Ticket 01 — ToolRuntime core + tracing (D1 + D3).

Covers:
- Seam 1: ToolRuntime + isolated ToolRegistry (in-memory DB for traces)
- Seam 3: Agent + ToolRuntime injection (full pipeline with FakeLLMService)
"""

import asyncio
import json

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime, _default_timeout_sec
from database import Base


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_in_memory_store():
    """Create a ToolTraceStore backed by an in-memory SQLite database."""
    from services.tool_trace_store import ToolTraceStore

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create tool_runs table
    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, factory

    return ToolTraceStore(session_factory=factory), _init


# ── Seam 1: ToolRuntime + isolated ToolRegistry ────────────────────────────


class TestToolRuntimeDispatch:
    """dispatch() with tracing enabled — trace records are persisted."""

    @pytest.mark.asyncio
    async def test_dispatch_success_writes_trace(self):
        """A successful dispatch writes a trace record with success=True."""
        reg = ToolRegistry()

        async def echo(**kw) -> str:
            return f"echo: {kw}"

        reg.register("echo", "echo tool", {"type": "object", "properties": {}}, echo, False)

        from services.tool_trace_store import ToolTraceStore
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        store = ToolTraceStore(session_factory=factory)
        rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
        rt._trace_store = store  # inject in-memory store

        result = await rt.dispatch("echo", {"msg": "hello"})
        assert "echo:" in result

        # Verify trace was written
        from models.tool_run import ToolRun
        async with factory() as s:
            rows = (await s.execute(select(ToolRun))).scalars().all()
            assert len(rows) == 1
            run = rows[0]
            assert run.tool_name == "echo"
            assert run.success is True
            assert run.error_message is None
            assert run.elapsed_ms >= 0
            assert "hello" in run.arguments

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_failure_writes_trace_with_error(self):
        """When a tool handler raises, trace records success=False + error_message."""
        reg = ToolRegistry()

        async def broken(**kw) -> str:
            raise RuntimeError("boom")

        reg.register("broken", "breaks", {"type": "object", "properties": {}}, broken, False)

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        from services.tool_trace_store import ToolTraceStore
        store = ToolTraceStore(session_factory=factory)
        rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
        rt._trace_store = store

        result = await rt.dispatch("broken", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "boom" in parsed["error"]

        from models.tool_run import ToolRun
        async with factory() as s:
            rows = (await s.execute(select(ToolRun))).scalars().all()
            assert len(rows) == 1
            run = rows[0]
            assert run.tool_name == "broken"
            assert run.success is False
            assert run.error_message == "boom"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_dispatch_json_error_detected_as_failure(self):
        """When a tool returns JSON with 'error' key, success is marked False."""
        reg = ToolRegistry()

        async def json_error(**kw) -> str:
            return json.dumps({"error": "not found"})

        reg.register("finder", "finds", {"type": "object", "properties": {}}, json_error, False)

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        from services.tool_trace_store import ToolTraceStore
        store = ToolTraceStore(session_factory=factory)
        rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
        rt._trace_store = store

        result = await rt.dispatch("finder", {})
        parsed = json.loads(result)
        assert parsed["error"] == "not found"

        from models.tool_run import ToolRun
        async with factory() as s:
            run = (await s.execute(select(ToolRun))).scalars().one()
            assert run.success is False
            assert run.error_message == "not found"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_tracing_disabled_skips_persistence(self):
        """When enable_tracing=False, no trace is written."""
        reg = ToolRegistry()

        async def echo(**kw) -> str:
            return f"echo: {kw}"

        reg.register("echo", "echo", {"type": "object", "properties": {}}, echo, False)

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        from services.tool_trace_store import ToolTraceStore
        store = ToolTraceStore(session_factory=factory)
        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)
        rt._trace_store = store

        await rt.dispatch("echo", {"x": 1})

        from models.tool_run import ToolRun
        async with factory() as s:
            count = (await s.execute(select(func.count(ToolRun.id)))).scalar()
            assert count == 0

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_elapsed_ms_recorded(self):
        """dispatch records plausible elapsed time."""
        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.05)
            return "ok"

        reg.register("slow", "slow tool", {"type": "object", "properties": {}}, slow, False)

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        from services.tool_trace_store import ToolTraceStore
        store = ToolTraceStore(session_factory=factory)
        rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
        rt._trace_store = store

        await rt.dispatch("slow", {})

        from models.tool_run import ToolRun
        async with factory() as s:
            run = (await s.execute(select(ToolRun))).scalars().one()
            # Should be at least ~50ms
            assert run.elapsed_ms >= 40

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_conversation_id_stored_in_trace(self):
        """When conversation_id is passed, it appears in the trace record."""
        reg = ToolRegistry()

        async def echo(**kw) -> str:
            return "ok"

        reg.register("echo", "echo", {"type": "object", "properties": {}}, echo, False)

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        from services.tool_trace_store import ToolTraceStore
        store = ToolTraceStore(session_factory=factory)
        rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
        rt._trace_store = store

        await rt.dispatch("echo", {}, conversation_id="conv-abc")

        from models.tool_run import ToolRun
        async with factory() as s:
            run = (await s.execute(select(ToolRun))).scalars().one()
            assert run.conversation_id == "conv-abc"

        await engine.dispose()


class TestToolRuntimeDelegation:
    """register / unregister / get_tool_definitions / needs_approval delegate correctly."""

    def test_get_tool_definitions_delegates(self):
        reg = ToolRegistry()

        async def t(**kw) -> str:
            return "ok"

        reg.register("t1", "desc", {"type": "object", "properties": {}}, t, False)
        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)

        defs = rt.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "t1"

    def test_needs_approval_delegates(self):
        reg = ToolRegistry()

        async def t(**kw) -> str:
            return "ok"

        reg.register("safe", "safe", {"type": "object", "properties": {}}, t, False)
        reg.register("danger", "danger", {"type": "object", "properties": {}}, t, True)
        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)

        assert rt.needs_approval("safe") is False
        assert rt.needs_approval("danger") is True
        assert rt.needs_approval("unknown") is False

    def test_unregister_removes_tool(self):
        reg = ToolRegistry()

        async def t(**kw) -> str:
            return "ok"

        reg.register("tmp", "temp", {"type": "object", "properties": {}}, t, False)
        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)
        assert len(rt.get_tool_definitions()) == 1

        rt.unregister("tmp")
        assert len(rt.get_tool_definitions()) == 0

    def test_register_passes_sandbox_config(self):
        """sandbox_config is stored on ToolRuntime, not passed to inner registry."""
        reg = ToolRegistry()

        async def t(**kw) -> str:
            return "ok"

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)
        rt.register("net", "net tool", {"type": "object", "properties": {}}, t, False,
                     sandbox_config={"timeout_sec": 30.0})

        assert rt._sandbox["net"] == {"timeout_sec": 30.0}
        # Tool is still registered on inner
        assert len(reg.get_tool_definitions()) == 1


class TestDefaultTimeouts:
    """_default_timeout_sec returns correct timeouts per tool category."""

    def test_file_tools_10s(self):
        assert _default_timeout_sec("read_file") == 10.0
        assert _default_timeout_sec("write_file") == 10.0
        assert _default_timeout_sec("list_directory") == 10.0
        assert _default_timeout_sec("search_files") == 10.0

    def test_network_tools_30s(self):
        assert _default_timeout_sec("fetch_url") == 30.0
        assert _default_timeout_sec("research") == 30.0

    def test_mcp_tools_30s(self):
        assert _default_timeout_sec("mcp__github__list_issues") == 30.0

    def test_screen_tools_15s(self):
        assert _default_timeout_sec("see_screen") == 15.0

    def test_unknown_tools_10s(self):
        assert _default_timeout_sec("some_future_tool") == 10.0


class TestD3Placeholders:
    """D3 feature flags are stored as instance attributes."""

    def test_default_flags(self):
        rt = ToolRuntime()
        assert rt.enable_circuit_breaker is False
        assert rt.enable_rate_limit is False
        assert rt.circuit_threshold == 5
        assert rt.rate_limit_per_min == 30

    def test_custom_flags(self):
        rt = ToolRuntime(
            enable_circuit_breaker=True,
            enable_rate_limit=True,
            circuit_threshold=10,
            rate_limit_per_min=60,
        )
        assert rt.enable_circuit_breaker is True
        assert rt.enable_rate_limit is True
        assert rt.circuit_threshold == 10
        assert rt.rate_limit_per_min == 60

    def test_placeholders_do_not_affect_dispatch(self):
        """Even with circuit_breaker/rate_limit 'enabled', dispatch works normally."""
        reg = ToolRegistry()

        async def echo(**kw) -> str:
            return "ok"

        reg.register("echo", "echo", {"type": "object", "properties": {}}, echo, False)
        rt = ToolRuntime(
            registry=reg,
            enable_tracing=False,
            enable_sandbox=False,
            enable_circuit_breaker=True,
            enable_rate_limit=True,
        )

        async def _run():
            return await rt.dispatch("echo", {})

        result = asyncio.run(_run())
        assert result == "ok"


# ── Seam 3: Agent + ToolRuntime injection ───────────────────────────────────


class FakeLLMService:
    """Yields a preset sequence of events from stream_chat()."""

    def __init__(self, events: list[dict]):
        self._events = events
        self._called = False

    async def stream_chat(self, messages, tools=None):
        if self._called:
            return
        self._called = True
        for event in self._events:
            yield event


async def _fake_memory_search(session, query="", top_k=3, character_id=None):
    return []


class TestAgentWithToolRuntime:
    """Agent.run() works correctly when injected with a ToolRuntime."""

    @pytest.mark.asyncio
    async def test_agent_dispatches_through_tool_runtime(self, monkeypatch):
        """Full pipeline: FakeLLM → tool_use → ToolRuntime dispatch → tool_result."""
        from services.memory_service import memory_service as memory_svc
        from core.agent import Agent

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation
            from models.user_config import UserConfig

            char = CharacterProfile(id="char-rt-1", name="RTTest", personality="cool",
                                     role="companion", archetype="friend")
            conv = Conversation(id="conv-rt-1", character_id="char-rt-1")
            cfg = UserConfig(id=1)
            session.add_all([char, conv, cfg])
            await session.commit()

            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            # Build ToolRuntime with tracing-enabled in-memory trace store
            reg = ToolRegistry()

            async def echo(**kw) -> str:
                return f"echo: {kw}"

            reg.register("echo", "echo", {"type": "object", "properties": {}}, echo, False)

            from services.tool_trace_store import ToolTraceStore
            trace_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
            async with trace_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            trace_factory = async_sessionmaker(trace_engine, class_=AsyncSession, expire_on_commit=False)
            trace_store = ToolTraceStore(session_factory=trace_factory)

            rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
            rt._trace_store = trace_store

            fake_llm = FakeLLMService([
                {"type": "token", "content": "好的"},
                {"type": "tool_use", "id": "call_rt_1", "name": "echo",
                 "arguments": {"msg": "from_agent"}},
                {"type": "token", "content": "完成。"},
            ])

            agent = Agent(llm_service=fake_llm, tool_registry=rt)

            events = []
            async for event in agent.run(
                session=session,
                user_message="测试",
                conversation_id="conv-rt-1",
                character_id="char-rt-1",
            ):
                events.append(event)

            # Verify tool_result event
            tool_results = [e for e in events if e["type"] == "tool_result"]
            assert len(tool_results) == 1
            assert tool_results[0]["name"] == "echo"
            assert tool_results[0]["is_error"] is False
            assert "from_agent" in tool_results[0]["result"]

            # Verify done event
            assert events[-1]["type"] == "done"

            # Verify trace was written through ToolRuntime
            from models.tool_run import ToolRun
            async with trace_factory() as s:
                runs = (await s.execute(select(ToolRun))).scalars().all()
                assert len(runs) == 1
                assert runs[0].tool_name == "echo"
                assert runs[0].success is True

        await engine.dispose()
        await trace_engine.dispose()

    @pytest.mark.asyncio
    async def test_agent_tool_failure_traced(self, monkeypatch):
        """When a tool returns an error, the trace records success=False."""
        from services.memory_service import memory_service as memory_svc
        from core.agent import Agent

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation
            from models.user_config import UserConfig

            char = CharacterProfile(id="char-rt-2", name="RTTest2", personality="cool",
                                     role="companion", archetype="friend")
            conv = Conversation(id="conv-rt-2", character_id="char-rt-2")
            cfg = UserConfig(id=1)
            session.add_all([char, conv, cfg])
            await session.commit()

            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            reg = ToolRegistry()

            async def broken(**kw) -> str:
                return json.dumps({"error": "tool error"})

            reg.register("broken", "broken", {"type": "object", "properties": {}}, broken, False)

            from services.tool_trace_store import ToolTraceStore
            trace_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
            async with trace_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            trace_factory = async_sessionmaker(trace_engine, class_=AsyncSession, expire_on_commit=False)
            trace_store = ToolTraceStore(session_factory=trace_factory)

            rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
            rt._trace_store = trace_store

            fake_llm = FakeLLMService([
                {"type": "token", "content": "试试"},
                {"type": "tool_use", "id": "call_fail", "name": "broken", "arguments": {}},
                {"type": "token", "content": "失败了。"},
            ])

            agent = Agent(llm_service=fake_llm, tool_registry=rt)

            events = []
            async for event in agent.run(
                session=session,
                user_message="测试",
                conversation_id="conv-rt-2",
                character_id="char-rt-2",
            ):
                events.append(event)

            # Verify tool_result has is_error=True
            tool_results = [e for e in events if e["type"] == "tool_result"]
            assert len(tool_results) == 1
            assert tool_results[0]["is_error"] is True

            # Trace shows failure
            from models.tool_run import ToolRun
            async with trace_factory() as s:
                run = (await s.execute(select(ToolRun))).scalars().one()
                assert run.success is False
                assert run.error_message == "tool error"

        await engine.dispose()
        await trace_engine.dispose()

    @pytest.mark.asyncio
    async def test_denied_tool_not_counted_as_failure(self, monkeypatch):
        """A user-denied tool call never reaches dispatch, so it can't trip the breaker (#18)."""
        from services.memory_service import memory_service as memory_svc
        from core.agent import Agent

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation
            from models.user_config import UserConfig

            char = CharacterProfile(id="char-deny-1", name="DenyTest", personality="cool",
                                     role="companion", archetype="friend")
            conv = Conversation(id="conv-deny-1", character_id="char-deny-1")
            cfg = UserConfig(id=1)
            session.add_all([char, conv, cfg])
            await session.commit()

            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            reg = ToolRegistry()
            calls = {"n": 0}

            async def dangerous(**kw) -> str:
                calls["n"] += 1
                return "written"

            reg.register("write_file", "write", {"type": "object", "properties": {}}, dangerous, True)

            # CB enabled with threshold=1: a single failure would open it, so this
            # test proves the deny does NOT count as a circuit-breaker failure.
            rt = ToolRuntime(
                registry=reg,
                enable_tracing=False,
                enable_sandbox=False,
                enable_circuit_breaker=True,
                circuit_threshold=1,
            )

            fake_llm = FakeLLMService([
                {"type": "token", "content": "我来写文件"},
                {"type": "tool_use", "id": "call_deny", "name": "write_file",
                 "arguments": {"path": "/tmp/x"}},
                {"type": "token", "content": "写好了。"},
            ])

            agent = Agent(llm_service=fake_llm, tool_registry=rt)

            async def deny(name, arguments):
                return False

            events = []
            async for event in agent.run(
                session=session,
                user_message="测试",
                conversation_id="conv-deny-1",
                character_id="char-deny-1",
                approval_callback=deny,
            ):
                events.append(event)

            # Denied → handler never invoked, breaker never consulted.
            assert calls["n"] == 0
            assert "write_file" not in rt._circuit_breakers

            # The deny surfaced as a tool_result with denied=True (an error to the
            # LLM, but NOT a circuit-breaker failure).
            denied = [e for e in events if e["type"] == "tool_result"]
            assert len(denied) == 1
            assert denied[0]["denied"] is True

        await engine.dispose()


# ── Seam 2: GET /api/tool-runs — in-memory SQLite + TestClient ──────────────


@pytest.fixture
async def tool_logs_client():
    """TestClient wired to an in-memory DB with tool_runs table seeded."""
    from httpx import ASGITransport, AsyncClient
    import models.tool_run  # noqa: F401 — ensure table is registered on Base.metadata

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from main import app
    from database import get_session

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory, engine
    app.dependency_overrides.clear()
    await engine.dispose()


def _make_run(**kw) -> dict:
    """Minimal dict for directly inserting a ToolRun via ORM."""
    import uuid as _uuid

    defaults = {
        "id": str(_uuid.uuid4()),
        "call_id": str(_uuid.uuid4()),
        "tool_name": "research",
        "arguments": '{"query": "test"}',
        "result_summary": "ok",
        "elapsed_ms": 100,
        "success": True,
        "error_message": None,
        "conversation_id": None,
    }
    defaults.update(kw)
    return defaults


async def _seed_runs(factory, *runs):
    """Insert ToolRun rows through the ORM session."""
    from models.tool_run import ToolRun

    async with factory() as s:
        for r in runs:
            s.add(ToolRun(**r))
        await s.commit()


class TestToolRunsAPI:
    """GET /api/tool-runs returns paginated, filterable results."""

    @pytest.mark.asyncio
    async def test_returns_paginated_runs(self, tool_logs_client):
        """Endpoint returns total + runs array with correct fields."""
        client, factory, _ = tool_logs_client
        await _seed_runs(factory, _make_run(), _make_run())

        r = await client.get("/api/tool-runs?limit=10")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["runs"]) == 2

        run = data["runs"][0]
        assert "id" in run
        assert "call_id" in run
        assert "tool_name" in run
        assert "arguments" in run
        assert "result_summary" in run
        assert "elapsed_ms" in run
        assert "success" in run
        assert "error_message" in run
        assert "conversation_id" in run
        assert "created_at" in run

    @pytest.mark.asyncio
    async def test_newest_first(self, tool_logs_client):
        """Results are ordered by created_at DESC."""
        from datetime import datetime, timezone, timedelta

        client, factory, _ = tool_logs_client
        now = datetime.now(timezone.utc)
        await _seed_runs(
            factory,
            _make_run(tool_name="older", created_at=now - timedelta(hours=1)),
            _make_run(tool_name="newer", created_at=now),
        )

        r = await client.get("/api/tool-runs")
        data = r.json()
        names = [run["tool_name"] for run in data["runs"]]
        assert names[0] == "newer"
        assert names[1] == "older"

    @pytest.mark.asyncio
    async def test_filter_by_tool_name(self, tool_logs_client):
        """tool_name query param filters to exact match only."""
        client, factory, _ = tool_logs_client
        await _seed_runs(
            factory,
            _make_run(tool_name="research"),
            _make_run(tool_name="research"),
            _make_run(tool_name="see_screen"),
        )

        r = await client.get("/api/tool-runs?tool_name=research")
        data = r.json()
        assert data["total"] == 2
        for run in data["runs"]:
            assert run["tool_name"] == "research"

    @pytest.mark.asyncio
    async def test_filter_by_success_true(self, tool_logs_client):
        """success=true returns only successful runs."""
        client, factory, _ = tool_logs_client
        await _seed_runs(
            factory,
            _make_run(success=True),
            _make_run(success=True),
            _make_run(success=False, error_message="fail"),
        )

        r = await client.get("/api/tool-runs?success=true")
        data = r.json()
        assert data["total"] == 2
        for run in data["runs"]:
            assert run["success"] is True

    @pytest.mark.asyncio
    async def test_filter_by_success_false(self, tool_logs_client):
        """success=false returns only failed runs."""
        client, factory, _ = tool_logs_client
        await _seed_runs(
            factory,
            _make_run(success=True),
            _make_run(success=False, error_message="e1"),
            _make_run(success=False, error_message="e2"),
        )

        r = await client.get("/api/tool-runs?success=false")
        data = r.json()
        assert data["total"] == 2
        for run in data["runs"]:
            assert run["success"] is False

    @pytest.mark.asyncio
    async def test_invalid_success_ignored(self, tool_logs_client):
        """An invalid success value is silently ignored (no filter applied)."""
        client, factory, _ = tool_logs_client
        await _seed_runs(factory, _make_run(success=True), _make_run(success=False, error_message="e"))

        r = await client.get("/api/tool-runs?success=maybe")
        data = r.json()
        assert data["total"] == 2  # all returned

    @pytest.mark.asyncio
    async def test_pagination_offset(self, tool_logs_client):
        """offset skips the first N results."""
        client, factory, _ = tool_logs_client
        await _seed_runs(factory, _make_run(), _make_run(), _make_run())

        r = await client.get("/api/tool-runs?limit=2&offset=1")
        data = r.json()
        assert data["total"] == 3
        assert len(data["runs"]) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self, tool_logs_client):
        """When no runs exist, total=0 and runs=[]."""
        client, _, _ = tool_logs_client

        r = await client.get("/api/tool-runs")
        data = r.json()
        assert data["total"] == 0
        assert data["runs"] == []

    @pytest.mark.asyncio
    async def test_filter_combined(self, tool_logs_client):
        """tool_name and success can be combined."""
        client, factory, _ = tool_logs_client
        await _seed_runs(
            factory,
            _make_run(tool_name="research", success=True),
            _make_run(tool_name="research", success=False, error_message="x"),
            _make_run(tool_name="see_screen", success=True),
        )

        r = await client.get("/api/tool-runs?tool_name=research&success=true")
        data = r.json()
        assert data["total"] == 1
        assert data["runs"][0]["tool_name"] == "research"
        assert data["runs"][0]["success"] is True

    @pytest.mark.asyncio
    async def test_arguments_deserialized_from_json(self, tool_logs_client):
        """The arguments field is returned as a dict, not a JSON string."""
        client, factory, _ = tool_logs_client
        await _seed_runs(factory, _make_run(arguments='{"query": "hello", "limit": 5}'))

        r = await client.get("/api/tool-runs")
        data = r.json()
        args = data["runs"][0]["arguments"]
        assert isinstance(args, dict)
        assert args["query"] == "hello"
        assert args["limit"] == 5


# ── Seam 4: Sandbox timeout — pure async, no DB/HTTP ────────────────────────


class TestSandboxTimeout:
    """asyncio.wait_for wrapping in ToolRuntime.dispatch()."""

    @pytest.mark.asyncio
    async def test_timeout_kills_slow_handler(self):
        """A handler slower than its timeout is killed; returns error JSON."""
        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.5)
            return "done"

        reg.register("slow", "slow tool", {"type": "object", "properties": {}}, slow, False)

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=True)
        rt.register("slow", "slow tool", {"type": "object", "properties": {}}, slow, False,
                     sandbox_config={"timeout_sec": 0.1})

        result = await rt.dispatch("slow", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Timeout" in parsed["error"]
        assert "0s" in parsed["error"]  # 0.1 → "0s" after floor

    @pytest.mark.asyncio
    async def test_fast_handler_not_affected(self):
        """A handler that completes within timeout is not affected."""
        reg = ToolRegistry()

        async def fast(**kw) -> str:
            return "quick response"

        reg.register("fast", "fast", {"type": "object", "properties": {}}, fast, False)

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=True)
        rt.register("fast", "fast", {"type": "object", "properties": {}}, fast, False,
                     sandbox_config={"timeout_sec": 5.0})

        result = await rt.dispatch("fast", {})
        assert result == "quick response"

    @pytest.mark.asyncio
    async def test_sandbox_disabled_no_timeout(self):
        """When enable_sandbox=False, a slow handler is NOT killed."""
        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.1)
            return "slow but done"

        reg.register("slow", "slow", {"type": "object", "properties": {}}, slow, False)

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)
        rt.register("slow", "slow", {"type": "object", "properties": {}}, slow, False,
                     sandbox_config={"timeout_sec": 0.01})  # very short timeout

        result = await rt.dispatch("slow", {})
        # Sandbox disabled → handler runs to completion, timeout ignored
        assert result == "slow but done"

    @pytest.mark.asyncio
    async def test_default_timeout_used_when_no_sandbox_config(self):
        """When no explicit sandbox_config is given, category default is used."""
        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.3)
            return "done"

        # "read_file" → file tool → default timeout 10s → won't timeout for 0.3s
        reg.register("read_file", "read", {"type": "object", "properties": {}}, slow, False)

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=True)
        rt.register("read_file", "read", {"type": "object", "properties": {}}, slow, False)

        result = await rt.dispatch("read_file", {"path": "/tmp/test"})
        assert result == "done"

    @pytest.mark.asyncio
    async def test_custom_timeout_overrides_default(self):
        """Explicit sandbox_config.timeout_sec overrides the category default."""
        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.3)
            return "done"

        # read_file defaults to 10s, but we override to 0.1s → timeout
        reg.register("read_file", "read", {"type": "object", "properties": {}}, slow, False)

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=True)
        rt.register("read_file", "read", {"type": "object", "properties": {}}, slow, False,
                     sandbox_config={"timeout_sec": 0.1})

        result = await rt.dispatch("read_file", {"path": "/tmp/test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Timeout" in parsed["error"]

    @pytest.mark.asyncio
    async def test_timeout_error_not_propagated(self):
        """asyncio.TimeoutError is caught, not raised to the caller."""
        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.3)
            return "done"

        reg.register("slow", "slow", {"type": "object", "properties": {}}, slow, False)

        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=True)
        rt.register("slow", "slow", {"type": "object", "properties": {}}, slow, False,
                     sandbox_config={"timeout_sec": 0.05})

        # Should NOT raise — timeout is caught and returned as error JSON
        result = await rt.dispatch("slow", {})
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_trace_records_timeout_as_failure(self):
        """When a handler times out, the trace shows success=False."""
        import models.tool_run  # noqa: F401 — ensure table on Base.metadata

        reg = ToolRegistry()

        async def slow(**kw) -> str:
            await asyncio.sleep(0.3)
            return "done"

        reg.register("slow", "slow", {"type": "object", "properties": {}}, slow, False)

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        from services.tool_trace_store import ToolTraceStore
        store = ToolTraceStore(session_factory=factory)

        rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=True)
        rt._trace_store = store
        rt.register("slow", "slow", {"type": "object", "properties": {}}, slow, False,
                     sandbox_config={"timeout_sec": 0.05})

        await rt.dispatch("slow", {})

        from models.tool_run import ToolRun
        async with factory() as s:
            run = (await s.execute(select(ToolRun))).scalars().one()
            assert run.success is False
            assert "Timeout" in (run.error_message or "")

        await engine.dispose()
