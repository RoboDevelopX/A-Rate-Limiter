"""Redis backend: every check is one Lua script, so it is atomic across all app servers."""

from collections.abc import Sequence
from importlib import resources
from typing import Any

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

from mlratelimit.algorithms.base import Algorithm
from mlratelimit.backends.base import ensure_unique_keys
from mlratelimit.clock import Clock
from mlratelimit.types import LimitRequest, RateLimitResult


def load_script_source(name: str) -> str:
    """Return the full Lua source for one algorithm: prelude, algorithm body, driver."""
    lua = resources.files("mlratelimit.backends") / "lua"
    parts = ("_prelude.lua", f"{name}.lua", "_driver.lua")
    return "\n".join((lua / part).read_text(encoding="utf-8") for part in parts)


class RedisBackend:
    """Stores state in Redis and checks it with server-side Lua scripts.

    Atomicity: Redis runs a script to completion before serving any other command, so
    read-decide-write cannot interleave between app servers. Scripts are sent once and then
    invoked by SHA (EVALSHA); if Redis has lost them (restart, failover, SCRIPT FLUSH),
    redis-py reloads them transparently on the NOSCRIPT error.

    Time: by default the script reads Redis's own clock (``TIME``), so app servers with
    skewed clocks still agree. Pass ``clock`` only in tests, to drive time deterministically.

    Expiry: each key gets a TTL of its logical expiry plus ``ttl_padding`` seconds. Keeping
    state a little longer is harmless (expired state never changes a decision) and absorbs
    millisecond rounding.

    Args:
        client: A ``redis.asyncio.Redis`` client. Set ``socket_timeout`` on it.
        clock: Test-only time override. ``None`` means Redis server time.
        ttl_padding: Extra seconds keys live past their logical expiry.
    """

    def __init__(
        self, client: Redis, *, clock: Clock | None = None, ttl_padding: float = 1.0
    ) -> None:
        self._client = client
        self._clock = clock
        self._ttl_padding = ttl_padding
        self._scripts: dict[str, AsyncScript] = {}

    @property
    def client(self) -> Redis:
        """The underlying Redis client."""
        return self._client

    async def apply(
        self, algorithm: Algorithm[Any], requests: Sequence[LimitRequest]
    ) -> list[RateLimitResult]:
        """See :meth:`mlratelimit.backends.base.Backend.apply`."""
        ensure_unique_keys(requests)
        now = "" if self._clock is None else repr(self._clock.now())
        args: list[str | int] = [now, repr(self._ttl_padding)]
        for request in requests:
            args += [request.quota.limit, repr(request.quota.period), request.cost]
        reply = await self._script(algorithm)(keys=[r.key for r in requests], args=args)
        return [
            _parse_result(reply[4 * i : 4 * i + 4], request.quota.limit)
            for i, request in enumerate(requests)
        ]

    def _script(self, algorithm: Algorithm[Any]) -> AsyncScript:
        script = self._scripts.get(algorithm.name)
        if script is None:
            script = self._client.register_script(load_script_source(algorithm.name))
            self._scripts[algorithm.name] = script
        return script


def _parse_result(fields: list[Any], limit: int) -> RateLimitResult:
    allowed, remaining, retry_after, reset_at = fields
    return RateLimitResult(
        allowed=bool(int(allowed)),
        limit=limit,
        remaining=int(remaining),
        retry_after=float(retry_after) if retry_after not in (b"", "") else None,
        reset_at=float(reset_at),
    )
