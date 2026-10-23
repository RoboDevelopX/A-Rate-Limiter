import pytest

from mlratelimit import ManualClock, Quota
from mlratelimit.algorithms import SlidingLog
from tests.helpers import T0, MakeLimiter

QUOTA = Quota(limit=3, period=10)


async def test_allows_up_to_limit_then_denies(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    results = [await limiter.check("k") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]


async def test_entries_expire_exactly_one_period_later(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    await limiter.check("k")  # t=0
    clock.advance(4)
    await limiter.check("k", cost=2)  # t=4
    denied = await limiter.check("k")
    assert denied.retry_after == pytest.approx(6.0)  # the t=0 entry frees one unit at t=10
    clock.advance(5.999)
    assert not (await limiter.check("k")).allowed
    clock.advance(0.001)  # t=10
    assert (await limiter.check("k")).allowed


async def test_no_boundary_burst(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    clock.advance(9.5)
    for _ in range(3):
        await limiter.check("k")
    clock.advance(1.0)  # past a 10 s "boundary": a fixed window would reset here
    assert not (await limiter.check("k")).allowed


async def test_retry_after_waits_for_enough_units_to_free(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    for _ in range(3):
        await limiter.check("k")
        clock.advance(1)
    denied = await limiter.check("k", cost=2)  # needs the t=0 and t=1 entries gone
    assert denied.retry_after == pytest.approx(8.0)
    assert denied.reset_at == pytest.approx(T0 + 12)


async def test_cost_greater_than_one_and_above_limit(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    assert (await limiter.check("k", cost=2)).remaining == 1
    assert not (await limiter.check("k", cost=2)).allowed
    never = await limiter.check("k", cost=4)
    assert not never.allowed
    assert never.retry_after is None


async def test_empty_log_resets_now(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    result = await limiter.check("k", cost=4)
    assert result.reset_at == T0
    assert result.remaining == 3


async def test_clock_stepping_back_keeps_entries(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingLog(), QUOTA)
    clock.advance(9)
    for _ in range(3):
        await limiter.check("k")
    clock.advance(-5)
    assert not (await limiter.check("k")).allowed
