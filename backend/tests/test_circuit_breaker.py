"""
Tests for CircuitBreaker — state machine + ToolRuntime integration (Workflow E).

Covers the pure CLOSED / OPEN / HALF_OPEN transitions (ticket #15) and
the ToolRuntime.dispatch wiring: short-circuit, cooldown recovery, and
per-tool independence (ticket #18).
"""

import json

import pytest

from core.circuit_breaker import CircuitBreaker, CLOSED, OPEN, HALF_OPEN
from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime


class FakeClock:
    """Controllable monotonic clock for cooldown tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestInitialState:
    def test_starts_closed_and_allows(self):
        cb = CircuitBreaker()
        assert cb.state == CLOSED
        assert cb.failure_count == 0
        assert cb.allow_request() is True

    def test_default_threshold_and_timeout(self):
        cb = CircuitBreaker()
        assert cb.threshold == 5
        assert cb.open_timeout == 60.0


class TestClosedToOpen:
    def test_below_threshold_stays_closed(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CLOSED
        assert cb.failure_count == 2
        assert cb.allow_request() is True

    def test_reaching_threshold_opens(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == OPEN
        assert cb.allow_request() is False

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        # Two more failures still under threshold
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CLOSED


class TestOpenCooldown:
    def _open_breaker(self, threshold=1, open_timeout=60.0):
        clock = FakeClock()
        cb = CircuitBreaker(threshold=threshold, open_timeout=open_timeout, clock=clock)
        cb.record_failure()  # now OPEN
        return cb, clock

    def test_rejects_until_cooldown_elapses(self):
        cb, clock = self._open_breaker(open_timeout=60.0)
        clock.advance(30)
        assert cb.allow_request() is False
        clock.advance(29)
        assert cb.allow_request() is False  # 59s total, still cooling

    def test_probe_allowed_after_cooldown(self):
        cb, clock = self._open_breaker(open_timeout=60.0)
        clock.advance(60)
        assert cb.allow_request() is True
        assert cb.state == HALF_OPEN


class TestHalfOpen:
    def _half_open(self, open_timeout=60.0):
        clock = FakeClock()
        cb = CircuitBreaker(threshold=1, open_timeout=open_timeout, clock=clock)
        cb.record_failure()  # OPEN
        clock.advance(open_timeout)
        assert cb.allow_request() is True  # probe dispatched → HALF_OPEN
        return cb, clock

    def test_only_single_probe_allowed(self):
        cb, _ = self._half_open()
        assert cb.allow_request() is False  # second request rejected

    def test_probe_success_closes_and_resets(self):
        cb, _ = self._half_open()
        cb.record_success()
        assert cb.state == CLOSED
        assert cb.failure_count == 0
        assert cb.allow_request() is True

    def test_probe_failure_reopens(self):
        cb, clock = self._half_open(open_timeout=60.0)
        cb.record_failure()
        assert cb.state == OPEN
        # Re-timed: still cooling for a fresh 60s
        clock.advance(59)
        assert cb.allow_request() is False
        clock.advance(1)
        assert cb.allow_request() is True


class TestReset:
    def test_reset_forces_closed(self):
        cb = CircuitBreaker(threshold=1)
        cb.record_failure()
        assert cb.state == OPEN
        cb.reset()
        assert cb.state == CLOSED
        assert cb.failure_count == 0
        assert cb.allow_request() is True


class TestGuard:
    def test_threshold_must_be_positive(self):
        with pytest.raises(ValueError):
            CircuitBreaker(threshold=0)


class TestCircuitBreakerIntegration:
    """Circuit breaker wired into ToolRuntime.dispatch (Workflow E, ticket #18)."""

    def _make_rt(
        self, reg, clock, *, threshold=5, open_sec=60.0,
        enable_tracing=False, trace_store=None,
    ):
        rt = ToolRuntime(
            registry=reg,
            enable_tracing=enable_tracing,
            enable_sandbox=False,
            enable_circuit_breaker=True,
            circuit_threshold=threshold,
            circuit_open_sec=open_sec,
            clock=clock,
        )
        if trace_store is not None:
            rt._trace_store = trace_store
        return rt

    @pytest.mark.asyncio
    async def test_short_circuits_when_open(self):
        reg = ToolRegistry()
        clock = FakeClock()
        rt = self._make_rt(reg, clock, threshold=2)

        calls = {"n": 0}

        async def failing(**kw):
            calls["n"] += 1
            raise RuntimeError("boom")

        rt.register("failing", "fails", {"type": "object", "properties": {}}, failing, False)

        await rt.dispatch("failing", {})
        await rt.dispatch("failing", {})
        assert calls["n"] == 2  # breaker now OPEN

        result = await rt.dispatch("failing", {})
        assert calls["n"] == 2  # short-circuited — handler NOT called
        assert "Circuit breaker open" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_recovers_after_cooldown(self):
        reg = ToolRegistry()
        clock = FakeClock()
        rt = self._make_rt(reg, clock, threshold=1)

        calls = {"n": 0}

        async def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return "ok"

        rt.register("flaky", "flaky", {"type": "object", "properties": {}}, flaky, False)

        await rt.dispatch("flaky", {})  # fails → OPEN
        clock.advance(60)
        result = await rt.dispatch("flaky", {})  # probe → success
        assert result == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_probe_failure_reopens(self):
        reg = ToolRegistry()
        clock = FakeClock()
        rt = self._make_rt(reg, clock, threshold=1)

        calls = {"n": 0}

        async def always_fails(**kw):
            calls["n"] += 1
            raise RuntimeError("boom")

        rt.register("always_fails", "fails", {"type": "object", "properties": {}}, always_fails, False)

        await rt.dispatch("always_fails", {})  # OPEN
        clock.advance(60)
        await rt.dispatch("always_fails", {})  # probe fails → OPEN (re-timed)
        assert calls["n"] == 2

        clock.advance(59)
        result = await rt.dispatch("always_fails", {})
        assert calls["n"] == 2  # still cooling — rejected
        assert "Circuit breaker open" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_breaker_per_tool_independent(self):
        reg = ToolRegistry()
        clock = FakeClock()
        rt = self._make_rt(reg, clock, threshold=1)

        async def failing(**kw):
            raise RuntimeError("boom")

        async def ok(**kw):
            return "ok"

        rt.register("a_failing", "a", {"type": "object", "properties": {}}, failing, False)
        rt.register("b_ok", "b", {"type": "object", "properties": {}}, ok, False)

        await rt.dispatch("a_failing", {})  # a → OPEN

        assert await rt.dispatch("b_ok", {}) == "ok"  # b unaffected

        result_a = await rt.dispatch("a_failing", {})
        assert "Circuit breaker open" in json.loads(result_a)["error"]

    @pytest.mark.asyncio
    async def test_logs_transition_on_open(self, capsys):
        reg = ToolRegistry()
        clock = FakeClock()
        rt = self._make_rt(reg, clock, threshold=1)

        async def failing(**kw):
            raise RuntimeError("boom")

        rt.register("failing", "fails", {"type": "object", "properties": {}}, failing, False)

        await rt.dispatch("failing", {})
        assert "[CircuitBreaker] failing OPEN" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_rejected_dispatch_traces_failure(self):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from database import Base
        from services.tool_trace_store import ToolTraceStore
        from models.tool_run import ToolRun

        reg = ToolRegistry()
        clock = FakeClock()

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        store = ToolTraceStore(session_factory=factory)

        rt = self._make_rt(reg, clock, threshold=1, enable_tracing=True, trace_store=store)

        async def failing(**kw):
            raise RuntimeError("boom")

        rt.register("failing", "fails", {"type": "object", "properties": {}}, failing, False)

        await rt.dispatch("failing", {})  # OPEN
        await rt.dispatch("failing", {})  # rejected

        async with factory() as s:
            runs = (await s.execute(select(ToolRun))).scalars().all()
            assert len(runs) == 2
            rejected = runs[1]
            assert rejected.success is False
            assert "Circuit breaker open" in (rejected.error_message or "")

        await engine.dispose()
