"""The contract every rate limiting algorithm implements."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from mlratelimit.types import Quota, RateLimitResult


@dataclass(frozen=True, slots=True)
class Evaluation[S]:
    """The decision for one key, plus how to persist it.

    Attributes:
        result: What the caller sees.
        commit: Builds the state to store. Backends call it only when every request in the
            batch is allowed, so a denied batch leaves all state untouched.
        expires_at: Unix time after which the committed state carries no information and
            can be dropped (the next check would behave as if the key were new).
    """

    result: RateLimitResult
    commit: Callable[[], S]
    expires_at: float


class Algorithm[S](ABC):
    """A pure state transition: ``(state, now, quota, cost) -> Evaluation``.

    Implementations hold no per-key data themselves; backends store the state. The same
    logic is mirrored by a Lua script of the same ``name`` for the Redis backend.
    """

    name: ClassVar[str]

    @abstractmethod
    def evaluate(self, state: S | None, now: float, quota: Quota, cost: int) -> Evaluation[S]:
        """Decide whether ``cost`` units fit at time ``now`` given the stored ``state``.

        ``state`` is ``None`` for a key that has never been seen or has expired.
        """


def retry_after_for(allowed: bool, cost: int, quota: Quota, wait: float) -> float | None:
    """Shared retry hint: 0 when allowed, ``None`` when ``cost`` can never fit, else ``wait``."""
    if allowed:
        return 0.0
    if cost > quota.limit:
        return None
    return max(0.0, wait)
