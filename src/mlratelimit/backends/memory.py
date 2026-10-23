"""In-process backend: a dict of per-key state with expiry."""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mlratelimit.algorithms.base import Algorithm, Evaluation
from mlratelimit.backends.base import ensure_unique_keys
from mlratelimit.clock import Clock, SystemClock
from mlratelimit.types import LimitRequest, RateLimitResult


@dataclass(slots=True)
class _Entry:
    state: Any
    expires_at: float


class MemoryBackend:
    """Keeps state in this process. Use it for a single worker, tests, or a local cache.

    Concurrency: :meth:`apply` reads, decides and writes without any ``await`` in between,
    so on one asyncio event loop no other coroutine can interleave with a check. It is not
    safe to share one instance across threads or processes.

    Memory: entries are kept in write order. Each check drops expired entries from the
    oldest end, so memory is bounded by the keys written within the longest quota period
    (two periods for the sliding window counter), at O(1) amortized cost per check and with
    no background task.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or SystemClock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    async def apply(
        self, algorithm: Algorithm[Any], requests: Sequence[LimitRequest]
    ) -> list[RateLimitResult]:
        """See :meth:`mlratelimit.backends.base.Backend.apply`."""
        ensure_unique_keys(requests)
        now = self._clock.now()
        # No await below this line: the whole check is one uninterrupted step on the loop.
        self._evict_expired(now)
        evaluations: list[tuple[str, Evaluation[Any]]] = []
        for request in requests:
            entry = self._entries.get(request.key)
            state = entry.state if entry is not None and entry.expires_at > now else None
            evaluation = algorithm.evaluate(state, now, request.quota, request.cost)
            evaluations.append((request.key, evaluation))
        if all(evaluation.result.allowed for _, evaluation in evaluations):
            for key, evaluation in evaluations:
                self._entries[key] = _Entry(evaluation.commit(), evaluation.expires_at)
                self._entries.move_to_end(key)
        return [evaluation.result for _, evaluation in evaluations]

    def _evict_expired(self, now: float) -> None:
        while self._entries:
            key, entry = next(iter(self._entries.items()))
            if entry.expires_at > now:
                return
            del self._entries[key]
