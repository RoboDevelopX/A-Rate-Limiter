"""Per-route FastAPI dependencies: rate limits and concurrency slots."""

import math
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from mlratelimit.clock import Clock, SystemClock
from mlratelimit.concurrency import ConcurrencyLimiter, Lease
from mlratelimit.fastapi.headers import rate_limit_headers, rejection_detail
from mlratelimit.keys import KeyExtractor
from mlratelimit.limiter import RateLimiter
from mlratelimit.types import RateLimitResult


@dataclass(frozen=True, slots=True)
class LimitTarget:
    """Which limiter to apply, to which key, at what cost, for one request."""

    limiter: RateLimiter
    key: str
    cost: int = 1


Resolver = Callable[[Request], Awaitable[LimitTarget]]


class RateLimitDependency:
    """``Depends()``-able rate limit for one route.

    The resolver picks the limiter, key and cost per request, which is how per-tier,
    per-model and token-cost limits plug in. It may raise ``HTTPException`` (e.g. 401 for
    a missing API key). On success the RateLimit headers are added to the response; on
    denial a 429 ``HTTPException`` with ``Retry-After`` is raised.
    """

    def __init__(self, resolve: Resolver, *, clock: Clock | None = None) -> None:
        self._resolve = resolve
        self._clock: Clock = clock or SystemClock()

    @classmethod
    def fixed(
        cls, limiter: RateLimiter, key: KeyExtractor, *, clock: Clock | None = None
    ) -> "RateLimitDependency":
        """One limiter, cost 1, keyed by ``key`` (401 if the request has no key)."""

        async def resolve(request: Request) -> LimitTarget:
            value = key(request)
            if value is None:
                raise HTTPException(status_code=401, detail="missing credentials")
            return LimitTarget(limiter, value)

        return cls(resolve, clock=clock)

    async def __call__(self, request: Request, response: Response) -> RateLimitResult:
        """Run the check for this request."""
        target = await self._resolve(request)
        result = await target.limiter.check(target.key, target.cost)
        headers = rate_limit_headers(result, self._clock.now())
        if not result.allowed:
            raise HTTPException(status_code=429, detail=rejection_detail(result), headers=headers)
        response.headers.update(headers)
        return result


class ConcurrencyDependency:
    """``Depends()``-able in-flight cap. Holds a slot for the duration of the request.

    When all slots are taken it raises 503 (the server is at capacity; this is not the
    caller's quota) with ``Retry-After``.

    Args:
        limiter: Where slots are counted.
        resolve: Returns ``(key, limit)`` for a request, e.g. ``("model:large", 2)``.
        retry_after: Seconds clients should wait before retrying.
    """

    def __init__(
        self,
        limiter: ConcurrencyLimiter,
        resolve: Callable[[Request], Awaitable[tuple[str, int]]],
        *,
        retry_after: float = 1.0,
    ) -> None:
        self._limiter = limiter
        self._resolve = resolve
        self._retry_after = retry_after

    async def __call__(self, request: Request) -> AsyncIterator[Lease]:
        """Acquire a slot, yield it to the route, release it afterwards."""
        key, limit = await self._resolve(request)
        lease = await self._limiter.acquire(key, limit)
        if lease is None:
            raise HTTPException(
                status_code=503,
                detail="server at capacity, retry shortly",
                headers={"Retry-After": str(math.ceil(self._retry_after))},
            )
        try:
            yield lease
        finally:
            await self._limiter.release(lease)
