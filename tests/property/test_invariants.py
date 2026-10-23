"""Property tests: invariants that must hold for any traffic pattern hypothesis can invent.

Times are multiples of 1/64 s and periods are powers of two, so the float arithmetic in
the algorithms is exact and the invariants can be checked without tolerances.
"""

import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mlratelimit import Algorithm, Quota, RateLimitResult
from mlratelimit.algorithms import (
    ALGORITHMS,
    FixedWindow,
    LeakyBucket,
    SlidingCounter,
    SlidingLog,
    TokenBucket,
)
from tests.helpers import T0, Simulator

quotas = st.builds(
    Quota, limit=st.integers(1, 20), period=st.sampled_from([0.5, 1.0, 2.0, 4.0, 8.0])
)
# (seconds since the previous request, cost); some costs exceed any limit on purpose.
steps = st.lists(st.tuples(st.integers(0, 16 * 64).map(lambda n: n / 64), st.integers(1, 25)))

Trace = list[tuple[float, int, RateLimitResult]]


def run(algorithm: Algorithm[Any], quota: Quota, ops: list[tuple[float, int]]) -> Trace:
    sim, now, trace = Simulator(algorithm, quota), T0, []
    for dt, cost in ops:
        now += dt
        trace.append((now, cost, sim.check(now, cost)))
    return trace


def allowed_between(trace: Trace, start: float, end: float, *, open_start: bool) -> int:
    return sum(
        cost
        for t, cost, r in trace
        if r.allowed and (start < t if open_start else start <= t) and t <= end
    )


every_algorithm = pytest.mark.parametrize("algorithm", ALGORITHMS.values(), ids=ALGORITHMS.keys())


@every_algorithm
@given(quota=quotas, ops=steps)
def test_result_fields_are_consistent(
    algorithm: Algorithm[Any], quota: Quota, ops: list[tuple[float, int]]
) -> None:
    for now, cost, r in run(algorithm, quota, ops):
        assert r.limit == quota.limit
        assert 0 <= r.remaining <= quota.limit
        assert r.reset_at >= now
        if r.allowed:
            assert r.retry_after == 0.0
        elif cost > quota.limit:
            assert r.retry_after is None
        else:
            assert r.retry_after is not None
            assert r.retry_after > 0


@every_algorithm
@given(quota=quotas, ops=steps)
def test_dropping_expired_state_is_invisible(
    algorithm: Algorithm[Any], quota: Quota, ops: list[tuple[float, int]]
) -> None:
    """``expires_at`` is honest: forgetting state after it changes no decision."""
    expiring, keeping = Simulator(algorithm, quota), Simulator(algorithm, quota, expire=False)
    now = T0
    for dt, cost in ops:
        now += dt
        assert expiring.check(now, cost) == keeping.check(now, cost)


@every_algorithm
@given(quota=quotas, ops=steps, cost=st.integers(1, 20))
def test_retry_after_is_exact(
    algorithm: Algorithm[Any], quota: Quota, ops: list[tuple[float, int]], cost: int
) -> None:
    """Retrying after ``retry_after`` succeeds, and retrying 1 ms earlier does not."""
    sim, now = Simulator(algorithm, quota), T0
    for dt, op_cost in ops:
        now += dt
        sim.check(now, op_cost)
    result = sim.check(now, cost)
    if result.allowed or result.retry_after is None:
        return
    if result.retry_after > 1e-3:
        assert not sim.check(now + result.retry_after - 1e-3, cost).allowed
    assert sim.check(now + result.retry_after + 1e-6, cost).allowed


@given(quota=quotas, ops=steps)
def test_sliding_log_is_exact(quota: Quota, ops: list[tuple[float, int]]) -> None:
    """In every trailing window: never more than the limit, and never a needless denial."""
    trace = run(SlidingLog(), quota, ops)
    for i, (now, cost, r) in enumerate(trace):
        used = allowed_between(trace[: i + 1], now - quota.period, now, open_start=True)
        assert used <= quota.limit
        if not r.allowed:
            assert used + cost > quota.limit


@given(quota=quotas, ops=steps)
def test_fixed_window_never_exceeds_limit_in_a_window(
    quota: Quota, ops: list[tuple[float, int]]
) -> None:
    trace = run(FixedWindow(), quota, ops)
    for now, _, _ in trace:
        start = math.floor(now / quota.period) * quota.period
        assert allowed_between(trace, start, start + quota.period - 1e-9, open_start=False) <= (
            quota.limit
        )


@given(quota=quotas, ops=steps)
def test_token_bucket_never_exceeds_burst_plus_refill(
    quota: Quota, ops: list[tuple[float, int]]
) -> None:
    """Over any interval [a, b], allowed units <= capacity + rate * (b - a)."""
    trace = run(TokenBucket(), quota, ops)
    times = [t for t, _, r in trace if r.allowed]
    for a in times:
        for b in times:
            if a <= b:
                used = allowed_between(trace, a, b, open_start=False)
                assert used <= quota.limit + quota.rate * (b - a)


@given(quota=quotas, ops=steps)
def test_sliding_counter_bounds(quota: Quota, ops: list[tuple[float, int]]) -> None:
    """An aligned window never exceeds the limit; any trailing window stays under 2x."""
    trace = run(SlidingCounter(), quota, ops)
    for now, _, _ in trace:
        start = math.floor(now / quota.period) * quota.period
        assert allowed_between(trace, start, now, open_start=False) <= quota.limit
        assert allowed_between(trace, now - quota.period, now, open_start=True) <= 2 * quota.limit


@given(quota=quotas, ops=steps)
def test_leaky_bucket_decides_exactly_like_token_bucket(
    quota: Quota, ops: list[tuple[float, int]]
) -> None:
    """A leaky bucket used as a meter is a token bucket seen upside down."""
    leaky, token = run(LeakyBucket(), quota, ops), run(TokenBucket(), quota, ops)
    for (_, _, a), (_, _, b) in zip(leaky, token, strict=True):
        assert (a.allowed, a.remaining, a.retry_after, a.reset_at) == (
            b.allowed,
            b.remaining,
            b.retry_after,
            b.reset_at,
        )
