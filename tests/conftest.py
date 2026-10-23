import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from hypothesis import settings
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from mlratelimit import Algorithm, Limiter, ManualClock, MemoryBackend, Quota
from mlratelimit.backends.base import Backend
from mlratelimit.backends.redis import RedisBackend
from tests.helpers import REDIS_URL, T0, MakeLimiter

settings.register_profile("default", deadline=None, max_examples=200)
settings.load_profile("default")


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """A clean Redis database. Skips when Redis is down, unless REQUIRE_REDIS=1 (CI)."""
    client = Redis.from_url(REDIS_URL, socket_timeout=2)
    try:
        await client.ping()
    except (RedisConnectionError, OSError) as exc:
        await client.aclose()
        if os.environ.get("REQUIRE_REDIS") == "1":
            pytest.fail(f"Redis required but unreachable at {REDIS_URL}: {exc}")
        pytest.skip(f"Redis unreachable at {REDIS_URL}")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture(params=["memory", pytest.param("redis", marks=pytest.mark.redis)])
def backend(request: pytest.FixtureRequest, clock: ManualClock) -> Backend:
    """Every backend, driven by the same manual clock."""
    if request.param == "memory":
        return MemoryBackend(clock)
    client: Redis = request.getfixturevalue("redis_client")
    return RedisBackend(client, clock=clock, ttl_padding=60)


@pytest.fixture
def make_limiter(backend: Backend) -> MakeLimiter:
    def make(algorithm: Algorithm[Any], quota: Quota) -> Limiter:
        return Limiter(algorithm, quota, backend)

    return make
