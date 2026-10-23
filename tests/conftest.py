from typing import Any

import pytest

from mlratelimit import Algorithm, Limiter, ManualClock, MemoryBackend, Quota
from mlratelimit.backends.base import Backend
from tests.helpers import T0, MakeLimiter


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T0)


@pytest.fixture(params=["memory"])
def backend(request: pytest.FixtureRequest, clock: ManualClock) -> Backend:
    assert request.param == "memory"
    return MemoryBackend(clock)


@pytest.fixture
def make_limiter(backend: Backend) -> MakeLimiter:
    def make(algorithm: Algorithm[Any], quota: Quota) -> Limiter:
        return Limiter(algorithm, quota, backend)

    return make
