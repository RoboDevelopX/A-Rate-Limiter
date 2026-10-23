"""FastAPI/Starlette integration. Requires the ``fastapi`` extra."""

from mlratelimit.fastapi.dependency import ConcurrencyDependency, LimitTarget, RateLimitDependency
from mlratelimit.fastapi.headers import rate_limit_headers, too_many_requests
from mlratelimit.fastapi.middleware import RateLimitMiddleware

__all__ = [
    "ConcurrencyDependency",
    "LimitTarget",
    "RateLimitDependency",
    "RateLimitMiddleware",
    "rate_limit_headers",
    "too_many_requests",
]
