"""Caps on in-flight requests, for GPU-bound endpoints where concurrency is the bottleneck."""

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

from mlratelimit.resilience import FailureGuard, FailureMode

_ACQUIRE = """
-- KEYS[1] lease set; ARGV: limit, lease ttl (s), lease token
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', string.format('%.17g', now))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  return 0
end
local ttl = tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], string.format('%.17g', now + ttl), ARGV[3])
redis.call('PEXPIRE', KEYS[1], math.ceil(ttl * 1000))
return 1
"""


@dataclass(frozen=True, slots=True)
class Lease:
    """Proof of one in-flight slot. Release it when the request finishes."""

    key: str
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    degraded: bool = False
    """True when granted without the backend (fail-open); releasing it is a no-op."""


class ConcurrencyLimiter(Protocol):
    """Grants at most ``limit`` concurrent leases per key."""

    async def acquire(self, key: str, limit: int) -> Lease | None:
        """Take a slot, or return ``None`` if ``limit`` are already in use."""
        ...

    async def release(self, lease: Lease) -> None:
        """Give a slot back. Safe to call more than once."""
        ...


class MemoryConcurrencyLimiter:
    """In-process slot counter. Correct for one worker process only."""

    def __init__(self) -> None:
        self._held: dict[str, set[str]] = {}

    def in_flight(self, key: str) -> int:
        """Slots currently held for ``key``."""
        return len(self._held.get(key, ()))

    async def acquire(self, key: str, limit: int) -> Lease | None:
        """See :meth:`ConcurrencyLimiter.acquire`."""
        held = self._held.setdefault(key, set())
        if len(held) >= limit:
            return None
        lease = Lease(key)
        held.add(lease.token)
        return lease

    async def release(self, lease: Lease) -> None:
        """See :meth:`ConcurrencyLimiter.release`."""
        held = self._held.get(lease.key)
        if held is None:
            return
        held.discard(lease.token)
        if not held:
            del self._held[lease.key]


class RedisConcurrencyLimiter:
    """Slots shared by every worker, stored as a Redis sorted set of expiring leases.

    Each lease expires after ``lease_ttl`` seconds even if never released, so a crashed
    worker cannot leak slots forever. Set ``lease_ttl`` above your slowest request: a
    request that outlives its lease stops being counted.

    Args:
        client: Redis client.
        lease_ttl: Seconds before an unreleased lease is reclaimed.
        mode: Fail-open grants a degraded lease when Redis is unavailable; fail-closed
            refuses.
        guard: Timeout and circuit breaker shared with the rate limit backend.
        prefix: Namespace for keys.
    """

    def __init__(
        self,
        client: Redis,
        *,
        lease_ttl: float = 120.0,
        mode: FailureMode = FailureMode.OPEN,
        guard: FailureGuard | None = None,
        prefix: str = "cc",
    ) -> None:
        self._client = client
        self._lease_ttl = lease_ttl
        self._mode = mode
        self._guard = guard or FailureGuard()
        self._prefix = prefix
        self._acquire: AsyncScript = client.register_script(_ACQUIRE)

    async def acquire(self, key: str, limit: int) -> Lease | None:
        """See :meth:`ConcurrencyLimiter.acquire`."""
        lease = Lease(key)
        granted = await self._guard.call(
            lambda: self._acquire(
                keys=[self._redis_key(key)], args=[limit, repr(self._lease_ttl), lease.token]
            )
        )
        if granted is None:
            return Lease(key, degraded=True) if self._mode is FailureMode.OPEN else None
        return lease if int(granted) == 1 else None

    async def release(self, lease: Lease) -> None:
        """See :meth:`ConcurrencyLimiter.release`."""
        if lease.degraded:
            return
        await self._guard.call(lambda: self._client.zrem(self._redis_key(lease.key), lease.token))

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{{{key}}}"
