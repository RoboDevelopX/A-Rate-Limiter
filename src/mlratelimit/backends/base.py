"""The storage contract shared by the in-memory and Redis backends."""

from collections.abc import Sequence
from typing import Any, Protocol

from mlratelimit.algorithms.base import Algorithm
from mlratelimit.types import LimitRequest, RateLimitResult


class Backend(Protocol):
    """Stores algorithm state and applies checks atomically."""

    async def apply(
        self, algorithm: Algorithm[Any], requests: Sequence[LimitRequest]
    ) -> list[RateLimitResult]:
        """Check every request in one atomic step.

        All-or-nothing: state is updated only if every request is allowed, so a request that
        fails one quota spends nothing from the others. Results come back in request order.
        """
        ...


def ensure_unique_keys(requests: Sequence[LimitRequest]) -> None:
    """Reject batches that name the same key twice, which would double-count one quota."""
    keys = [request.key for request in requests]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate keys in one batch: {keys}")
