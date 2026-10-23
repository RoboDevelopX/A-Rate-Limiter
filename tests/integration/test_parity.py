"""The Lua scripts and the Python algorithms must make identical decisions.

Redis is compared with the pure Python algorithm run without expiry, because Redis keeps
keys for ``ttl_padding`` (60 s here) of real time, far longer than any example runs.
"""

from typing import Any
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from redis.asyncio import Redis

from mlratelimit import Algorithm, Limiter, ManualClock, Quota
from mlratelimit.algorithms import ALGORITHMS
from mlratelimit.backends.redis import RedisBackend
from tests.helpers import T0, Simulator

pytestmark = pytest.mark.redis

quotas = st.builds(
    Quota, limit=st.integers(1, 20), period=st.sampled_from([0.5, 1.0, 2.0, 4.0, 8.0])
)
steps = st.lists(
    # Negative steps simulate a clock stepping back.
    st.tuples(st.integers(-64, 16 * 64).map(lambda n: n / 64), st.integers(1, 25)),
    max_size=40,
)


@pytest.mark.parametrize("algorithm", ALGORITHMS.values(), ids=ALGORITHMS.keys())
@settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(quota=quotas, ops=steps)
async def test_lua_matches_python(
    redis_client: Redis, algorithm: Algorithm[Any], quota: Quota, ops: list[tuple[float, int]]
) -> None:
    clock = ManualClock(T0)
    python = Simulator(algorithm, quota, expire=False)
    redis = Limiter(algorithm, quota, RedisBackend(redis_client, clock=clock, ttl_padding=60))
    key = uuid4().hex  # hypothesis reuses the fixture across examples
    for dt, cost in ops:
        clock.advance(dt)
        assert await redis.check(key, cost) == python.check(clock.now(), cost)
