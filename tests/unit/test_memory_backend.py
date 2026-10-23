import asyncio

import pytest

from mlratelimit import FixedWindow, Limiter, ManualClock, MemoryBackend, Quota, TokenBucket
from mlratelimit.types import LimitRequest
from tests.helpers import T0


async def test_expired_entries_are_evicted(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    limiter = Limiter(FixedWindow(), Quota(limit=5, period=10), backend)
    for i in range(1000):
        await limiter.check(f"user-{i}")
    assert len(backend) == 1000
    clock.advance(10)  # every window has ended
    await limiter.check("fresh")
    assert len(backend) == 1


async def test_state_survives_until_it_expires(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    limiter = Limiter(TokenBucket(), Quota(limit=2, period=10), backend)
    await limiter.check("k", cost=2)
    clock.advance(9.9)
    assert not (await limiter.check("k", cost=2)).allowed
    assert len(backend) == 1
    clock.advance(0.1)  # bucket is full again: the entry carries no information any more
    assert (await limiter.check("k", cost=2)).allowed


async def test_batch_is_all_or_nothing(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    small, large = Quota(limit=1, period=10), Quota(limit=100, period=10)
    batch = [LimitRequest("small", small, 1), LimitRequest("large", large, 50)]
    first = await backend.apply(FixedWindow(), batch)
    assert [r.allowed for r in first] == [True, True]
    second = await backend.apply(FixedWindow(), batch)
    # "small" is exhausted, so "large" must not be charged either.
    assert [r.allowed for r in second] == [False, True]
    third = await backend.apply(FixedWindow(), [LimitRequest("large", large, 50)])
    assert third[0].allowed
    assert third[0].remaining == 0


async def test_duplicate_keys_in_a_batch_are_rejected(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    quota = Quota(limit=1, period=1)
    with pytest.raises(ValueError, match="duplicate keys"):
        await backend.apply(FixedWindow(), [LimitRequest("k", quota, 1)] * 2)


async def test_many_coroutines_on_one_key_never_exceed_the_limit(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    limiter = Limiter(TokenBucket(), Quota(limit=50, period=60), backend)
    results = await asyncio.gather(*(limiter.check("hot") for _ in range(1000)))
    assert sum(r.allowed for r in results) == 50


async def test_defaults_to_system_clock() -> None:
    backend = MemoryBackend()
    limiter = Limiter(FixedWindow(), Quota(limit=1, period=3600), backend)
    result = await limiter.check("k")
    assert result.allowed
    assert result.reset_at > T0
