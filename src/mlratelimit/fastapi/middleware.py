"""ASGI middleware applying one limit to every HTTP request (e.g. per client IP)."""

from collections.abc import Collection

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mlratelimit.clock import Clock, SystemClock
from mlratelimit.fastapi.headers import rate_limit_headers, too_many_requests
from mlratelimit.keys import KeyExtractor
from mlratelimit.limiter import RateLimiter


class RateLimitMiddleware:
    """Checks ``limiter`` before the app runs; rejects with 429 or adds RateLimit headers.

    Headers already set by a route (e.g. by :class:`RateLimitDependency`, which knows the
    more specific limit) are left as they are.

    Args:
        app: The wrapped ASGI app.
        limiter: Applied with cost 1 per request.
        key: How to identify the caller. Requests without a key share ``"anonymous"``.
        exempt_paths: Exact paths that skip the limit, e.g. health checks and metrics.
        clock: Used to turn reset times into header seconds.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        key: KeyExtractor,
        exempt_paths: Collection[str] = (),
        clock: Clock | None = None,
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._key = key
        self._exempt = frozenset(exempt_paths)
        self._clock: Clock = clock or SystemClock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point."""
        if scope["type"] != "http" or scope["path"] in self._exempt:
            await self._app(scope, receive, send)
            return
        key = self._key(Request(scope)) or "anonymous"
        result = await self._limiter.check(key)
        now = self._clock.now()
        if not result.allowed:
            await too_many_requests(result, now)(scope, receive, send)
            return
        headers = rate_limit_headers(result, now)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in headers.items():
                    response_headers.setdefault(name, value)
            await send(message)

        await self._app(scope, receive, send_with_headers)
