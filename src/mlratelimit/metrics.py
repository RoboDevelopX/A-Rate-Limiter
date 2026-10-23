"""Prometheus metrics for limit decisions, backend errors and check latency."""

import time

from prometheus_client import CollectorRegistry, Counter, Histogram

from mlratelimit.limiter import RateLimiter
from mlratelimit.types import RateLimitResult

_LATENCY_BUCKETS = (0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25)


class Metrics:
    """The metric families, registered on their own registry (not the global default).

    Labels hold rule names such as ``"pro:large-model"``, never caller keys, so the number
    of time series stays bounded no matter how many users there are.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.decisions = Counter(
            "ratelimit_decisions",
            "Rate limit decisions by limiter and outcome (allowed or blocked).",
            ["limiter", "decision"],
            registry=self.registry,
        )
        self.backend_errors = Counter(
            "ratelimit_backend_errors",
            "Backend calls that failed, timed out, or were skipped by an open circuit.",
            ["reason"],
            registry=self.registry,
        )
        self.check_latency = Histogram(
            "ratelimit_check_duration_seconds",
            "Time spent in one rate limit check, including the backend round trip.",
            ["limiter"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )

    def record_backend_error(self, reason: str) -> None:
        """Count one backend failure; pass this as ``FailureGuard(on_failure=...)``."""
        self.backend_errors.labels(reason=reason).inc()

    def record_decision(self, limiter: str, allowed: bool) -> None:
        """Count one allowed or blocked decision."""
        self.decisions.labels(limiter=limiter, decision="allowed" if allowed else "blocked").inc()


class InstrumentedLimiter:
    """Wraps any :class:`RateLimiter` to record decisions and latency under ``name``."""

    def __init__(self, limiter: RateLimiter, metrics: Metrics, name: str) -> None:
        self._limiter = limiter
        self._metrics = metrics
        self._name = name

    async def check(self, key: str, cost: int = 1) -> RateLimitResult:
        """See :meth:`RateLimiter.check`."""
        start = time.perf_counter()
        result = await self._limiter.check(key, cost)
        self._metrics.check_latency.labels(limiter=self._name).observe(time.perf_counter() - start)
        self._metrics.record_decision(self._name, result.allowed)
        return result
