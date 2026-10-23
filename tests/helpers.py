from collections.abc import Callable
from typing import Any

from mlratelimit import Algorithm, Limiter, Quota

# A start time that is a multiple of every period used in tests, so windows line up.
T0 = 60.0 * 28_333_333

MakeLimiter = Callable[[Algorithm[Any], Quota], Limiter]
