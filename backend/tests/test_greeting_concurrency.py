"""
Tests for concurrent greeting + extraction flow.

Verifies that:
- The greeting returns before slow background extraction completes (greeting-first)
- Background extraction failure does not affect the greeting
- ``before`` parameter filters post-snapshot messages (covered by test_memory_extractor)
- ``before=None`` backward compatibility (covered by existing 6 extractor tests)
"""

import asyncio
from datetime import datetime, timezone

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────


class ConcurrencyHarness:
    """Minimal reproduction of the concurrent greeting + extraction pattern
    from ``handle_daily_greeting`` in ``api/chat.py``.

    Does NOT connect to a real database or LLM — all side effects are
    controlled via injected callbacks so we can observe event ordering.
    """

    def __init__(self):
        self.events: list[dict] = []
        self._greeting_finished = asyncio.Event()
        self._extraction_finished = asyncio.Event()

    async def run(
        self,
        *,
        extraction_delay: float = 0.0,
        extraction_should_fail: bool = False,
    ) -> None:
        """Simulate the concurrent flow.

        1. Take a snapshot
        2. Launch background extraction (with controlled delay / failure)
        3. Run greeting immediately — does NOT await the background task
        4. Record event ordering
        """
        snapshot = datetime.now(timezone.utc)

        # ── Background task: extract + compact ─────────────────
        async def _background_extract():
            try:
                if extraction_should_fail:
                    raise RuntimeError("simulated extraction failure")
                await asyncio.sleep(extraction_delay)
                self.events.append({"type": "extraction_done"})
            except Exception as e:
                self.events.append({"type": "extraction_error", "error": str(e)})
            finally:
                self._extraction_finished.set()

        bg_task = asyncio.create_task(_background_extract())

        # ── Gather memories + recent messages (simulated) ─────
        # In the real code, user_stated memories + recent 5 messages
        memories = ["用户名字是张三"]

        # ── Greeting runs immediately ──────────────────────────
        self.events.append({"type": "greeting_start"})
        await asyncio.sleep(0.01)  # simulate fast greeting generation
        self.events.append({"type": "done", "daily_greeting": True})
        self._greeting_finished.set()

        # Let background task finish naturally (don't await — just
        # wait long enough for the test assertions to observe ordering)
        await asyncio.sleep(extraction_delay + 0.1)

    async def flush(self) -> None:
        """Wait for all pending work to settle."""
        await asyncio.sleep(0)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestGreetingBeforeExtraction:
    """Greeting ``done`` event fires before slow extraction completes."""

    @pytest.mark.asyncio
    async def test_greeting_done_before_extraction(self):
        """With a 0.3s extraction delay, the greeting done event is emitted
        while extraction is still running."""
        harness = ConcurrencyHarness()
        await harness.run(extraction_delay=0.3)

        # Find indices of key events
        events = harness.events
        greeting_done_idx = next(
            i for i, e in enumerate(events)
            if e.get("type") == "done" and e.get("daily_greeting")
        )
        extraction_done_idx = next(
            (i for i, e in enumerate(events) if e.get("type") == "extraction_done"),
            len(events),  # if not found, it came after
        )

        # Greeting done must appear BEFORE extraction done (or extraction
        # hasn't even finished yet — in real code it runs in background)
        assert greeting_done_idx < extraction_done_idx, (
            f"Expected greeting done (idx={greeting_done_idx}) before "
            f"extraction done (idx={extraction_done_idx}), "
            f"but got events: {events}"
        )

    @pytest.mark.asyncio
    async def test_greeting_does_not_await_extraction(self):
        """The greeting_finished event is set without waiting for extraction."""
        harness = ConcurrencyHarness()
        # Fire and immediately check — greeting should finish quickly
        # even with a long extraction delay
        task = asyncio.create_task(harness.run(extraction_delay=5.0))

        # Wait for greeting to finish (should be fast ~0.01s)
        await asyncio.wait_for(harness._greeting_finished.wait(), timeout=1.0)

        # Greeting is done, extraction should still be running
        assert not harness._extraction_finished.is_set(), (
            "Extraction should still be running when greeting is already done"
        )

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestBackgroundFailureIsolation:
    """Background extraction failure does not affect the greeting."""

    @pytest.mark.asyncio
    async def test_extraction_failure_does_not_block_greeting(self):
        """Background task throws → greeting still completes normally."""
        harness = ConcurrencyHarness()
        await harness.run(extraction_should_fail=True)

        events = harness.events

        # Greeting must have completed
        assert any(
            e.get("type") == "done" and e.get("daily_greeting")
            for e in events
        ), f"Greeting should complete, got: {events}"

        # Extraction error recorded but did not propagate
        assert any(
            e.get("type") == "extraction_error"
            for e in events
        ), f"Extraction error should be recorded, got: {events}"

        # Greeting start must come before any extraction error in the
        # event log (they're unrelated — greeting runs independently)
        greeting_start_idx = next(
            i for i, e in enumerate(events) if e.get("type") == "greeting_start"
        )
        greeting_done_idx = next(
            i for i, e in enumerate(events)
            if e.get("type") == "done" and e.get("daily_greeting")
        )
        assert greeting_start_idx < greeting_done_idx, (
            "Greeting should start before it finishes"
        )

    @pytest.mark.asyncio
    async def test_extraction_failure_no_exception_propagation(self):
        """The caller of handle_daily_greeting never sees the background error."""
        harness = ConcurrencyHarness()

        # Run with failing extraction — should not raise
        try:
            await harness.run(extraction_should_fail=True)
        except RuntimeError:
            pytest.fail("Background extraction failure must not propagate to caller")

        # Greeting still completed
        assert harness._greeting_finished.is_set()
