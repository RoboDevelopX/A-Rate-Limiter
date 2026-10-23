"""Several quotas checked together, e.g. requests per minute and tokens per minute."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mlratelimit.algorithms.base import Algorithm
from mlratelimit.backends.base import Backend
from mlratelimit.limiter import storage_key, validate_cost
from mlratelimit.types import LimitRequest, Quota, RateLimitResult


class Unit(StrEnum):
    """What a quota counts."""

    REQUESTS = "requests"
    """Every check costs 1, whatever cost the caller passes."""
    TOKENS = "tokens"
    """Every check costs the caller's ``cost`` (e.g. an LLM token estimate)."""


@dataclass(frozen=True, slots=True)
class NamedQuota:
    """A quota with a name (used in storage keys and logs) and a unit."""

    name: str
    quota: Quota
    unit: Unit = Unit.REQUESTS


class CompositeLimiter:
    """Checks every quota in one atomic backend call; all must allow.

    A request that fails the token quota spends nothing from the request quota, and vice
    versa. The returned result describes the most restrictive quota, which is what clients
    need for headers and retries.

    Args:
        algorithm: Shared by all quotas, so the batch is one script call on Redis.
        quotas: At least one, with unique names.
        backend: Where state lives.
        name: Namespace for this rule, e.g. ``"pro:large-model"``.
        prefix: Namespace for storage keys.
    """

    def __init__(
        self,
        algorithm: Algorithm[Any],
        quotas: Sequence[NamedQuota],
        backend: Backend,
        *,
        name: str,
        prefix: str = "rl",
    ) -> None:
        if not quotas:
            raise ValueError("at least one quota is required")
        names = [q.name for q in quotas]
        if len(set(names)) != len(names):
            raise ValueError(f"quota names must be unique, got {names}")
        self._algorithm = algorithm
        self._quotas = tuple(quotas)
        self._backend = backend
        self._name = name
        self._prefix = prefix

    @property
    def name(self) -> str:
        """The rule name this limiter was created with."""
        return self._name

    @property
    def quotas(self) -> tuple[NamedQuota, ...]:
        """The quotas checked on every call."""
        return self._quotas

    async def check(self, key: str, cost: int = 1) -> RateLimitResult:
        """Check all quotas for ``key``; ``cost`` applies to token quotas only."""
        validate_cost(cost)
        requests = [
            LimitRequest(
                storage_key(self._prefix, key, f"{self._name}:{q.name}", self._algorithm),
                q.quota,
                cost if q.unit is Unit.TOKENS else 1,
            )
            for q in self._quotas
        ]
        return most_restrictive(await self._backend.apply(self._algorithm, requests))


def most_restrictive(results: Sequence[RateLimitResult]) -> RateLimitResult:
    """Pick the result a client should see.

    If anything was denied: the denial with the longest wait (``None``, meaning never, wins).
    Otherwise: the quota with the smallest fraction remaining.
    """
    denied = [r for r in results if not r.allowed]
    if denied:
        return max(denied, key=lambda r: float("inf") if r.retry_after is None else r.retry_after)
    return min(results, key=lambda r: r.remaining / r.limit)
