import asyncio

import pytest
from redis.asyncio import Redis

from mlratelimit.concurrency import RedisConcurrencyLimiter
from mlratelimit.resilience import FailureGuard, FailureMode

pytestmark = pytest.mark.redis

UNREACHABLE = "redis://localhost:1/0"


def patient() -> FailureGuard:
    """These tests are about slots, not latency: on a slow host ten first calls (new connections,
    script loads) can exceed the 50 ms default and fail open, which would hide the real result."""
    return FailureGuard(timeout=5)


async def test_slots_are_shared_and_capped(redis_client: Redis) -> None:
    worker_a = RedisConcurrencyLimiter(redis_client, guard=patient())
    worker_b = RedisConcurrencyLimiter(redis_client, guard=patient())
    leases = await asyncio.gather(*(w.acquire("gpu", 3) for w in [worker_a, worker_b] * 5))
    granted = [lease for lease in leases if lease is not None]
    assert len(granted) == 3
    await worker_b.release(granted[0])
    assert await worker_a.acquire("gpu", 3) is not None


async def test_unreleased_leases_expire(redis_client: Redis) -> None:
    limiter = RedisConcurrencyLimiter(redis_client, lease_ttl=0.2, guard=patient())
    assert await limiter.acquire("gpu", 1) is not None  # a worker that crashed mid-request
    assert await limiter.acquire("gpu", 1) is None
    await asyncio.sleep(0.3)
    assert await limiter.acquire("gpu", 1) is not None


@pytest.mark.parametrize("mode", list(FailureMode))
async def test_outage_follows_the_failure_mode(mode: FailureMode) -> None:
    client = Redis.from_url(UNREACHABLE, socket_connect_timeout=0.05)
    limiter = RedisConcurrencyLimiter(client, mode=mode, guard=FailureGuard(timeout=0.2))
    lease = await limiter.acquire("gpu", 1)
    if mode is FailureMode.OPEN:
        assert lease is not None
        assert lease.degraded
        await limiter.release(lease)  # no-op, must not raise
    else:
        assert lease is None
    await client.aclose()
