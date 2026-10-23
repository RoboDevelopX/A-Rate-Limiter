from mlratelimit import ManualClock, MemoryBackend
from mlratelimit.config import parse_config
from mlratelimit.registry import LimiterRegistry

CONFIG = {
    "algorithm": "fixed_window",
    "tiers": {
        "free": {
            "default": [{"name": "rpm", "limit": 2, "period": 60}],
            "models": {"big": [{"name": "rpm", "limit": 1, "period": 60}]},
        }
    },
}


async def test_one_limiter_per_rule(clock: ManualClock) -> None:
    registry = LimiterRegistry(parse_config(CONFIG), MemoryBackend(clock))
    assert registry.for_rule("free", "a") is registry.for_rule("free", "b")
    assert registry.for_rule("free", "big") is not registry.for_rule("free", "a")


async def test_unlisted_models_share_the_default_budget(clock: ManualClock) -> None:
    registry = LimiterRegistry(parse_config(CONFIG), MemoryBackend(clock))
    assert (await registry.for_rule("free", "a").check("u")).allowed
    assert (await registry.for_rule("free", "b").check("u")).allowed
    assert not (await registry.for_rule("free", "c").check("u")).allowed
    assert (await registry.for_rule("free", "big").check("u")).allowed  # its own budget


async def test_global_limiter_is_optional(clock: ManualClock) -> None:
    backend = MemoryBackend(clock)
    assert LimiterRegistry(parse_config(CONFIG), backend).global_limiter() is None
    with_global = {**CONFIG, "global_limit": {"name": "per_ip", "limit": 1, "period": 10}}
    limiter = LimiterRegistry(parse_config(with_global), backend).global_limiter()
    assert limiter is not None
    assert (await limiter.check("ip:1")).allowed
    assert not (await limiter.check("ip:1")).allowed
