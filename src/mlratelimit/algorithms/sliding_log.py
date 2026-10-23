"""Sliding window log: remember every allowed request inside the trailing window."""

from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

from mlratelimit.algorithms.base import Algorithm, Evaluation, retry_after_for
from mlratelimit.types import Quota, RateLimitResult


@dataclass(slots=True)
class SlidingLogState:
    """Allowed requests as ``(timestamp, cost)``, oldest first, plus their running total."""

    entries: deque[tuple[float, int]] = field(default_factory=deque)
    total: int = 0


class SlidingLog(Algorithm[SlidingLogState]):
    """Exact: allow a request only if the units used in ``(now - period, now]`` fit.

    An entry stops counting exactly ``period`` seconds after it was recorded. Exact at every
    instant, with no boundary burst, but memory is O(requests in the window) per key, so it
    suits low limits where precision matters (e.g. expensive model calls, abuse limits).
    """

    name: ClassVar[str] = "sliding_log"

    def evaluate(
        self, state: SlidingLogState | None, now: float, quota: Quota, cost: int
    ) -> Evaluation[SlidingLogState]:
        """See :meth:`Algorithm.evaluate`."""
        log = state if state is not None else SlidingLogState()
        if log.entries:
            now = max(now, log.entries[-1][0])  # a clock stepping back must not revive entries
        # Dropping entries that no longer count is safe even if this check is not committed.
        cutoff = now - quota.period
        while log.entries and log.entries[0][0] <= cutoff:
            log.total -= log.entries.popleft()[1]

        allowed = log.total + cost <= quota.limit
        used = log.total + cost if allowed else log.total
        newest = now if allowed else (log.entries[-1][0] if log.entries else now)
        reset_at = newest + quota.period if used else now
        wait = 0.0 if allowed or cost > quota.limit else self._wait(log, now, quota, cost)
        result = RateLimitResult(
            allowed=allowed,
            limit=quota.limit,
            remaining=quota.limit - used,
            retry_after=retry_after_for(allowed, cost, quota, wait),
            reset_at=reset_at,
        )

        def commit() -> SlidingLogState:
            log.entries.append((now, cost))
            log.total += cost
            return log

        return Evaluation(result=result, commit=commit, expires_at=reset_at)

    @staticmethod
    def _wait(log: SlidingLogState, now: float, quota: Quota, cost: int) -> float:
        """Seconds until enough old entries expire for ``cost`` (<= limit) to fit."""
        must_free = log.total + cost - quota.limit
        freed = 0
        for timestamp, entry_cost in log.entries:
            freed += entry_cost
            if freed >= must_free:
                return timestamp + quota.period - now
        raise AssertionError(
            "unreachable: the whole log frees at least cost - limit units"
        )  # pragma: no cover
