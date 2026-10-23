"""HTTP headers and 429 responses for rate limit results.

Follows the IETF ``RateLimit`` header draft: ``RateLimit-Limit``, ``RateLimit-Remaining``
and ``RateLimit-Reset`` (seconds until the quota is full again), plus ``Retry-After`` on 429.
"""

import math

from starlette.responses import JSONResponse

from mlratelimit.types import RateLimitResult


def rate_limit_headers(result: RateLimitResult, now: float) -> dict[str, str]:
    """Headers describing ``result``; ``now`` is the current Unix time."""
    headers = {
        "RateLimit-Limit": str(result.limit),
        "RateLimit-Remaining": str(result.remaining),
        "RateLimit-Reset": str(math.ceil(max(0.0, result.reset_at - now))),
    }
    if not result.allowed and result.retry_after is not None:
        headers["Retry-After"] = str(math.ceil(result.retry_after))
    return headers


def rejection_detail(result: RateLimitResult) -> str:
    """Human-readable reason for a denied result."""
    if result.retry_after is None:
        return "request cost exceeds the rate limit and can never be allowed; reduce it"
    return "rate limit exceeded"


def too_many_requests(result: RateLimitResult, now: float) -> JSONResponse:
    """A 429 response with rate limit headers and a JSON body."""
    return JSONResponse(
        {"detail": rejection_detail(result), "retry_after": result.retry_after},
        status_code=429,
        headers=rate_limit_headers(result, now),
    )
