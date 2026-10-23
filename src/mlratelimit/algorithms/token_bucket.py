"""Token bucket: tokens refill continuously up to a capacity; each request spends some."""

import math
from dataclasses import dataclass
from typing import ClassVar

from mlratelimit.algorithms.base import Algorithm, Evaluation, retry_after_for
from mlratelimit.types import Quota, RateLimitResult


@dataclass(frozen=True, slots=True)
class TokenBucketState:
    """Tokens left at ``updated_at``. Refill since then is computed lazily on the next check."""

    tokens: float
    updated_at: float


class TokenBucket(Algorithm[TokenBucketState]):
    """Capacity ``limit`` tokens, refilled at ``limit / period`` tokens per second.

    Allows bursts of up to ``limit`` after an idle period, while holding the long-run average
    to the refill rate. O(1) time and memory; no background timer is needed because refill is
    computed from the elapsed time whenever the key is checked.
    """

    name: ClassVar[str] = "token_bucket"

    def evaluate(
        self, state: TokenBucketState | None, now: float, quota: Quota, cost: int
    ) -> Evaluation[TokenBucketState]:
        """See :meth:`Algorithm.evaluate`."""
        rate = quota.rate
        if state is None:
            tokens, updated_at = float(quota.limit), now
        else:
            elapsed = max(0.0, now - state.updated_at)  # a clock stepping back adds nothing
            tokens = min(float(quota.limit), state.tokens + elapsed * rate)
            updated_at = max(now, state.updated_at)
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        reset_at = now + (quota.limit - tokens) / rate
        result = RateLimitResult(
            allowed=allowed,
            limit=quota.limit,
            remaining=math.floor(tokens),
            retry_after=retry_after_for(allowed, cost, quota, (cost - tokens) / rate),
            reset_at=reset_at,
        )
        new_state = TokenBucketState(tokens, updated_at)
        # Once the bucket would be full again, the stored state equals a fresh key's.
        return Evaluation(result=result, commit=lambda: new_state, expires_at=reset_at)
