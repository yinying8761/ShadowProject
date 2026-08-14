"""
Tests for the retry helper — pure exponential-backoff retry (Workflow E, ticket #15).

Covers retryable/non-retryable classification, exponential backoff,
exhaustion, and injectable sleep (no real time, no real network).
"""

import pytest

from services.retry import retry, is_retryable


class FakeHTTPError(Exception):
    """Stand-in for SDK errors that carry an HTTP status_code."""

    def __init__(self, status_code: int):
        self.status_code = status_code


class RecordingSleep:
    """Fake sleep that records the requested delays instead of sleeping."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class TestRetry:
    @pytest.mark.asyncio
    async def test_returns_on_first_success(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            return "ok"

        sleep = RecordingSleep()
        result = await retry(fn, sleep=sleep)

        assert result == "ok"
        assert calls == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_retries_transient_then_succeeds(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("slow")
            return "ok"

        sleep = RecordingSleep()
        result = await retry(fn, max_retries=2, sleep=sleep)

        assert result == "ok"
        assert calls == 2
        assert sleep.delays == [1.0]

    @pytest.mark.asyncio
    async def test_backoff_is_exponential(self):
        async def fn():
            raise TimeoutError("always slow")

        sleep = RecordingSleep()
        with pytest.raises(TimeoutError):
            await retry(fn, max_retries=5, sleep=sleep)

        assert sleep.delays == [1.0, 2.0, 4.0, 8.0, 16.0]

    @pytest.mark.asyncio
    async def test_exhausts_and_raises_last_error(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            raise FakeHTTPError(503)

        sleep = RecordingSleep()
        with pytest.raises(FakeHTTPError):
            await retry(fn, max_retries=2, sleep=sleep)

        assert calls == 3  # 1 initial + 2 retries
        assert sleep.delays == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            raise FakeHTTPError(400)

        sleep = RecordingSleep()
        with pytest.raises(FakeHTTPError):
            await retry(fn, max_retries=5, sleep=sleep)

        assert calls == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_custom_retryable_predicate(self):
        class WeirdError(Exception):
            pass

        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise WeirdError()
            return "ok"

        sleep = RecordingSleep()
        result = await retry(
            fn,
            max_retries=3,
            retryable=lambda e: isinstance(e, WeirdError),
            sleep=sleep,
        )

        assert result == "ok"
        assert calls == 3

class TestIsRetryable:
    def test_retryable_status_codes(self):
        assert is_retryable(FakeHTTPError(429)) is True
        assert is_retryable(FakeHTTPError(500)) is True
        assert is_retryable(FakeHTTPError(503)) is True

    def test_non_retryable_status_codes(self):
        assert is_retryable(FakeHTTPError(400)) is False
        assert is_retryable(FakeHTTPError(401)) is False
        assert is_retryable(FakeHTTPError(404)) is False

    def test_network_errors_retryable(self):
        assert is_retryable(TimeoutError("timeout")) is True
        assert is_retryable(ConnectionError("refused")) is True

    def test_other_errors_not_retryable(self):
        assert is_retryable(ValueError("bad")) is False
        assert is_retryable(KeyError("missing")) is False
