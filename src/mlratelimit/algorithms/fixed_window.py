"""Fixed window counter: count units per calendar-aligned window."""

import math
from dataclasses import dataclass
from typing import ClassVar

from mlratelimit.algorithms.base import Algorithm, Evaluation, retry_after_for
from mlratelimit.types import Quota, RateLimitResult


@dataclass(frozen=True, slots=True)
class FixedWindowState:
    """Index of the current window (``floor(now / period)``) and units used in it."""

    window: int
    count: int


class FixedWindow(Algorithm[FixedWindowState]):
    """Allow ``limit`` units per window aligned to ``period`` boundaries.

    O(1) time and memory. Known flaw: a client can spend the full limit at the end of one
    window and again at the start of the next, so up to ``2 * limit`` units can pass within
    a span much shorter than ``period``.
    """

    name: ClassVar[str] = "fixed_window"

    def evaluate(
        self, state: FixedWindowState | None, now: float, quota: Quota, cost: int
    ) -> Evaluation[FixedWindowState]:
        """See :meth:`Algorithm.evaluate`."""
        window = math.floor(now / quota.period)
        count = 0
        if state is not None and state.window >= window:
            # Same window, or the clock stepped back: keep counting in the stored window.
            window, count = state.window, state.count
        reset_at = (window + 1) * quota.period
        allowed = count + cost <= quota.limit
        if allowed:
            count += cost
        result = RateLimitResult(
            allowed=allowed,
            limit=quota.limit,
            remaining=quota.limit - count,
            retry_after=retry_after_for(allowed, cost, quota, reset_at - now),
            reset_at=reset_at,
        )
        new_state = FixedWindowState(window, count)
        return Evaluation(result=result, commit=lambda: new_state, expires_at=reset_at)
