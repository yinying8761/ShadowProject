"""
Proactive companion watcher.

Supports two trigger sources:
- idle: consumes the user's configurable daily quota
- scheduled: fixed local-time slots that do not consume the daily quota
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable


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
    ):
        self._get_level = get_level
        self._get_daily_limit = get_daily_limit
        self._get_schedule_enabled = get_schedule_enabled
        self._load_state = load_state
        self._save_state = save_state
        self._on_trigger = on_trigger
        self._name = name
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        now = time.monotonic()
        self._last_activity = now
        self._last_proactive = 0.0
        self._next_threshold = self._roll_threshold("medium")

        self._state_date = ""
        self._daily_count = 0
        self._scheduled_slots_fired: set[str] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())
            print(f"[{self._name}] started", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            print(f"[{self._name}] stopped", flush=True)

    def reset_idle(self) -> None:
        self._last_activity = time.monotonic()
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

    def _should_trigger_idle(self, level: str) -> tuple[bool, str]:
        if level == "off":
            return False, "off"
        tier = TIERS.get(level)
        if tier is None or tier.silence_max <= 0:
            return False, "no-tier"

        now = time.monotonic()
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
        self._last_proactive = time.monotonic()
        self._scheduled_slots_fired.add(slot_name)
        await self._save_state(self._state_date, self._daily_count, self._scheduled_slots_fired)
        print(f"[{self._name}] FIRING scheduled slot={slot_name}", flush=True)
        await self._on_trigger("scheduled")

    async def _fire_idle(self) -> None:
        self._last_proactive = time.monotonic()
        self._daily_count += 1
        await self._save_state(self._state_date, self._daily_count, self._scheduled_slots_fired)
        print(f"[{self._name}] FIRING idle count={self._daily_count}", flush=True)
        await self._on_trigger("idle")
        self._next_threshold = self._roll_threshold(self._get_level())

    async def _loop(self) -> None:
        try:
            await self._sync_state()
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    pass

                await self._sync_state()
                slot_name = self._scheduled_slot_due()
                if slot_name:
                    try:
                        await self._fire_scheduled(slot_name)
                    except Exception as e:
                        print(f"[{self._name}] scheduled trigger error: {e}", flush=True)
                    continue

                level = self._get_level()
                ok, reason = self._should_trigger_idle(level)
                now = time.monotonic()
                idle = now - self._last_activity
                print(
                    f"[{self._name}] tick level={level} idle={idle:.0f}s "
                    f"thresh={self._next_threshold:.0f}s daily={self._daily_count} -> {reason}",
                    flush=True,
                )
                if not ok:
                    continue

                try:
                    await self._fire_idle()
                except Exception as e:
                    print(f"[{self._name}] idle trigger error: {e}", flush=True)
        except asyncio.CancelledError:
            pass
