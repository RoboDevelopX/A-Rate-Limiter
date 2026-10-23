"""Leaky bucket as a meter: a bucket that drains at a constant rate and rejects overflow."""

import math
from dataclasses import dataclass
from typing import ClassVar

from mlratelimit.algorithms.base import Algorithm, Evaluation, retry_after_for
from mlratelimit.types import Quota, RateLimitResult


@dataclass(frozen=True, slots=True)
class LeakyBucketState:
    """Water level at ``updated_at``; draining since then is computed on the next check."""

    level: float
    updated_at: float


class LeakyBucket(Algorithm[LeakyBucketState]):
    """Capacity ``limit`` units, draining at ``limit / period`` units per second.

    Each request pours ``cost`` units in; if that would overflow, the request is rejected
    (the *meter* variant, not the *queue* variant that delays requests). As a meter it makes
    exactly the same decisions as a token bucket: the level is ``limit - tokens``. It is kept
    as its own algorithm because many systems describe their limits this way, and the
    property tests prove the two agree.
    """

    name: ClassVar[str] = "leaky_bucket"

    def evaluate(
        self, state: LeakyBucketState | None, now: float, quota: Quota, cost: int
    ) -> Evaluation[LeakyBucketState]:
        """See :meth:`Algorithm.evaluate`."""
        rate = quota.rate
        if state is None:
            level, updated_at = 0.0, now
        else:
            elapsed = max(0.0, now - state.updated_at)  # a clock stepping back drains nothing
            level = max(0.0, state.level - elapsed * rate)
            updated_at = max(now, state.updated_at)
        allowed = level + cost <= quota.limit
        if allowed:
            level += cost
        reset_at = now + level / rate
        result = RateLimitResult(
            allowed=allowed,
            limit=quota.limit,
            remaining=math.floor(quota.limit - level),
            retry_after=retry_after_for(allowed, cost, quota, (level + cost - quota.limit) / rate),
            reset_at=reset_at,
        )
        new_state = LeakyBucketState(level, updated_at)
        return Evaluation(result=result, commit=lambda: new_state, expires_at=reset_at)
