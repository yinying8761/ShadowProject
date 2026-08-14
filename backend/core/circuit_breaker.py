"""
CircuitBreaker — pure state machine for per-tool fault isolation.

States: CLOSED (normal) → OPEN (rejecting) → HALF_OPEN (probing).

Transitions:
  CLOSED     --failure_count >= threshold--> OPEN
  OPEN       --cooldown elapsed--> HALF_OPEN (on the next allow_request)
  HALF_OPEN  --probe success--> CLOSED
  HALF_OPEN  --probe failure--> OPEN (re-timed)

The breaker is deliberately free of I/O, DB, and HTTP: it only tracks
state and time via an injectable clock, so it can be unit-tested purely.
"""

from __future__ import annotations

import time
from typing import Callable

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

DEFAULT_THRESHOLD = 5
DEFAULT_OPEN_TIMEOUT = 60.0


class CircuitBreaker:
    """A single-circuit breaker (one instance per tool name)."""

    def __init__(
        self,
        threshold: int = DEFAULT_THRESHOLD,
        open_timeout: float = DEFAULT_OPEN_TIMEOUT,
        *,
        clock: Callable[[], float] | None = None,
    ):
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if open_timeout < 0:
            raise ValueError("open_timeout must be >= 0")
        self.threshold = threshold
        self.open_timeout = open_timeout
        self._clock = clock if clock is not None else time.monotonic
        self._state = CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        """Current state: CLOSED / OPEN / HALF_OPEN."""
        return self._state

    @property
    def failure_count(self) -> int:
        """Consecutive failures since the last success (CLOSED phase)."""
        return self._failure_count

    def allow_request(self) -> bool:
        """Whether a request may proceed right now.

        CLOSED → always allow.  OPEN → allow exactly once after the
        cooldown elapses (transitioning to HALF_OPEN as the probe), then
        reject until the probe resolves.  HALF_OPEN → reject (only the
        single probe is allowed).
        """
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            if self._clock() - self._opened_at >= self.open_timeout:
                self._state = HALF_OPEN
                return True
            return False
        return False  # HALF_OPEN — probe already dispatched

    def record_success(self) -> None:
        """Report a successful request."""
        if self._state == CLOSED:
            self._failure_count = 0
        elif self._state == HALF_OPEN:
            self._close()

    def record_failure(self) -> None:
        """Report a failed request."""
        if self._state == CLOSED:
            self._failure_count += 1
            if self._failure_count >= self.threshold:
                self._open()
        elif self._state == HALF_OPEN:
            # Probe failed → reopen immediately (re-time the cooldown).
            self._open()

    def reset(self) -> None:
        """Force the breaker back to CLOSED with a clean counter."""
        self._close()

    def _close(self) -> None:
        self._state = CLOSED
        self._failure_count = 0
        self._opened_at = None

    def _open(self) -> None:
        self._state = OPEN
        self._opened_at = self._clock()
