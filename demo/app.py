"""Demo service: a fake LLM ``/predict`` endpoint protected by mlratelimit.

Run with ``uvicorn demo.app:app``. Configuration comes from environment variables; see
:class:`Settings`.
"""

import asyncio
import json
import logging
import math
import os
from collections.abc import AsyncIterator, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.responses import Response

from mlratelimit import MemoryBackend, RedisBackend
from mlratelimit.backends.base import Backend
from mlratelimit.concurrency import (
    ConcurrencyLimiter,
    Lease,
    MemoryConcurrencyLimiter,
    RedisConcurrencyLimiter,
)
from mlratelimit.config import LimitsConfig
from mlratelimit.fastapi import (
    ConcurrencyDependency,
    LimitTarget,
    RateLimitDependency,
    RateLimitMiddleware,
)
from mlratelimit.keys import ClientIP, api_key
from mlratelimit.log import configure_logging
from mlratelimit.metrics import InstrumentedLimiter, Metrics
from mlratelimit.registry import LimiterRegistry
from mlratelimit.resilience import CircuitBreaker, FailureGuard, FailureMode, ResilientBackend
from mlratelimit.types import RateLimitResult

logger = logging.getLogger("demo")

API_KEY_HEADER = "X-API-Key"
DEFAULT_LIMITS_FILE = Path(__file__).with_name("limits.yaml")


class ModelSpec(BaseModel):
    """A fake model and how fast it "generates"."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    seconds_per_token: PositiveFloat


class DemoConfig(BaseModel):
    """The demo's YAML file: library limits plus demo-only models and API keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    limits: LimitsConfig
    models: dict[str, ModelSpec] = Field(min_length=1)
    api_keys: dict[str, str]


@dataclass(frozen=True)
class Settings:
    """Runtime settings, read from environment variables by :meth:`from_env`."""

    limits_file: Path = DEFAULT_LIMITS_FILE
    redis_url: str | None = None
    failure_mode: FailureMode = FailureMode.OPEN
    redis_timeout: float = 0.05
    trusted_proxies: tuple[str, ...] = ()
    log_level: str = "INFO"
    base_latency: float = 0.02

    @classmethod
    def from_env(cls) -> "Settings":
        """``LIMITS_FILE``, ``REDIS_URL``, ``FAILURE_MODE``, ``REDIS_TIMEOUT_MS``,
        ``TRUSTED_PROXIES`` (comma separated), ``LOG_LEVEL``, ``BASE_LATENCY_MS``."""
        env = os.environ
        proxies = tuple(p.strip() for p in env.get("TRUSTED_PROXIES", "").split(",") if p.strip())
        return cls(
            limits_file=Path(env.get("LIMITS_FILE", str(DEFAULT_LIMITS_FILE))),
            redis_url=env.get("REDIS_URL") or None,
            failure_mode=FailureMode(env.get("FAILURE_MODE", "open")),
            redis_timeout=float(env.get("REDIS_TIMEOUT_MS", "50")) / 1000,
            trusted_proxies=proxies,
            log_level=env.get("LOG_LEVEL", "INFO"),
            base_latency=float(env.get("BASE_LATENCY_MS", "20")) / 1000,
        )


class PredictRequest(BaseModel):
    """An LLM completion request."""

    model: str
    prompt: str = Field(min_length=1)
    max_tokens: int = Field(default=128, ge=1, le=4096)


class Usage(BaseModel):
    """Token accounting, as LLM APIs report it."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class PredictResponse(BaseModel):
    """A fake completion."""

    model: str
    completion: str
    usage: Usage


def estimate_prompt_tokens(prompt: str) -> int:
    """Rough token count (about 4 characters per token for English text)."""
    return max(1, math.ceil(len(prompt) / 4))


def load_demo_config(path: Path) -> DemoConfig:
    """Load and validate the demo YAML file."""
    return DemoConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


_caller_key = api_key(API_KEY_HEADER)


def authenticate(request: Request, api_keys: Mapping[str, str]) -> tuple[str, str]:
    """Return ``(tier, caller key)`` for a known API key, else raise 401."""
    tier = api_keys.get(request.headers.get(API_KEY_HEADER, ""))
    caller = _caller_key(request)
    if tier is None or caller is None:
        raise HTTPException(status_code=401, detail=f"missing or unknown {API_KEY_HEADER}")
    return tier, caller


async def read_predict_request(request: Request, models: Collection[str]) -> PredictRequest:
    """Parse and validate the body inside a dependency (which runs before FastAPI's own
    body validation). Starlette caches the body, so it is only read from the socket once."""
    try:
        body = PredictRequest.model_validate(await request.json())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="body is not valid JSON") from exc
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    if body.model not in models:
        raise HTTPException(status_code=422, detail=f"unknown model {body.model!r}")
    return body


@dataclass(frozen=True)
class Storage:
    """Where limiter state lives: Redis (shared by all workers) or this process."""

    backend: Backend
    concurrency: ConcurrencyLimiter
    redis: Redis | None


def build_storage(settings: Settings, metrics: Metrics) -> Storage:
    """Redis with a timeout, circuit breaker and failure mode if configured, else memory."""
    if not settings.redis_url:
        return Storage(MemoryBackend(), MemoryConcurrencyLimiter(), None)
    redis = Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout,
        socket_connect_timeout=settings.redis_timeout,
    )
    guard = FailureGuard(
        timeout=settings.redis_timeout,
        breaker=CircuitBreaker(),
        on_failure=metrics.record_backend_error,
    )
    return Storage(
        backend=ResilientBackend(RedisBackend(redis), mode=settings.failure_mode, guard=guard),
        concurrency=RedisConcurrencyLimiter(redis, mode=settings.failure_mode, guard=guard),
        redis=redis,
    )


def create_app(settings: Settings) -> FastAPI:
    """Build the demo app. Everything is wired here so tests can build their own."""
    config = load_demo_config(settings.limits_file)
    metrics = Metrics()
    storage = build_storage(settings, metrics)
    redis = storage.redis
    registry = LimiterRegistry(config.limits, storage.backend)

    async def require_api_key(request: Request) -> None:
        authenticate(request, config.api_keys)

    async def parse_body(request: Request) -> PredictRequest:
        return await read_predict_request(request, config.models)

    async def resolve_limit(request: Request) -> LimitTarget:
        tier, caller = authenticate(request, config.api_keys)
        body = await parse_body(request)
        limiter = registry.for_rule(tier, body.model)
        return LimitTarget(
            limiter=InstrumentedLimiter(limiter, metrics, limiter.name),
            key=caller,
            cost=estimate_prompt_tokens(body.prompt) + body.max_tokens,
        )

    async def resolve_slot(request: Request) -> tuple[str, int]:
        body = await parse_body(request)
        return f"model:{body.model}", config.limits.concurrency.limit_for(body.model)

    rate_limit = RateLimitDependency(resolve_limit)
    gpu_slot = ConcurrencyDependency(storage.concurrency, resolve_slot)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        logger.info(
            "starting demo",
            extra={"backend": "redis" if redis else "memory", "mode": str(settings.failure_mode)},
        )
        yield
        if redis is not None:
            await redis.aclose()

    app = FastAPI(title="mlratelimit demo", lifespan=lifespan)

    global_limiter = registry.global_limiter()
    if global_limiter is not None:
        app.add_middleware(
            RateLimitMiddleware,
            limiter=InstrumentedLimiter(global_limiter, metrics, "global"),
            key=ClientIP(settings.trusted_proxies),
            exempt_paths={"/healthz", "/metrics"},
        )

    @app.post("/predict")
    async def predict(
        body: PredictRequest,
        # Order matters: authenticate, then take a GPU slot, then charge quota, so a request
        # turned away because the server is at capacity costs the caller nothing.
        _auth: None = Depends(require_api_key),
        _slot: Lease = Depends(gpu_slot),
        _rate: RateLimitResult = Depends(rate_limit),
    ) -> PredictResponse:
        """Pretend to run inference: sleep in proportion to the tokens generated."""
        spec = config.models[body.model]
        await asyncio.sleep(settings.base_latency + spec.seconds_per_token * body.max_tokens)
        prompt_tokens = estimate_prompt_tokens(body.prompt)
        return PredictResponse(
            model=body.model,
            completion="lorem ipsum " * min(body.max_tokens, 8),
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=body.max_tokens,
                total_tokens=prompt_tokens + body.max_tokens,
            ),
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness plus backend reachability (the service still answers if Redis is down)."""
        if redis is None:
            return {"status": "ok", "backend": "memory"}
        try:
            async with asyncio.timeout(settings.redis_timeout * 4):
                await redis.ping()
        except (TimeoutError, OSError, RedisError):
            return {"status": "degraded", "backend": "redis", "redis": "down"}
        return {"status": "ok", "backend": "redis", "redis": "up"}

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        """Prometheus scrape endpoint."""
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app(Settings.from_env())
