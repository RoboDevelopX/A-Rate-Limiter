import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import BlockingConnectionPool, Redis

from mlratelimit import Limiter, Quota, TokenBucket
from mlratelimit.algorithms import ALGORITHMS, FixedWindow, SlidingLog
from mlratelimit.backends.redis import RedisBackend, load_script_source
from mlratelimit.types import LimitRequest
from tests.helpers import REDIS_URL

pytestmark = pytest.mark.redis


@pytest.fixture
async def pooled_client(redis_client: Redis) -> AsyncIterator[Redis]:
    """50 real connections; extra coroutines wait for a free one, as in a busy app server."""
    pool = BlockingConnectionPool.from_url(REDIS_URL, max_connections=50, timeout=10)
    client = Redis(connection_pool=pool)
    yield client
    await client.aclose()
    await pool.disconnect()


@pytest.mark.parametrize("name", ALGORITHMS.keys())
async def test_concurrent_checks_on_one_key_never_exceed_the_limit(
    pooled_client: Redis, name: str
) -> None:
    """500 coroutines race on one key over 50 connections; exactly `limit` win."""
    limiter = Limiter(ALGORITHMS[name], Quota(limit=50, period=3600), RedisBackend(pooled_client))
    results = await asyncio.gather(*(limiter.check("hot") for _ in range(500)))
    assert sum(r.allowed for r in results) == 50


async def test_naive_read_modify_write_overadmits(pooled_client: Redis) -> None:
    """Counter-example: GET, decide, SET as separate commands lets races through."""
    limit = 50

    async def naive_check() -> bool:
        used = int(await pooled_client.get("naive") or 0)
        if used + 1 > limit:
            return False
        await pooled_client.set("naive", used + 1)
        return True

    results = await asyncio.gather(*(naive_check() for _ in range(500)))
    assert sum(results) > limit


async def test_uses_redis_server_time(redis_client: Redis) -> None:
    limiter = Limiter(FixedWindow(), Quota(limit=1, period=1), RedisBackend(redis_client))
    seconds, micros = await redis_client.time()
    result = await limiter.check("k")
    server_now = seconds + micros / 1_000_000
    assert server_now < result.reset_at <= server_now + 1.5


async def test_scripts_reload_after_script_flush(redis_client: Redis) -> None:
    limiter = Limiter(FixedWindow(), Quota(limit=2, period=3600), RedisBackend(redis_client))
    assert (await limiter.check("k")).allowed
    await redis_client.script_flush()  # what a Redis restart or failover does
    assert (await limiter.check("k")).allowed
    assert not (await limiter.check("k")).allowed


async def test_keys_get_a_ttl_of_logical_expiry_plus_padding(redis_client: Redis) -> None:
    backend = RedisBackend(redis_client, ttl_padding=1.0)
    limiter = Limiter(TokenBucket(), Quota(limit=10, period=10), backend, name="n")
    await limiter.check("k", cost=5)  # full again in 5 s
    ttl_ms = await redis_client.pttl("rl:{k}:n:token_bucket")
    assert 5_000 < ttl_ms <= 6_000


async def test_expired_keys_disappear_in_real_time(redis_client: Redis) -> None:
    backend = RedisBackend(redis_client, ttl_padding=0.05)
    limiter = Limiter(SlidingLog(), Quota(limit=1, period=0.2), backend)
    assert (await limiter.check("k")).allowed
    assert not (await limiter.check("k")).allowed
    [key] = await redis_client.keys("*")
    assert 0 < await redis_client.pttl(key) <= 251  # period + padding, rounded up to whole ms
    await asyncio.sleep(0.4)
    # Not DBSIZE: Redis removes unread expired keys in a background sweep whose timing it does
    # not promise (it lags on some hosts); a read always sees an expired key as gone.
    assert not await redis_client.exists(key)
    assert (await limiter.check("k")).allowed


async def test_batch_is_all_or_nothing(redis_client: Redis) -> None:
    backend = RedisBackend(redis_client)
    small, large = Quota(limit=1, period=3600), Quota(limit=100, period=3600)
    batch = [LimitRequest("{u}:small", small, 1), LimitRequest("{u}:large", large, 50)]
    assert [r.allowed for r in await backend.apply(FixedWindow(), batch)] == [True, True]
    assert [r.allowed for r in await backend.apply(FixedWindow(), batch)] == [False, True]
    [after] = await backend.apply(FixedWindow(), [LimitRequest("{u}:large", large, 50)])
    assert after.allowed
    assert after.remaining == 0


async def test_duplicate_keys_are_rejected_before_reaching_redis(redis_client: Redis) -> None:
    backend = RedisBackend(redis_client)
    with pytest.raises(ValueError, match="duplicate keys"):
        await backend.apply(FixedWindow(), [LimitRequest("k", Quota(1, 1), 1)] * 2)


def test_script_source_is_prelude_body_and_driver() -> None:
    source = load_script_source("token_bucket")
    assert source.index("redis.call('TIME')") < source.index("local function evaluate")
    assert source.index("local function evaluate") < source.index("commits[i]()")
