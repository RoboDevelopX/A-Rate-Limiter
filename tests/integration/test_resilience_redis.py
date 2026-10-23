import pytest
from redis.asyncio import Redis

from mlratelimit import FixedWindow, Limiter, Quota, RedisBackend
from mlratelimit.resilience import CircuitBreaker, FailureGuard, FailureMode, ResilientBackend

pytestmark = pytest.mark.redis


async def test_redis_outage_trips_the_breaker_and_fails_open() -> None:
    client = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.05)
    reasons: list[str] = []
    guard = FailureGuard(
        timeout=0.2, breaker=CircuitBreaker(failure_threshold=2), on_failure=reasons.append
    )
    backend = ResilientBackend(RedisBackend(client), mode=FailureMode.OPEN, guard=guard)
    limiter = Limiter(FixedWindow(), Quota(limit=1, period=60), backend)
    results = [await limiter.check("k") for _ in range(3)]
    assert all(r.allowed for r in results)
    assert reasons == ["error", "error", "circuit_open"]
    await client.aclose()


async def test_healthy_redis_is_used_normally(redis_client: Redis) -> None:
    backend = ResilientBackend(RedisBackend(redis_client), mode=FailureMode.CLOSED)
    limiter = Limiter(FixedWindow(), Quota(limit=1, period=60), backend)
    assert (await limiter.check("k")).allowed
    assert not (await limiter.check("k")).allowed
