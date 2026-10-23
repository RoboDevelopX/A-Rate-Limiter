"""Injectable time sources, so algorithms can be tested without sleeping."""

import time
from typing import Protocol


class Clock(Protocol):
    """Anything that returns the current Unix time in seconds."""

    def now(self) -> float:
        """Return the current Unix time in seconds."""
        ...


class SystemClock:
    """Wall-clock time from :func:`time.time`."""

    def now(self) -> float:
        """Return the current Unix time in seconds."""
        return time.time()


class ManualClock:
    """A clock that only moves when told to. Use it in tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        """Return the clock's current time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward (or backward, to simulate skew) by ``seconds``."""
        self._now += seconds

    def set(self, timestamp: float) -> None:
        """Jump to an absolute time."""
        self._now = timestamp
