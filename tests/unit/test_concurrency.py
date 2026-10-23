from mlratelimit.concurrency import Lease, MemoryConcurrencyLimiter


async def test_memory_limiter_caps_slots_per_key() -> None:
    limiter = MemoryConcurrencyLimiter()
    first = await limiter.acquire("gpu", 2)
    second = await limiter.acquire("gpu", 2)
    assert first is not None
    assert second is not None
    assert await limiter.acquire("gpu", 2) is None
    assert await limiter.acquire("other", 2) is not None
    await limiter.release(first)
    assert limiter.in_flight("gpu") == 1
    assert await limiter.acquire("gpu", 2) is not None


async def test_release_is_idempotent() -> None:
    limiter = MemoryConcurrencyLimiter()
    lease = await limiter.acquire("gpu", 1)
    assert lease is not None
    await limiter.release(lease)
    await limiter.release(lease)
    await limiter.release(Lease("never-acquired"))
    assert limiter.in_flight("gpu") == 0
