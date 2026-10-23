import time

from mlratelimit import ManualClock, SystemClock


def test_system_clock_tracks_wall_time() -> None:
    before = time.time()
    now = SystemClock().now()
    assert before <= now <= time.time()


def test_manual_clock_moves_only_when_told() -> None:
    clock = ManualClock(100.0)
    assert clock.now() == 100.0
    clock.advance(2.5)
    assert clock.now() == 102.5
    clock.advance(-1.0)
    assert clock.now() == 101.5
    clock.set(7.0)
    assert clock.now() == 7.0
