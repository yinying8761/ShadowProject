"""
Proactive companion watcher.

Supports two trigger sources:
- idle: consumes the user's configurable daily quota
- scheduled: fixed local-time slots that do not consume the daily quota
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, NamedTuple


TriggerCallback = Callable[[str], Awaitable[None]]
LevelGetter = Callable[[], str]
DailyLimitGetter = Callable[[], int]
ScheduleEnabledGetter = Callable[[], bool]
StateLoader = Callable[[], Awaitable[tuple[str, int, set[str]]]]
StateSaver = Callable[[str, int, set[str]], Awaitable[None]]


@dataclass
class TierConfig:
    silence_min: float
    silence_max: float
    cooldown: float


TIERS: dict[str, TierConfig] = {
    "off": TierConfig(0, 0, 0),
    "low": TierConfig(8 * 60, 15 * 60, 15 * 60),
    "medium": TierConfig(4 * 60, 8 * 60, 8 * 60),
    "high": TierConfig(2 * 60, 5 * 60, 5 * 60),
}

SCHEDULED_SLOTS = {
    "07:00": "morning",
    "12:00": "noon",
    "18:00": "evening",
}

POLL_INTERVAL = 15.0

# Set PROACTIVE_TICK_DEBUG=1 to get every routine tick back (the firehose).
TICK_DEBUG_ENV = "PROACTIVE_TICK_DEBUG"


class TickSignature(NamedTuple):
    """The tick-line fields whose change makes a tick worth logging.

    ``idle`` and the free-text reason are deliberately excluded — they change
    on every tick, which is exactly the noise this silences.
    """

    level: str
    next_threshold: float
    daily_count: int


def should_log_tick(
    previous: TickSignature | None,
    current: TickSignature,
    ok: bool,
    *,
    debug: bool = False,
) -> bool:
    """Whether a routine tick deserves a line.

    A tick every ``POLL_INTERVAL`` is ~240 lines/hour of ``idle=X<thresh=Y``
    that buries the lines worth reading, so it is logged only when the state
    it reports changed since the previous tick, when the watcher is about to
    trigger (*ok*), or when *debug* restores the firehose.

    *previous* is None on a run's first tick, which logs one baseline line
    saying what the watcher is doing while it stays quiet.
    """
    return debug or ok or current != previous


class ProactiveWatcher:
    def __init__(
        self,
        get_level: LevelGetter,
        get_daily_limit: DailyLimitGetter,
        get_schedule_enabled: ScheduleEnabledGetter,
        load_state: StateLoader,
        save_state: StateSaver,
        on_trigger: TriggerCallback,
        name: str = "watcher",
        *,
        clock: Callable[[], float] | None = None,
    ):
        self._get_level = get_level
        self._get_daily_limit = get_daily_limit
        self._get_schedule_enabled = get_schedule_enabled
        self._load_state = load_state
        self._save_state = save_state
        self._on_trigger = on_trigger
        self._name = name
        self._clock = clock if clock is not None else time.monotonic
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self._last_activity = self._clock()
        self._last_proactive = 0.0
        self._next_threshold = self._roll_threshold("medium")
        self._tick_signature: TickSignature | None = None

        self._state_date = ""
        self._daily_count = 0
        self._scheduled_slots_fired: set[str] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._tick_signature = None     # a fresh run logs its own baseline
            self._task = asyncio.create_task(self._loop())
            print(f"[{self._name}] started", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            print(f"[{self._name}] stopped", flush=True)

    def reset_idle(self) -> None:
        self._last_activity = self._clock()
        level = self._get_level()
        self._next_threshold = self._roll_threshold(level)
        print(
            f"[{self._name}] idle reset; level={level} next_threshold={self._next_threshold:.0f}s",
            flush=True,
        )

    def _roll_threshold(self, level: str) -> float:
        tier = TIERS.get(level, TIERS["off"])
        if tier.silence_max <= 0:
            return float("inf")
        return random.uniform(tier.silence_min, tier.silence_max)

    async def _sync_state(self) -> None:
        state_date, daily_count, scheduled_slots = await self._load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        if state_date != today:
            self._state_date = today
            self._daily_count = 0
            self._scheduled_slots_fired = set()
            await self._save_state(self._state_date, self._daily_count, self._scheduled_slots_fired)
            return

        self._state_date = state_date
        self._daily_count = daily_count
        self._scheduled_slots_fired = set(scheduled_slots)

    def _scheduled_slot_due(self) -> str | None:
        if not self._get_schedule_enabled():
            return None

        now = datetime.now()
        current_slot = now.strftime("%H:%M")
        slot_name = SCHEDULED_SLOTS.get(current_slot)
        if not slot_name:
            return None
        if slot_name in self._scheduled_slots_fired:
            return None
        return slot_name

    def _should_trigger_idle(self, level: str, now: float) -> tuple[bool, str]:
        if level == "off":
            return False, "off"
        tier = TIERS.get(level)
        if tier is None or tier.silence_max <= 0:
            return False, "no-tier"

        idle = now - self._last_activity
        since_last_proactive = now - self._last_proactive
        daily_limit = max(0, self._get_daily_limit())

        if idle < self._next_threshold:
            return False, f"idle={idle:.0f}<thresh={self._next_threshold:.0f}"
        if self._last_proactive > 0 and since_last_proactive < tier.cooldown:
            return False, f"cooldown {since_last_proactive:.0f}<{tier.cooldown:.0f}"
        if self._daily_count >= daily_limit:
            return False, f"daily-cap {self._daily_count}/{daily_limit}"
        return True, "OK"

    async def _fire_scheduled(self, slot_name: str) -> None:
        self._last_proactive = self._clock()
        self._scheduled_slots_fired.add(slot_name)
        await self._save_state(self._state_date, self._daily_count, self._scheduled_slots_fired)
        print(f"[{self._name}] FIRING scheduled slot={slot_name}", flush=True)
        await self._on_trigger("scheduled")

    async def _fire_idle(self) -> None:
        self._last_proactive = self._clock()
        self._daily_count += 1
        await self._save_state(self._state_date, self._daily_count, self._scheduled_slots_fired)
        print(f"[{self._name}] FIRING idle count={self._daily_count}", flush=True)
        await self._on_trigger("idle")
        self._next_threshold = self._roll_threshold(self._get_level())

    async def poll_once(self) -> None:
        """Run one poll round — the loop calls this every POLL_INTERVAL.

        Split out of the loop so a round can be driven directly with fakes
        and a fake clock instead of waiting out real 15 s intervals.
        """
        await self._sync_state()

        slot_name = self._scheduled_slot_due()
        if slot_name:
            try:
                await self._fire_scheduled(slot_name)
            except Exception as e:
                print(f"[{self._name}] scheduled trigger error: {e}", flush=True)
            return

        level = self._get_level()
        now = self._clock()
        ok, reason = self._should_trigger_idle(level, now)
        idle = now - self._last_activity
        signature = TickSignature(level, self._next_threshold, self._daily_count)
        if should_log_tick(
            self._tick_signature,
            signature,
            ok,
            debug=os.environ.get(TICK_DEBUG_ENV) == "1",
        ):
            print(
                f"[{self._name}] tick level={level} idle={idle:.0f}s "
                f"thresh={self._next_threshold:.0f}s daily={self._daily_count} -> {reason}",
                flush=True,
            )
        self._tick_signature = signature
        if not ok:
            return

        try:
            await self._fire_idle()
        except Exception as e:
            print(f"[{self._name}] idle trigger error: {e}", flush=True)

    async def _loop(self) -> None:
        try:
            await self._sync_state()
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    pass
                await self.poll_once()
        except asyncio.CancelledError:
            pass
