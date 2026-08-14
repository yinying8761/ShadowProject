"""
Tests for ToolRuntime tool retry — Workflow E ticket #17 (E2).

Covers: retry on transient exceptions/timeouts, no retry on deterministic
JSON errors, retry_count persisted to tool_runs and serialized by the API.
"""

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime
from database import Base


class RecordingSleep:
    """Fake sleep that records delays instead of sleeping."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def _make_runtime(reg, sleep, *, enable_sandbox=False):
    """Build a ToolRuntime with tracing enabled + an in-memory trace store."""
    from services.tool_trace_store import ToolTraceStore

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    rt = ToolRuntime(
        registry=reg,
        enable_tracing=True,
        enable_sandbox=enable_sandbox,
        retry_sleep=sleep,
    )
    rt._trace_store = ToolTraceStore(session_factory=factory)
    return rt, factory, engine


async def _latest_run(factory):
    from models.tool_run import ToolRun

    async with factory() as s:
        return (await s.execute(select(ToolRun))).scalars().one()


class TestToolRetry:
    @pytest.mark.asyncio
    async def test_retries_transient_then_succeeds(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("slow")
            return "ok"

        rt.register("flaky", "flaky", {"type": "object", "properties": {}}, flaky, False,
                    retry_config={"max_retries": 1})

        result = await rt.dispatch("flaky", {})

        assert result == "ok"
        assert calls["n"] == 2
        assert sleep.delays == [0.5]
        run = await _latest_run(factory)
        assert run.retry_count == 1
        assert run.success is True
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_retries_exhausted_marks_failure(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def always_fails(**kw):
            calls["n"] += 1
            raise TimeoutError("always slow")

        rt.register("always_fails", "fails", {"type": "object", "properties": {}}, always_fails, False,
                    retry_config={"max_retries": 1})

        result = await rt.dispatch("always_fails", {})

        assert "error" in json.loads(result)
        assert calls["n"] == 2
        assert sleep.delays == [0.5]
        run = await _latest_run(factory)
        assert run.retry_count == 1
        assert run.success is False
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_no_retry_by_default(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def fails(**kw):
            calls["n"] += 1
            raise TimeoutError("slow")

        rt.register("fails", "fails", {"type": "object", "properties": {}}, fails, False)

        result = await rt.dispatch("fails", {})

        assert "error" in json.loads(result)
        assert calls["n"] == 1
        assert sleep.delays == []
        run = await _latest_run(factory)
        assert run.retry_count == 0
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_deterministic_json_error_not_retried(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def not_found(**kw):
            calls["n"] += 1
            return json.dumps({"error": "not found"})

        rt.register("not_found", "finds", {"type": "object", "properties": {}}, not_found, False,
                    retry_config={"max_retries": 1})

        result = await rt.dispatch("not_found", {})

        assert json.loads(result)["error"] == "not found"
        assert calls["n"] == 1  # deterministic failure — no exception, no retry
        assert sleep.delays == []
        run = await _latest_run(factory)
        assert run.retry_count == 0
        assert run.success is False
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_idempotent_tool_retries_by_default(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def flaky_read(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("slow")
            return "contents"

        # No explicit retry_config — the idempotent default (max_retries=1) applies.
        rt.register("read_file", "read", {"type": "object", "properties": {}}, flaky_read, False)

        result = await rt.dispatch("read_file", {})

        assert result == "contents"
        assert calls["n"] == 2
        run = await _latest_run(factory)
        assert run.retry_count == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_custom_retryable_exceptions(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def flaky_value(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad")
            return "ok"

        rt.register("flaky_value", "v", {"type": "object", "properties": {}}, flaky_value, False,
                    retry_config={"max_retries": 1, "retryable_exceptions": (ValueError,)})

        result = await rt.dispatch("flaky_value", {})

        assert result == "ok"
        assert calls["n"] == 2
        run = await _latest_run(factory)
        assert run.retry_count == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_custom_retryable_does_not_retry_other_types(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep)

        calls = {"n": 0}

        async def key_error(**kw):
            calls["n"] += 1
            raise KeyError("missing")

        rt.register("key_error", "k", {"type": "object", "properties": {}}, key_error, False,
                    retry_config={"max_retries": 1, "retryable_exceptions": (ValueError,)})

        result = await rt.dispatch("key_error", {})

        assert "error" in json.loads(result)
        assert calls["n"] == 1  # KeyError is not in retryable_exceptions
        assert sleep.delays == []
        run = await _latest_run(factory)
        assert run.retry_count == 0
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_timeout_is_retried(self):
        reg = ToolRegistry()
        sleep = RecordingSleep()
        rt, factory, engine = await _make_runtime(reg, sleep, enable_sandbox=True)

        calls = {"n": 0}

        async def slow(**kw):
            calls["n"] += 1
            await asyncio.sleep(0.3)
            return "done"

        rt.register("slow", "slow", {"type": "object", "properties": {}}, slow, False,
                    sandbox_config={"timeout_sec": 0.05},
                    retry_config={"max_retries": 1})

        result = await rt.dispatch("slow", {})

        assert "Timeout" in json.loads(result)["error"]
        assert calls["n"] == 2  # timed out on both attempts
        assert sleep.delays == [0.5]
        run = await _latest_run(factory)
        assert run.retry_count == 1
        assert run.success is False
        await engine.dispose()


class TestRunSerialization:
    def test_run_to_dict_includes_retry_count(self):
        from api.tool_logs import _run_to_dict
        from models.tool_run import ToolRun

        run = ToolRun(call_id="c1", tool_name="research", retry_count=3)
        assert _run_to_dict(run)["retry_count"] == 3
