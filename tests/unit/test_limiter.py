import pytest

from mlratelimit import FixedWindow, Limiter, ManualClock, MemoryBackend, Quota, TokenBucket
from mlratelimit.limiter import storage_key


@pytest.mark.parametrize("cost", [0, -1])
async def test_rejects_non_positive_cost(clock: ManualClock, cost: int) -> None:
    limiter = Limiter(FixedWindow(), Quota(limit=1, period=1), MemoryBackend(clock))
    with pytest.raises(ValueError, match="cost must be"):
        await limiter.check("k", cost=cost)


async def test_limiters_with_different_names_do_not_share_state(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    quota = Quota(limit=1, period=60)
    rpm = Limiter(FixedWindow(), quota, backend, name="rpm")
    other = Limiter(FixedWindow(), quota, backend, name="other")
    assert (await rpm.check("k")).allowed
    assert not (await rpm.check("k")).allowed
    assert (await other.check("k")).allowed
    assert rpm.quota == quota


def test_storage_key_uses_a_cluster_hash_tag() -> None:
    assert storage_key("rl", "user:42", "tpm", TokenBucket()) == "rl:{user:42}:tpm:token_bucket"
