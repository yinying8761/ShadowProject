"""
Tests for CircuitBreaker — pure state machine (Workflow E, ticket #15).

Covers CLOSED / OPEN / HALF_OPEN transitions, single-probe semantics,
success resetting the failure count, and reset().
"""

import pytest

from core.circuit_breaker import CircuitBreaker, CLOSED, OPEN, HALF_OPEN


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
