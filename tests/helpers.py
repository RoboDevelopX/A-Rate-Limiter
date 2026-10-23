import os
from collections.abc import Callable
from typing import Any

from mlratelimit import Algorithm, Limiter, Quota, RateLimitResult

# A start time that is a multiple of every period used in tests, so windows line up.
T0 = 60.0 * 28_333_333

# Database 15 by default so a local run never touches data in database 0.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")

MakeLimiter = Callable[[Algorithm[Any], Quota], Limiter]


class Simulator:
    """Drives one algorithm for one key synchronously, the way a backend does.

    With ``expire=False`` state is never dropped, which lets tests check that expiry is
    invisible to callers.
    """

    def __init__(self, algorithm: Algorithm[Any], quota: Quota, *, expire: bool = True) -> None:
        self.algorithm = algorithm
        self.quota = quota
        self.expire = expire
        self.state: Any = None
        self.expires_at = float("-inf")

    def check(self, now: float, cost: int) -> RateLimitResult:
        state = self.state if not self.expire or self.expires_at > now else None
        evaluation = self.algorithm.evaluate(state, now, self.quota, cost)
        if evaluation.result.allowed:
            self.state = evaluation.commit()
            self.expires_at = evaluation.expires_at
        return evaluation.result
