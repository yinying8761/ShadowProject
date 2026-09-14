"""
Retry — exponential-backoff retry helper for transient failures.

Pure and free of any specific SDK/HTTP client: classification is driven
by a ``status_code`` attribute when present, otherwise by exception type.
Callers (LLM service, tool runtime) inject their own ``retryable`` predicate
or ``sleep`` when they need SDK-specific semantics or fast tests.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

# Sync callback invoked just before each backoff retry: (attempt, max_retries, exc).
RetryCallback = Callable[[int, int, Exception], None]

# Errors without an HTTP status_code that we treat as transient by default.
_DEFAULT_TRANSIENT = (TimeoutError, ConnectionError)


def is_retryable(exc: Exception) -> bool:
    """Classify an exception as worth retrying.

    HTTP-style errors (those exposing a ``status_code``): retryable when
    the status is 429 (rate limit) or 5xx (server error); other 4xx
    (401 auth, 400 bad request, 404 not found) are not retryable.

    Exceptions without a ``status_code`` are treated as transient when they
    are timeouts or connection errors.
    """
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(exc, _DEFAULT_TRANSIENT)


async def retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 5,
    base_delay: float = 1.0,
    retryable: Callable[[Exception], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: RetryCallback | None = None,
) -> T:
    """Call *fn*, retrying transient failures with exponential backoff.

    Makes up to ``max_retries`` retries (i.e. ``max_retries + 1`` total
    attempts), sleeping ``base_delay * 2**attempt`` seconds between them.
    *retryable* decides which exceptions warrant a retry and defaults to
    :func:`is_retryable`.  When an exception is non-retryable, or the
    retries are exhausted, the last exception is re-raised.

    *on_retry*, when given, is called synchronously just before each actual
    backoff retry with ``(attempt, max_retries, exc)`` where *attempt* is the
    upcoming retry number (1-based).  It is never called on success, on a
    non-retryable error, or once retries are exhausted.
    """
    predicate = retryable or is_retryable
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt == max_retries or not predicate(exc):
                raise
            if on_retry is not None:
                on_retry(attempt + 1, max_retries, exc)
            await sleep(base_delay * (2 ** attempt))
