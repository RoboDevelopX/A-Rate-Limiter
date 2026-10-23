import pytest

from mlratelimit import FixedWindow, ManualClock, Quota
from tests.helpers import T0, MakeLimiter

QUOTA = Quota(limit=5, period=10)


async def test_allows_up_to_limit_then_denies(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    results = [await limiter.check("k") for _ in range(6)]
    assert [r.allowed for r in results] == [True] * 5 + [False]
    assert [r.remaining for r in results] == [4, 3, 2, 1, 0, 0]
    assert all(r.limit == 5 for r in results)


async def test_denied_reports_time_to_window_end(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    clock.advance(3)
    for _ in range(5):
        await limiter.check("k")
    denied = await limiter.check("k")
    assert not denied.allowed
    assert denied.retry_after == pytest.approx(7.0)
    assert denied.reset_at == pytest.approx(T0 + 10)


async def test_window_rollover_resets_count(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    for _ in range(5):
        await limiter.check("k")
    clock.advance(9.999)
    assert not (await limiter.check("k")).allowed
    clock.advance(0.001)  # exactly at the boundary: a new window
    result = await limiter.check("k")
    assert result.allowed
    assert result.remaining == 4
    assert result.reset_at == pytest.approx(T0 + 20)


async def test_boundary_burst_lets_twice_the_limit_through(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    """The documented flaw: 2 x limit within one second across a boundary."""
    limiter = make_limiter(FixedWindow(), QUOTA)
    clock.advance(9.5)
    first = [await limiter.check("k") for _ in range(5)]
    clock.advance(1.0)
    second = [await limiter.check("k") for _ in range(5)]
    assert all(r.allowed for r in first + second)


async def test_cost_greater_than_one(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    assert (await limiter.check("k", cost=3)).remaining == 2
    denied = await limiter.check("k", cost=3)
    assert not denied.allowed
    assert denied.remaining == 2  # a denied request consumes nothing
    assert (await limiter.check("k", cost=2)).allowed


async def test_cost_above_limit_can_never_succeed(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    result = await limiter.check("k", cost=6)
    assert not result.allowed
    assert result.retry_after is None
    assert result.remaining == 5


async def test_keys_are_independent(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    for _ in range(5):
        await limiter.check("a")
    assert not (await limiter.check("a")).allowed
    assert (await limiter.check("b")).allowed


async def test_clock_stepping_back_does_not_reset_window(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(FixedWindow(), QUOTA)
    clock.advance(10.5)
    for _ in range(5):
        await limiter.check("k")
    clock.advance(-1.0)  # back into the previous window
    assert not (await limiter.check("k")).allowed
