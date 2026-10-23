"""Sliding window counter: blend the previous window's count into the current one."""

import math
from dataclasses import dataclass
from typing import ClassVar

from mlratelimit.algorithms.base import Algorithm, Evaluation, retry_after_for
from mlratelimit.types import Quota, RateLimitResult


@dataclass(frozen=True, slots=True)
class SlidingCounterState:
    """Counts for the current aligned window and the one before it."""

    window: int
    current: int
    previous: int


class SlidingCounter(Algorithm[SlidingCounterState]):
    """Approximate sliding window from two fixed-window counters.

    ``estimate = previous * (1 - elapsed / period) + current``, which assumes the previous
    window's requests were spread evenly. O(1) memory like fixed window, without its
    boundary burst; the price is that the estimate can be off when traffic is bursty.
    """

    name: ClassVar[str] = "sliding_counter"

    def evaluate(
        self, state: SlidingCounterState | None, now: float, quota: Quota, cost: int
    ) -> Evaluation[SlidingCounterState]:
        """See :meth:`Algorithm.evaluate`."""
        period = quota.period
        window = math.floor(now / period)
        current = previous = 0
        if state is not None:
            if state.window >= window:  # same window, or the clock stepped back
                window, current, previous = state.window, state.current, state.previous
            elif state.window == window - 1:
                previous = state.current
        start = window * period
        elapsed = min(max(now - start, 0.0), period)
        weight = (period - elapsed) / period

        allowed = previous * weight + current + cost <= quota.limit
        if allowed:
            current += cost
        used = previous * weight + current
        if current:
            reset_at = start + 2 * period
        elif previous:
            reset_at = start + period
        else:
            reset_at = now
        wait = 0.0 if allowed else self._wait(quota, start, current, previous, cost) - now
        result = RateLimitResult(
            allowed=allowed,
            limit=quota.limit,
            remaining=max(0, math.floor(quota.limit - used)),
            retry_after=retry_after_for(allowed, cost, quota, wait),
            reset_at=reset_at,
        )
        new_state = SlidingCounterState(window, current, previous)
        return Evaluation(result=result, commit=lambda: new_state, expires_at=start + 2 * period)

    @staticmethod
    def _wait(quota: Quota, start: float, current: int, previous: int, cost: int) -> float:
        """Earliest time at which ``cost`` fits, assuming no other traffic arrives."""
        limit, period = quota.limit, quota.period
        if current + cost <= limit:
            # Fits later in this window, once the previous window's weight has decayed enough.
            return start + period * (1 - (limit - cost - current) / previous)
        if current == 0:
            return start  # only when cost > limit; retry_after_for returns None then
        # Next window: this window's count becomes "previous" and decays from there.
        return start + period + period * (1 - (limit - cost) / current)
