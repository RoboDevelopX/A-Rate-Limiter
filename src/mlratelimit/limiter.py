"""The public ``RateLimiter`` interface and its standard implementation."""

from typing import Any, Protocol

from mlratelimit.algorithms.base import Algorithm
from mlratelimit.backends.base import Backend
from mlratelimit.types import LimitRequest, Quota, RateLimitResult


class RateLimiter(Protocol):
    """Anything that can decide whether ``key`` may spend ``cost`` units now."""

    async def check(self, key: str, cost: int = 1) -> RateLimitResult:
        """Check and, if allowed, consume ``cost`` units for ``key``."""
        ...


def validate_cost(cost: int) -> None:
    """Costs are whole units of at least 1 (a request, or a number of LLM tokens)."""
    if cost < 1:
        raise ValueError(f"cost must be >= 1, got {cost}")


class Limiter:
    """One algorithm and one quota, stored in a backend.

    Args:
        algorithm: How to count, e.g. ``TokenBucket()``.
        quota: How much is allowed, e.g. ``Quota(limit=100, period=60)``.
        backend: Where state lives, e.g. ``MemoryBackend()``.
        name: Distinguishes limiters that share keys, e.g. ``"rpm"`` and ``"tpm"``.
        prefix: Namespace for storage keys.
    """

    def __init__(
        self,
        algorithm: Algorithm[Any],
        quota: Quota,
        backend: Backend,
        *,
        name: str = "default",
        prefix: str = "rl",
    ) -> None:
        self._algorithm = algorithm
        self._quota = quota
        self._backend = backend
        self._name = name
        self._prefix = prefix

    @property
    def quota(self) -> Quota:
        """The quota this limiter enforces."""
        return self._quota

    async def check(self, key: str, cost: int = 1) -> RateLimitResult:
        """See :meth:`RateLimiter.check`."""
        validate_cost(cost)
        request = LimitRequest(
            storage_key(self._prefix, key, self._name, self._algorithm), self._quota, cost
        )
        [result] = await self._backend.apply(self._algorithm, [request])
        return result


def storage_key(prefix: str, key: str, name: str, algorithm: Algorithm[Any]) -> str:
    """Build ``prefix:{key}:name:algorithm``.

    The braces are a Redis Cluster hash tag: only the text inside them picks the cluster
    slot, so every quota for one caller lands on the same node and can be checked by one
    atomic script.
    """
    return f"{prefix}:{{{key}}}:{name}:{algorithm.name}"
