"""
Tests for ProactiveWatcher tick logging —routine ticks stay quiet unless
something changed (ticket 02 of the debug-console work).

Seams: should_log_tick() as a pure rule, and ProactiveWatcher.poll_once()
driven with constructor-injected fakes and a fake clock —no DB, no timers,
capsys for the log lines.
"""

from datetime import datetime

import pytest

from services.proactive_watcher import (
    TICK_DEBUG_ENV,
    ProactiveWatcher,
    TickSignature,
    should_log_tick,
)


class FakeClock:
    """Controllable monotonic clock, so idle time is what the test says."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class WatcherHarness:
    """A ProactiveWatcher on in-memory state, polled one round at a time."""

    def __init__(self, *, level: str = "low", daily_limit: int = 10, count: int = 0):
        self.clock = FakeClock()
        self.level = level
        self.daily_limit = daily_limit
        self.count = count
        self.fired: list[str] = []
        self.watcher = ProactiveWatcher(
            get_level=lambda: self.level,
            get_daily_limit=lambda: self.daily_limit,
            get_schedule_enabled=lambda: False,
            load_state=self._load_state,
            save_state=self._save_state,
            on_trigger=self._on_trigger,
            name="harness",
            clock=self.clock,
        )

    async def _load_state(self):
        return (datetime.now().strftime("%Y-%m-%d"), self.count, set())

    async def _save_state(self, date, count, slots):
        self.count = count

    async def _on_trigger(self, reason: str) -> None:
        self.fired.append(reason)


def tick_lines(capsys) -> list[str]:
    """Only the routine tick lines, not FIRING / started / stopped."""
    return [l for l in capsys.readouterr().out.splitlines() if "tick level=" in l]


class TestShouldLogTick:
    def test_unchanged_state_is_silent(self):
        sig = TickSignature("low", 600.0, 0)
        assert should_log_tick(sig, sig, ok=False) is False

    def test_first_tick_logs_a_baseline(self):
        assert should_log_tick(None, TickSignature("low", 600.0, 0), ok=False) is True

    @pytest.mark.parametrize(
        "changed",
        [
            TickSignature("medium", 600.0, 0),   # level
            TickSignature("low", 900.0, 0),      # next threshold
            TickSignature("low", 600.0, 3),      # daily count
        ],
    )
    def test_changed_state_logs(self, changed):
        assert should_log_tick(TickSignature("low", 600.0, 0), changed, ok=False) is True

    def test_about_to_trigger_always_logs(self):
        sig = TickSignature("low", 600.0, 0)
        assert should_log_tick(sig, sig, ok=True) is True

    def test_debug_restores_the_firehose(self):
        sig = TickSignature("low", 600.0, 0)
        assert should_log_tick(sig, sig, ok=False, debug=True) is True


class TestWatcherTickLogging:
    @pytest.mark.asyncio
    async def test_steady_state_ticks_are_silent(self, capsys):
        harness = WatcherHarness()
        await harness.watcher.poll_once()      # first tick logs the baseline
        capsys.readouterr()

        for _ in range(5):
            harness.clock.advance(15)
            await harness.watcher.poll_once()

        assert tick_lines(capsys) == []

    @pytest.mark.asyncio
    async def test_level_change_logs_a_tick(self, capsys):
        harness = WatcherHarness()
        await harness.watcher.poll_once()
        capsys.readouterr()

        harness.level = "medium"
        harness.clock.advance(15)
        await harness.watcher.poll_once()

        lines = tick_lines(capsys)
        assert len(lines) == 1
        assert "level=medium" in lines[0]

    @pytest.mark.asyncio
    async def test_daily_count_change_logs_a_tick(self, capsys):
        harness = WatcherHarness()
        await harness.watcher.poll_once()
        capsys.readouterr()

        harness.count = 3
        harness.clock.advance(15)
        await harness.watcher.poll_once()

        lines = tick_lines(capsys)
        assert len(lines) == 1
        assert "daily=3" in lines[0]

    @pytest.mark.asyncio
    async def test_about_to_trigger_logs_a_tick(self, capsys):
        harness = WatcherHarness()
        await harness.watcher.poll_once()
        capsys.readouterr()

        harness.clock.advance(24 * 60)          # past any low-tier threshold
        await harness.watcher.poll_once()

        lines = tick_lines(capsys)
        assert len(lines) == 1
        assert "-> OK" in lines[0]
        assert harness.fired == ["idle"]

    @pytest.mark.asyncio
    async def test_env_debug_restores_every_tick(self, capsys, monkeypatch):
        monkeypatch.setenv(TICK_DEBUG_ENV, "1")
        harness = WatcherHarness()

        for _ in range(3):
            harness.clock.advance(15)
            await harness.watcher.poll_once()

        assert len(tick_lines(capsys)) == 3
