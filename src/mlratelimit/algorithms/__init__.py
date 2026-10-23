"""Rate limiting algorithms. Each is a pure state transition; backends store the state."""

from typing import Any

from mlratelimit.algorithms.base import Algorithm, Evaluation
from mlratelimit.algorithms.fixed_window import FixedWindow
from mlratelimit.algorithms.leaky_bucket import LeakyBucket
from mlratelimit.algorithms.sliding_counter import SlidingCounter
from mlratelimit.algorithms.sliding_log import SlidingLog
from mlratelimit.algorithms.token_bucket import TokenBucket

ALGORITHMS: dict[str, Algorithm[Any]] = {
    algorithm.name: algorithm
    for algorithm in (FixedWindow(), SlidingLog(), SlidingCounter(), TokenBucket(), LeakyBucket())
}
"""Every algorithm by its ``name``, for configuration files."""

__all__ = [
    "ALGORITHMS",
    "Algorithm",
    "Evaluation",
    "FixedWindow",
    "LeakyBucket",
    "SlidingCounter",
    "SlidingLog",
    "TokenBucket",
]
