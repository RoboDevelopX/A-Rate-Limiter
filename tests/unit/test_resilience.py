import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from mlratelimit import Algorithm, FixedWindow, Limiter, ManualClock, MemoryBackend, Quota
from mlratelimit.resilience import (
    BreakerState,
    CircuitBreaker,
    FailureGuard,
    FailureMode,
    ResilientBackend,
)
from mlratelimit.types import LimitRequest, RateLimitResult
from tests.helpers import T0


class BrokenBackend:
    """Fails every call with the given exception, or hangs."""

    def __init__(self, exc: BaseException | None = None, *, hang: bool = False) -> None:
        self.exc = exc
        self.hang = hang
        self.calls = 0

    async def apply(
        self, algorithm: Algorithm[Any], requests: Sequence[LimitRequest]
    ) -> list[RateLimitResult]:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(10)
        assert self.exc is not None
        raise self.exc


def test_breaker_opens_after_threshold_and_probes_after_timeout(clock: ManualClock) -> None:
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=5, clock=clock)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()
    clock.advance(5)
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow()  # the single probe
    assert not breaker.allow()  # everyone else waits for it
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow()


def test_failed_probe_reopens(clock: ManualClock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=5, clock=clock)
    breaker.record_failure()
    clock.advance(5)
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    clock.advance(4.9)
    assert not breaker.allow()


def test_success_resets_the_failure_count(clock: ManualClock) -> None:
    breaker = CircuitBreaker(failure_threshold=2, clock=clock)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_breaker_rejects_bad_threshold() -> None:
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(failure_threshold=0)


async def test_guard_reports_errors_then_short_circuits(clock: ManualClock) -> None:
    reasons: list[str] = []
    guard = FailureGuard(
        breaker=CircuitBreaker(failure_threshold=2, clock=clock), on_failure=reasons.append
    )
    backend = BrokenBackend(RedisConnectionError("down"))
    for _ in range(4):
        assert await guard.call(lambda: backend.apply(FixedWindow(), [])) is None
    assert reasons == ["error", "error", "circuit_open", "circuit_open"]
    assert backend.calls == 2  # the open circuit stopped calls from reaching the backend


async def test_guard_times_out_slow_backends() -> None:
    reasons: list[str] = []
    guard = FailureGuard(timeout=0.01, on_failure=reasons.append)
    backend = BrokenBackend(hang=True)
    assert await guard.call(lambda: backend.apply(FixedWindow(), [])) is None
    assert reasons == ["timeout"]


async def test_guard_does_not_hide_programming_errors() -> None:
    guard = FailureGuard()
    backend = BrokenBackend(ValueError("bug"))
    with pytest.raises(ValueError, match="bug"):
        await guard.call(lambda: backend.apply(FixedWindow(), []))


async def test_fail_open_allows_when_backend_is_down(clock: ManualClock) -> None:
    backend = ResilientBackend(BrokenBackend(OSError("refused")), clock=clock)
    limiter = Limiter(FixedWindow(), Quota(limit=5, period=60), backend)
    result = await limiter.check("k")
    assert result == RateLimitResult(True, 5, 5, 0.0, T0)


async def test_fail_closed_denies_until_the_breaker_may_probe(clock: ManualClock) -> None:
    guard = FailureGuard(breaker=CircuitBreaker(reset_timeout=7, clock=clock))
    backend = ResilientBackend(
        BrokenBackend(OSError("refused")), mode=FailureMode.CLOSED, guard=guard, clock=clock
    )
    limiter = Limiter(FixedWindow(), Quota(limit=5, period=60), backend)
    result = await limiter.check("k")
    assert result == RateLimitResult(False, 5, 0, 7.0, T0 + 7)


async def test_healthy_backend_passes_through(clock: ManualClock) -> None:
    backend = ResilientBackend(MemoryBackend(clock), mode=FailureMode.CLOSED)
    limiter = Limiter(FixedWindow(), Quota(limit=1, period=60), backend)
    assert (await limiter.check("k")).allowed
    assert not (await limiter.check("k")).allowed
    assert backend.guard.breaker.state is BreakerState.CLOSED
