"""What to do when the backend (Redis) is slow or down."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any

from redis.exceptions import RedisError

from mlratelimit.algorithms.base import Algorithm
from mlratelimit.backends.base import Backend
from mlratelimit.clock import Clock, SystemClock
from mlratelimit.types import LimitRequest, RateLimitResult

logger = logging.getLogger("mlratelimit.resilience")

BACKEND_ERRORS: tuple[type[BaseException], ...] = (RedisError, OSError, TimeoutError)
"""Exceptions that mean "the backend is unavailable", as opposed to a bug in our code."""


class FailureMode(StrEnum):
    """Decision to return when the backend cannot answer."""

    OPEN = "open"
    """Allow the request: availability first, the API is briefly unprotected."""
    CLOSED = "closed"
    """Reject the request: protection first, the API is briefly unavailable."""


class BreakerState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Stops calling a failing backend for a while, then probes it.

    ``closed``: calls go through; ``failure_threshold`` consecutive failures open it.
    ``open``: calls are skipped for ``reset_timeout`` seconds.
    ``half_open``: one probe call goes through; success closes it, failure opens it again.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 5.0,
        clock: Clock | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._clock: Clock = clock or SystemClock()
        self._failures = 0
        self._opened_at = 0.0
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        """Current state, moving from open to half-open once the timeout has passed."""
        if (
            self._state is BreakerState.OPEN
            and self._clock.now() - self._opened_at >= self.reset_timeout
        ):
            self._transition(BreakerState.HALF_OPEN)
        return self._state

    def allow(self) -> bool:
        """Whether a call may go to the backend now. In half-open, only the first may."""
        state = self.state
        if state is BreakerState.HALF_OPEN:
            # Let exactly one probe through; others are short-circuited until it reports.
            self._opened_at = self._clock.now()
            self._state = BreakerState.OPEN
            return True
        return state is BreakerState.CLOSED

    def record_success(self) -> None:
        """A call succeeded: reset the failure count and close."""
        self._failures = 0
        if self._state is not BreakerState.CLOSED:
            self._transition(BreakerState.CLOSED)

    def record_failure(self) -> None:
        """A call failed: open after enough consecutive failures (or a failed probe)."""
        self._failures += 1
        if self._failures >= self._threshold or self._state is not BreakerState.CLOSED:
            self._opened_at = self._clock.now()
            self._transition(BreakerState.OPEN)

    def _transition(self, state: BreakerState) -> None:
        if state is not self._state:
            logger.warning(
                "circuit breaker state changed",
                extra={"from_state": str(self._state), "to_state": str(state)},
            )
        self._state = state


class FailureGuard:
    """Runs backend calls with a timeout and a circuit breaker.

    Args:
        timeout: Seconds before a call is abandoned. Keep it well under your API's latency
            budget; a rate limit check should take about a millisecond.
        breaker: Shared breaker; one per backend.
        on_failure: Called with a reason (``"timeout"``, ``"error"``, ``"circuit_open"``),
            e.g. to count errors in metrics.
    """

    def __init__(
        self,
        *,
        timeout: float = 0.05,
        breaker: CircuitBreaker | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.breaker = breaker or CircuitBreaker()
        self._on_failure = on_failure

    async def call[T](self, operation: Callable[[], Awaitable[T]]) -> T | None:
        """Return the operation's result, or ``None`` if the backend is unavailable."""
        if not self.breaker.allow():
            self._failed("circuit_open")
            return None
        try:
            async with asyncio.timeout(self.timeout):
                result = await operation()
        except TimeoutError:
            self.breaker.record_failure()
            self._failed("timeout")
            return None
        except BACKEND_ERRORS as exc:
            self.breaker.record_failure()
            self._failed("error", exc)
            return None
        self.breaker.record_success()
        return result

    def _failed(self, reason: str, exc: BaseException | None = None) -> None:
        logger.warning(
            "rate limit backend unavailable",
            extra={"reason": reason, "error": repr(exc) if exc else None},
        )
        if self._on_failure is not None:
            self._on_failure(reason)


class ResilientBackend:
    """A backend wrapper that never raises for backend outages.

    When the inner backend fails, times out, or the breaker is open, it returns fallback
    results: allowed for :attr:`FailureMode.OPEN`, denied for :attr:`FailureMode.CLOSED`
    (with ``retry_after`` set to the breaker's reset timeout).
    """

    def __init__(
        self,
        backend: Backend,
        *,
        mode: FailureMode = FailureMode.OPEN,
        guard: FailureGuard | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._backend = backend
        self.mode = mode
        self.guard = guard or FailureGuard()
        self._clock: Clock = clock or SystemClock()

    async def apply(
        self, algorithm: Algorithm[Any], requests: Sequence[LimitRequest]
    ) -> list[RateLimitResult]:
        """See :meth:`mlratelimit.backends.base.Backend.apply`."""
        results = await self.guard.call(lambda: self._backend.apply(algorithm, requests))
        if results is not None:
            return results
        return [self._fallback(request) for request in requests]

    def _fallback(self, request: LimitRequest) -> RateLimitResult:
        now = self._clock.now()
        limit = request.quota.limit
        if self.mode is FailureMode.OPEN:
            return RateLimitResult(True, limit, limit, 0.0, now)
        wait = self.guard.breaker.reset_timeout
        return RateLimitResult(False, limit, 0, wait, now + wait)
