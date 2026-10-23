"""Value types shared by every algorithm and backend."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Quota:
    """Allow ``limit`` units per ``period`` seconds.

    For the bucket algorithms ``limit`` is also the bucket capacity (the largest burst) and
    ``limit / period`` is the refill (or leak) rate.
    """

    limit: int
    period: float

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"limit must be >= 1, got {self.limit}")
        if self.period <= 0:
            raise ValueError(f"period must be > 0, got {self.period}")

    @property
    def rate(self) -> float:
        """Units replenished per second."""
        return self.limit / self.period


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of one rate limit check.

    Attributes:
        allowed: Whether the request may proceed. A denied request consumes nothing.
        limit: The quota's ``limit``.
        remaining: Units still available right after this check (never negative).
        retry_after: Seconds until a request with the same cost could succeed. ``0.0`` when
            allowed, ``None`` when the cost exceeds ``limit`` and can never succeed.
        reset_at: Unix time (seconds) at which ``remaining`` is back to ``limit``.
    """

    allowed: bool
    limit: int
    remaining: int
    retry_after: float | None
    reset_at: float


@dataclass(frozen=True, slots=True)
class LimitRequest:
    """One quota to check inside an atomic batch sent to a backend.

    ``key`` is the full storage key and must be unique per algorithm and quota.
    """

    key: str
    quota: Quota
    cost: int
