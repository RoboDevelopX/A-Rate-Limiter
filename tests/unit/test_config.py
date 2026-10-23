from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from mlratelimit.composite import Unit
from mlratelimit.config import load_config, parse_config
from mlratelimit.types import Quota

BASE: dict[str, Any] = {
    "tiers": {
        "free": {
            "default": [
                {"name": "rpm", "limit": 5, "period": 60},
                {"name": "tpm", "limit": 2000, "period": 60, "unit": "tokens"},
            ],
            "models": {"big": [{"name": "rpm", "limit": 1, "period": 60}]},
        }
    }
}


def test_rule_for_uses_model_override_or_tier_default() -> None:
    config = parse_config(BASE)
    name, quotas = config.rule_for("free", "big")
    assert (name, [q.limit for q in quotas]) == ("free:big", [1])
    name, quotas = config.rule_for("free", "anything-else")
    assert name == "free:default"
    assert quotas[1].to_named_quota().quota == Quota(2000, 60)
    assert quotas[1].unit is Unit.TOKENS
    with pytest.raises(KeyError):
        config.rule_for("enterprise", "big")


def test_defaults() -> None:
    config = parse_config(BASE)
    assert config.algorithm == "sliding_counter"
    assert config.global_limit is None
    assert config.concurrency.limit_for("big") == 8


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"algorithm": "magic"}, "unknown algorithm"),
        ({"surprise": 1}, "Extra inputs"),
        ({"tiers": {}}, "at least 1"),
        ({"tiers": {"t": {"default": []}}}, "at least 1"),
        ({"tiers": {"t": {"default": [{"name": "a", "limit": 0, "period": 1}]}}}, "greater than"),
        (
            {"tiers": {"t": {"default": [{"name": "a", "limit": 1, "period": 1}] * 2}}},
            "unique",
        ),
        (
            {
                "tiers": {
                    "t": {"default": [{"name": "a", "limit": 1, "period": 1}], "models": {"m": []}}
                }
            },
            "at least one quota",
        ),
        (
            {
                "tiers": {
                    "t": {
                        "default": [{"name": "a", "limit": 1, "period": 1}],
                        "models": {"m": [{"name": "a", "limit": 1, "period": 1}] * 2},
                    }
                }
            },
            "unique",
        ),
    ],
)
def test_invalid_configs_are_rejected(patch: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_config({**BASE, **patch})


def test_load_config_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text(
        "algorithm: token_bucket\n"
        "global_limit: {name: per_ip, limit: 10, period: 1}\n"
        "tiers:\n  free:\n    default:\n      - {name: rpm, limit: 5, period: 60}\n"
        "concurrency: {default: 2, models: {big: 1}}\n"
    )
    config = load_config(path)
    assert config.algorithm == "token_bucket"
    assert config.concurrency.limit_for("big") == 1
    assert config.global_limit is not None
