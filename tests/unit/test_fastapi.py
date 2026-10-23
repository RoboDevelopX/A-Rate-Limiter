import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from starlette.types import Message, Receive, Scope, Send

from mlratelimit import FixedWindow, Limiter, ManualClock, MemoryBackend, Quota, RateLimitResult
from mlratelimit.concurrency import Lease, MemoryConcurrencyLimiter
from mlratelimit.fastapi import (
    ConcurrencyDependency,
    LimitTarget,
    RateLimitDependency,
    RateLimitMiddleware,
)
from mlratelimit.keys import ClientIP, api_key
from tests.helpers import T0


def build_app(clock: ManualClock, slots: MemoryConcurrencyLimiter) -> FastAPI:
    backend = MemoryBackend(clock)
    per_ip = Limiter(FixedWindow(), Quota(limit=5, period=10), backend, name="ip")
    per_key = Limiter(FixedWindow(), Quota(limit=2, period=60), backend, name="key")
    tokens = Limiter(FixedWindow(), Quota(limit=100, period=60), backend, name="tokens")

    async def by_tokens(request: Request) -> LimitTarget:
        return LimitTarget(tokens, "caller", int(request.query_params["cost"]))

    async def slot_for(_: Request) -> tuple[str, int]:
        return "gpu", 1

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=per_ip,
        key=ClientIP(),
        exempt_paths={"/healthz"},
        clock=clock,
    )
    keyed = RateLimitDependency.fixed(per_key, api_key(), clock=clock)
    costed = RateLimitDependency(by_tokens, clock=clock)
    gpu = ConcurrencyDependency(slots, slot_for, retry_after=2)

    @app.get("/open")
    async def open_route() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/keyed")
    async def keyed_route(result: RateLimitResult = Depends(keyed)) -> dict[str, int]:
        return {"remaining": result.remaining}

    @app.get("/costed")
    async def costed_route(_: RateLimitResult = Depends(costed)) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/slow")
    async def slow_route(_: Lease = Depends(gpu)) -> dict[str, str]:
        await asyncio.sleep(0.05)
        return {"ok": "yes"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def slots() -> MemoryConcurrencyLimiter:
    return MemoryConcurrencyLimiter()


@pytest.fixture
async def client(
    clock: ManualClock, slots: MemoryConcurrencyLimiter
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(clock, slots), client=("203.0.113.7", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_middleware_adds_headers_then_rejects_with_429(client: httpx.AsyncClient) -> None:
    for remaining in range(4, -1, -1):
        response = await client.get("/open")
        assert response.status_code == 200
        assert response.headers["RateLimit-Limit"] == "5"
        assert response.headers["RateLimit-Remaining"] == str(remaining)
        assert response.headers["RateLimit-Reset"] == "10"
    blocked = await client.get("/open")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "10"
    assert blocked.json() == {"detail": "rate limit exceeded", "retry_after": 10.0}


async def test_exempt_paths_skip_the_limit(client: httpx.AsyncClient) -> None:
    for _ in range(10):
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert "RateLimit-Limit" not in response.headers


async def test_route_headers_win_over_middleware_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/keyed", headers={"X-API-Key": "abc"})
    assert response.status_code == 200
    assert response.headers["RateLimit-Limit"] == "2"  # the route's limit, not the IP limit
    assert response.json() == {"remaining": 1}


async def test_dependency_rejects_with_429_and_retry_after(
    client: httpx.AsyncClient, clock: ManualClock
) -> None:
    clock.advance(15)  # 45 s left in the minute window
    for _ in range(2):
        await client.get("/keyed", headers={"X-API-Key": "abc"})
    response = await client.get("/keyed", headers={"X-API-Key": "abc"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "45"
    assert response.headers["RateLimit-Remaining"] == "0"
    assert response.json() == {"detail": "rate limit exceeded"}


async def test_fixed_dependency_requires_a_key(client: httpx.AsyncClient) -> None:
    response = await client.get("/keyed")
    assert response.status_code == 401


async def test_cost_above_the_limit_explains_itself(client: httpx.AsyncClient) -> None:
    assert (await client.get("/costed", params={"cost": 60})).status_code == 200
    assert (await client.get("/costed", params={"cost": 60})).status_code == 429
    response = await client.get("/costed", params={"cost": 101})
    assert response.status_code == 429
    assert "Retry-After" not in response.headers
    assert "can never be allowed" in response.json()["detail"]


async def test_concurrency_dependency_returns_503_and_releases(
    client: httpx.AsyncClient, slots: MemoryConcurrencyLimiter
) -> None:
    first, second = await asyncio.gather(client.get("/slow"), client.get("/slow"))
    assert sorted([first.status_code, second.status_code]) == [200, 503]
    busy = first if first.status_code == 503 else second
    assert busy.headers["Retry-After"] == "2"
    assert slots.in_flight("gpu") == 0  # released after the response
    assert (await client.get("/slow")).status_code == 200


async def test_requests_without_a_client_share_the_anonymous_key(clock: ManualClock) -> None:
    app = build_app(clock, MemoryConcurrencyLimiter())
    statuses = []

    async def call() -> None:
        scope: Scope = {
            "type": "http", "method": "GET", "path": "/open", "raw_path": b"/open",
            "query_string": b"", "headers": [], "client": None, "server": ("test", 80),
            "scheme": "http", "http_version": "1.1", "root_path": "", "asgi": {"version": "3.0"},
        }  # fmt: skip

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        await app(scope, receive, send)

    for _ in range(6):
        await call()
    assert statuses == [200] * 5 + [429]


async def test_non_http_scopes_pass_through(clock: ManualClock) -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    limiter = Limiter(FixedWindow(), Quota(1, 1), MemoryBackend(clock))
    middleware = RateLimitMiddleware(inner, limiter=limiter, key=ClientIP())

    async def noop_receive() -> Message:
        return {}

    async def noop_send(message: Message) -> None:
        return None

    await middleware({"type": "lifespan"}, noop_receive, noop_send)
    assert seen == ["lifespan"]
    assert clock.now() == T0
