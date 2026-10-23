import pytest

from mlratelimit import ManualClock, Quota
from mlratelimit.algorithms import LeakyBucket
from tests.helpers import T0, MakeLimiter

# Capacity 4, drains 2 units per second.
QUOTA = Quota(limit=4, period=2)


async def test_empty_bucket_absorbs_a_burst_of_capacity(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(LeakyBucket(), QUOTA)
    results = [await limiter.check("k") for _ in range(5)]
    assert [r.allowed for r in results] == [True] * 4 + [False]
    assert [r.remaining for r in results] == [3, 2, 1, 0, 0]


async def test_drains_at_a_constant_rate(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(LeakyBucket(), QUOTA)
    await limiter.check("k", cost=4)
    denied = await limiter.check("k", cost=3)
    assert denied.retry_after == pytest.approx(1.5)
    clock.advance(1.5)
    assert (await limiter.check("k", cost=3)).allowed


async def test_reset_at_is_when_bucket_is_empty(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(LeakyBucket(), QUOTA)
    result = await limiter.check("k", cost=3)
    assert result.reset_at == pytest.approx(T0 + 1.5)


async def test_level_never_drops_below_empty(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(LeakyBucket(), QUOTA)
    await limiter.check("k")
    clock.advance(3600)
    results = [await limiter.check("k") for _ in range(5)]
    assert [r.allowed for r in results] == [True] * 4 + [False]


async def test_cost_above_capacity_can_never_succeed(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(LeakyBucket(), QUOTA)
    result = await limiter.check("k", cost=5)
    assert not result.allowed
    assert result.retry_after is None


async def test_clock_stepping_back_drains_nothing(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(LeakyBucket(), QUOTA)
    await limiter.check("k", cost=4)
    clock.advance(-10)
    assert not (await limiter.check("k")).allowed
    clock.advance(10)
    assert not (await limiter.check("k")).allowed
