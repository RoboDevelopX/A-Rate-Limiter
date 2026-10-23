import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis

from demo.app import DEFAULT_LIMITS_FILE, Settings, create_app, estimate_prompt_tokens
from mlratelimit.resilience import FailureMode
from tests.helpers import REDIS_URL

FREE = {"X-API-Key": "demo-free-key"}
PRO = {"X-API-Key": "demo-pro-key"}

Connect = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@asynccontextmanager
async def connect(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(base_latency=0.0, **overrides))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
            yield client


def body(model: str = "small-model", prompt: str = "hello", max_tokens: int = 8) -> dict[str, Any]:
    return {"model": model, "prompt": prompt, "max_tokens": max_tokens}


async def test_free_tier_gets_five_requests_then_429() -> None:
    async with connect() as client:
        for _ in range(5):
            response = await client.post("/predict", json=body(), headers=FREE)
            assert response.status_code == 200
        blocked = await client.post("/predict", json=body(), headers=FREE)
    assert blocked.status_code == 429
    assert blocked.headers["RateLimit-Limit"] == "5"
    assert blocked.headers["RateLimit-Remaining"] == "0"
    assert int(blocked.headers["Retry-After"]) > 0
    usage = response.json()["usage"]
    assert usage == {"prompt_tokens": 2, "completion_tokens": 8, "total_tokens": 10}


async def test_token_cost_is_limited_separately_from_request_count() -> None:
    async with connect() as client:
        ok = await client.post("/predict", json=body(max_tokens=1500), headers=FREE)
        assert ok.status_code == 200
        # 1502 of 2000 tokens used: the token quota is now the most restrictive one.
        assert ok.headers["RateLimit-Limit"] == "2000"
        assert ok.headers["RateLimit-Remaining"] == "498"
        blocked = await client.post("/predict", json=body(max_tokens=1000), headers=FREE)
        assert blocked.status_code == 429
        assert blocked.headers["RateLimit-Limit"] == "2000"
        # The rejected call charged nothing: a small call still fits.
        assert (await client.post("/predict", json=body(), headers=FREE)).status_code == 200


async def test_pro_tier_has_a_stricter_rule_for_the_large_model() -> None:
    async with connect() as client:
        small = await client.post("/predict", json=body(), headers=PRO)
        large = await client.post("/predict", json=body("large-model"), headers=PRO)
    assert small.headers["RateLimit-Limit"] == "60"
    assert large.headers["RateLimit-Limit"] == "20"


async def test_large_model_concurrency_is_capped_at_two() -> None:
    async with connect() as client:
        responses = await asyncio.gather(
            *(
                client.post("/predict", json=body("large-model", max_tokens=200), headers=PRO)
                for _ in range(3)
            )
        )
        after = await client.post("/predict", json=body("large-model"), headers=PRO)
    assert sorted(r.status_code for r in responses) == [200, 200, 503]
    # Only the two served requests were charged: 20 - 2 - 1 = 17 requests left.
    assert after.headers["RateLimit-Remaining"] == "17"


@pytest.mark.parametrize(
    ("headers", "payload", "status"),
    [
        ({}, body(), 401),
        ({"X-API-Key": "wrong"}, body(), 401),
        (FREE, body("gpt-17"), 422),
        (FREE, {"model": "small-model"}, 422),
    ],
)
async def test_rejects_bad_requests(
    headers: dict[str, str], payload: dict[str, Any], status: int
) -> None:
    async with connect() as client:
        response = await client.post("/predict", json=payload, headers=headers)
    assert response.status_code == status


async def test_rejects_malformed_json() -> None:
    async with connect() as client:
        response = await client.post("/predict", content=b"{not json", headers=FREE)
    assert response.status_code == 422


async def test_healthz_and_metrics() -> None:
    async with connect() as client:
        await client.post("/predict", json=body(), headers=FREE)
        health = await client.get("/healthz")
        metrics = (await client.get("/metrics")).text
    assert health.json() == {"status": "ok", "backend": "memory"}
    assert 'ratelimit_decisions_total{decision="allowed",limiter="free:default"} 1.0' in metrics
    assert 'ratelimit_decisions_total{decision="allowed",limiter="global"}' in metrics
    assert "ratelimit_check_duration_seconds_bucket" in metrics


def test_estimate_prompt_tokens() -> None:
    assert estimate_prompt_tokens("x") == 1
    assert estimate_prompt_tokens("x" * 9) == 3


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LIMITS_FILE", str(tmp_path / "x.yaml"))
    monkeypatch.setenv("REDIS_URL", "redis://r:6379/0")
    monkeypatch.setenv("FAILURE_MODE", "closed")
    monkeypatch.setenv("REDIS_TIMEOUT_MS", "25")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8, 172.16.0.0/12")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("BASE_LATENCY_MS", "5")
    settings = Settings.from_env()
    assert settings == Settings(
        limits_file=tmp_path / "x.yaml",
        redis_url="redis://r:6379/0",
        failure_mode=FailureMode.CLOSED,
        redis_timeout=0.025,
        trusted_proxies=("10.0.0.0/8", "172.16.0.0/12"),
        log_level="debug",
        base_latency=0.005,
    )
    for name in ["LIMITS_FILE", "REDIS_URL", "FAILURE_MODE", "TRUSTED_PROXIES"]:
        monkeypatch.delenv(name)
    defaults = Settings.from_env()
    assert defaults.limits_file == DEFAULT_LIMITS_FILE
    assert defaults.redis_url is None
    assert defaults.trusted_proxies == ()


@pytest.mark.redis
async def test_with_redis(redis_client: Redis) -> None:
    async with connect(redis_url=REDIS_URL) as client:
        for _ in range(5):
            assert (await client.post("/predict", json=body(), headers=FREE)).status_code == 200
        assert (await client.post("/predict", json=body(), headers=FREE)).status_code == 429
        assert (await client.get("/healthz")).json() == {
            "status": "ok",
            "backend": "redis",
            "redis": "up",
        }


UNREACHABLE = "redis://localhost:1/0"


async def test_redis_outage_fails_open_and_reports_it() -> None:
    async with connect(redis_url=UNREACHABLE) as client:
        response = await client.post("/predict", json=body(), headers=FREE)
        health = await client.get("/healthz")
        metrics = (await client.get("/metrics")).text
    assert response.status_code == 200
    assert health.json() == {"status": "degraded", "backend": "redis", "redis": "down"}
    assert "ratelimit_backend_errors_total" in metrics


async def test_redis_outage_fails_closed_when_configured() -> None:
    async with connect(redis_url=UNREACHABLE, failure_mode=FailureMode.CLOSED) as client:
        response = await client.post("/predict", json=body(), headers=FREE)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
