import pytest

from mlratelimit import ManualClock, Quota, TokenBucket
from tests.helpers import T0, MakeLimiter

# Capacity 4, refill 2 tokens per second.
QUOTA = Quota(limit=4, period=2)


async def test_full_bucket_allows_a_burst_of_capacity(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    results = [await limiter.check("k") for _ in range(5)]
    assert [r.allowed for r in results] == [True] * 4 + [False]
    assert [r.remaining for r in results] == [3, 2, 1, 0, 0]


async def test_denied_reports_time_until_enough_tokens(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    for _ in range(4):
        await limiter.check("k")
    denied = await limiter.check("k", cost=3)
    assert denied.retry_after == pytest.approx(1.5)  # 3 tokens at 2 per second
    assert denied.reset_at == pytest.approx(T0 + 2.0)


async def test_refill_is_proportional_to_elapsed_time(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    for _ in range(4):
        await limiter.check("k")
    clock.advance(0.25)  # half a token: still empty
    assert not (await limiter.check("k")).allowed
    clock.advance(0.25)  # one token
    result = await limiter.check("k")
    assert result.allowed
    assert result.remaining == 0
    assert not (await limiter.check("k")).allowed


async def test_refill_is_capped_at_capacity(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    await limiter.check("k")
    clock.advance(3600)
    results = [await limiter.check("k") for _ in range(5)]
    assert [r.allowed for r in results] == [True] * 4 + [False]


async def test_reset_at_is_when_bucket_is_full_again(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    result = await limiter.check("k", cost=3)
    assert result.reset_at == pytest.approx(T0 + 1.5)


async def test_cost_greater_than_one(make_limiter: MakeLimiter, clock: ManualClock) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    assert (await limiter.check("k", cost=3)).remaining == 1
    denied = await limiter.check("k", cost=2)
    assert not denied.allowed
    assert denied.remaining == 1
    clock.advance(0.5)
    assert (await limiter.check("k", cost=2)).allowed


async def test_cost_above_capacity_can_never_succeed(make_limiter: MakeLimiter) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    result = await limiter.check("k", cost=5)
    assert not result.allowed
    assert result.retry_after is None
    assert result.remaining == 4


async def test_clock_stepping_back_adds_no_tokens(
    make_limiter: MakeLimiter, clock: ManualClock
) -> None:
    limiter = make_limiter(TokenBucket(), QUOTA)
    for _ in range(4):
        await limiter.check("k")
    clock.advance(-10)
    assert not (await limiter.check("k")).allowed
    clock.advance(10)  # back to where we were: still no time has really passed
    assert not (await limiter.check("k")).allowed
