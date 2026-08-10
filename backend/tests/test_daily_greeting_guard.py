"""
Tests for daily greeting in-flight guard.

Verifies that concurrent daily_greeting requests are correctly gated:
only one task runs at a time; subsequent requests receive a skip event;
after the first task completes, a new one can start.
"""

import asyncio

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────


class GuardHarness:
    """Minimal reproduction of the in-flight guard pattern used in ws_chat.

    Mirrors the logic added to ``api/chat.py``:
        _daily_greeting_tasks: dict[str, asyncio.Task] = {}

        def handle_message(...):
            if msg_type == "daily_greeting":
                existing = _daily_greeting_tasks.get(conversation_id)
                if existing and not existing.done():
                    send skip; return
                task = asyncio.create_task(handle_daily_greeting())
                _daily_greeting_tasks[conversation_id] = task
                task.add_done_callback(lambda t: ...pop...)
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._run_count = 0
        self._skip_count = 0
        self._skip_reasons: list[str] = []

    # ── Simulated message handler ───────────────────────────────────────

    async def on_daily_greeting(self, conv_id: str, task_body) -> str:
        """Simulate ``handle_message`` receiving a daily_greeting message.

        Returns "running" | "in_flight_skip".
        """
        existing = self._tasks.get(conv_id)
        if existing and not existing.done():
            self._skip_count += 1
            self._skip_reasons.append("in_flight")
            return "in_flight_skip"

        task = asyncio.create_task(self._run(conv_id, task_body))
        self._tasks[conv_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(conv_id, None))
        return "running"

    async def _run(self, conv_id: str, task_body):
        """Simulated handle_daily_greeting."""
        self._run_count += 1
        await task_body()

    async def flush(self) -> None:
        """Wait for all tracked tasks to complete and callbacks to fire.

        ``add_done_callback`` callbacks are scheduled via ``call_soon``,
        so we need two event-loop iterations: one for the task to finish,
        one for the callback to run.
        """
        # Await any in-flight tasks first
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Let call_soon-scheduled done callbacks fire (may need multiple yields)
        for _ in range(3):
            await asyncio.sleep(0)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestInFlightGuard:
    """The guard blocks concurrent requests for the same conversation."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_only_one_runs(self):
        """Two concurrent requests → one runs, one skipped."""
        harness = GuardHarness()

        # A slow task body that we control via an event
        started = asyncio.Event()
        done = asyncio.Event()

        async def slow_body():
            started.set()
            await done.wait()

        # Fire two requests concurrently
        results = await asyncio.gather(
            harness.on_daily_greeting("conv-1", slow_body),
            harness.on_daily_greeting("conv-1", slow_body),
        )

        # One ran, one was skipped
        assert results.count("running") == 1
        assert results.count("in_flight_skip") == 1
        assert harness._run_count == 1
        assert harness._skip_count == 1
        assert harness._skip_reasons == ["in_flight"]

        # Task still tracked while in-flight
        assert "conv-1" in harness._tasks

        # Let the running task finish
        done.set()
        await harness.flush()

        # Cleaned up after completion
        assert "conv-1" not in harness._tasks

    @pytest.mark.asyncio
    async def test_new_request_allowed_after_completion(self):
        """After the first task finishes, a new request creates a fresh task."""
        harness = GuardHarness()

        async def quick_body():
            pass

        # First request
        result1 = await harness.on_daily_greeting("conv-1", quick_body)
        assert result1 == "running"

        # Wait for task and its done callback to complete
        await harness.flush()

        # Second request after completion — should run (not skipped by guard)
        result2 = await harness.on_daily_greeting("conv-1", quick_body)
        assert result2 == "running"
        await harness.flush()
        assert harness._run_count == 2
        assert harness._skip_count == 0

    @pytest.mark.asyncio
    async def test_different_conversations_independent(self):
        """Two conversations can run daily greeting concurrently."""
        harness = GuardHarness()
        started_a = asyncio.Event()
        started_b = asyncio.Event()
        done = asyncio.Event()

        async def body_a():
            started_a.set()
            await done.wait()

        async def body_b():
            started_b.set()
            await done.wait()

        results = await asyncio.gather(
            harness.on_daily_greeting("conv-a", body_a),
            harness.on_daily_greeting("conv-b", body_b),
        )

        # Both should run — different conversations
        assert results == ["running", "running"]
        assert harness._run_count == 2
        assert harness._skip_count == 0
        assert started_a.is_set()
        assert started_b.is_set()

        done.set()

    @pytest.mark.asyncio
    async def test_already_done_task_is_cleaned_up(self):
        """A task that has already completed is not treated as in-flight."""
        harness = GuardHarness()

        async def quick_body():
            pass

        await harness.on_daily_greeting("conv-1", quick_body)
        await harness.flush()

        # The done callback should have removed it
        assert "conv-1" not in harness._tasks

        # A new request should run fresh
        result = await harness.on_daily_greeting("conv-1", quick_body)
        assert result == "running"
        await harness.flush()
        assert harness._run_count == 2
