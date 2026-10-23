import pytest

from mlratelimit import FixedWindow, ManualClock, MemoryBackend, Quota, RateLimitResult
from mlratelimit.composite import CompositeLimiter, NamedQuota, Unit, most_restrictive


def make(clock: ManualClock) -> CompositeLimiter:
    quotas = [
        NamedQuota("rpm", Quota(limit=3, period=60)),
        NamedQuota("tpm", Quota(limit=100, period=60), Unit.TOKENS),
    ]
    return CompositeLimiter(FixedWindow(), quotas, MemoryBackend(clock), name="free:default")


async def test_request_quota_costs_one_and_token_quota_costs_cost(clock: ManualClock) -> None:
    limiter = make(clock)
    result = await limiter.check("u", cost=40)
    assert result.allowed
    # tpm has 60/100 left (0.6), rpm 2/3 left (0.67): tpm is the most restrictive.
    assert (result.limit, result.remaining) == (100, 60)


async def test_token_denial_spends_nothing_from_request_quota(clock: ManualClock) -> None:
    limiter = make(clock)
    denied = await limiter.check("u", cost=101)
    assert not denied.allowed
    assert denied.retry_after is None  # can never fit in 100 tokens
    for _ in range(3):  # all three requests are still available
        assert (await limiter.check("u", cost=1)).allowed
    assert not (await limiter.check("u", cost=1)).allowed


async def test_request_denial_reports_the_request_quota(clock: ManualClock) -> None:
    limiter = make(clock)
    for _ in range(3):
        await limiter.check("u")
    denied = await limiter.check("u")
    assert (denied.allowed, denied.limit) == (False, 3)
    assert limiter.name == "free:default"
    assert [q.name for q in limiter.quotas] == ["rpm", "tpm"]


async def test_rejects_invalid_cost(clock: ManualClock) -> None:
    with pytest.raises(ValueError, match="cost"):
        await make(clock).check("u", cost=0)


def test_needs_unique_non_empty_quotas(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    with pytest.raises(ValueError, match="at least one"):
        CompositeLimiter(FixedWindow(), [], backend, name="x")
    duplicate = [NamedQuota("a", Quota(1, 1)), NamedQuota("a", Quota(2, 1))]
    with pytest.raises(ValueError, match="unique"):
        CompositeLimiter(FixedWindow(), duplicate, backend, name="x")


def test_most_restrictive_prefers_never_over_longest_wait() -> None:
    wait = RateLimitResult(False, 10, 0, 5.0, 0.0)
    never = RateLimitResult(False, 10, 0, None, 0.0)
    ok = RateLimitResult(True, 10, 9, 0.0, 0.0)
    assert most_restrictive([ok, wait, never]) is never
    assert most_restrictive([ok, wait]) is wait
