import pytest

from mlratelimit import ManualClock, Quota
from mlratelimit.algorithms import SlidingCounter
from tests.helpers import T0, MakeLimiter

QUOTA = Quota(limit=10, period=10)


async def test_allows_up_to_limit_within_a_window(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    results = [await limiter.check("k") for _ in range(11)]
    assert [r.allowed for r in results] == [True] * 10 + [False]
    assert results[9].remaining == 0


async def test_previous_window_decays_linearly(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    await limiter.check("k", cost=10)  # fills window [0, 10)
    clock.advance(10)  # new window, previous weight 1.0: estimate is still 10
    blocked = await limiter.check("k")
    assert not blocked.allowed
    assert blocked.retry_after == pytest.approx(1.0)  # weight 0.9 frees one unit
    clock.advance(5)  # weight 0.5: estimate 5, so 5 more fit
    results = [await limiter.check("k") for _ in range(6)]
    assert [r.allowed for r in results] == [True] * 5 + [False]


async def test_smooths_the_fixed_window_boundary_burst(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    clock.advance(9.5)
    await limiter.check("k", cost=10)
    clock.advance(1.0)  # 0.5 s into the next window, previous weight 0.95
    assert not (await limiter.check("k")).allowed


async def test_retry_after_when_current_window_is_full(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    clock.advance(2)
    await limiter.check("k", cost=8)
    denied = await limiter.check("k", cost=4)
    # Next window starts at t=10 with previous=8; 8 * (1 - x) <= 6 when x = 0.25, i.e. t=12.5.
    assert denied.retry_after == pytest.approx(10.5)
    assert denied.reset_at == pytest.approx(T0 + 20)


async def test_windows_two_apart_forget_everything(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    await limiter.check("k", cost=10)
    clock.advance(20)
    result = await limiter.check("k", cost=10)
    assert result.allowed


async def test_reset_at_when_only_previous_window_has_traffic(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    await limiter.check("k", cost=10)
    clock.advance(10)
    denied = await limiter.check("k", cost=11)
    assert denied.retry_after is None
    assert denied.reset_at == pytest.approx(T0 + 20)


async def test_fresh_key_resets_now(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    result = await limiter.check("k", cost=11)
    assert result.reset_at == T0
    assert result.remaining == 10


async def test_clock_stepping_back_keeps_the_count(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(SlidingCounter(), QUOTA)
    clock.advance(12)
    await limiter.check("k", cost=10)
    clock.advance(-5)
    assert not (await limiter.check("k")).allowed
