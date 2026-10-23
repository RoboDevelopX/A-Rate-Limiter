"""Limits configuration: tiers, per-model overrides, concurrency caps, loaded from YAML."""

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, model_validator

from mlratelimit.algorithms import ALGORITHMS
from mlratelimit.composite import NamedQuota, Unit
from mlratelimit.types import Quota


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuotaSpec(_Strict):
    """One quota, e.g. ``{name: tpm, limit: 40000, period: 60, unit: tokens}``."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    limit: PositiveInt
    period: PositiveFloat
    unit: Unit = Unit.REQUESTS

    def to_named_quota(self) -> NamedQuota:
        """Convert to the runtime type."""
        return NamedQuota(self.name, Quota(self.limit, self.period), self.unit)


def _unique_names(quotas: list[QuotaSpec]) -> list[QuotaSpec]:
    names = [q.name for q in quotas]
    if len(set(names)) != len(names):
        raise ValueError(f"quota names must be unique within a rule, got {names}")
    return quotas


class TierSpec(_Strict):
    """Quotas for one tier: a default rule, plus overrides for specific models."""

    default: list[QuotaSpec] = Field(min_length=1)
    models: dict[str, list[QuotaSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_rules(self) -> Self:
        _unique_names(self.default)
        for rule in self.models.values():
            if not rule:
                raise ValueError("a model rule needs at least one quota")
            _unique_names(rule)
        return self


class ConcurrencySpec(_Strict):
    """Maximum in-flight requests per model (across all tiers)."""

    default: PositiveInt = 8
    models: dict[str, PositiveInt] = Field(default_factory=dict)

    def limit_for(self, model: str) -> int:
        """The cap for ``model``, or the default."""
        return self.models.get(model, self.default)


class LimitsConfig(_Strict):
    """The whole limits file.

    Example::

        algorithm: sliding_counter
        global_limit: {name: per_ip, limit: 100, period: 10}
        tiers:
          free:
            default:
              - {name: rpm, limit: 10, period: 60}
              - {name: tpm, limit: 5000, period: 60, unit: tokens}
        concurrency: {default: 4}
    """

    algorithm: str = "sliding_counter"
    global_limit: QuotaSpec | None = None
    tiers: dict[str, TierSpec] = Field(min_length=1)
    concurrency: ConcurrencySpec = Field(default_factory=ConcurrencySpec)

    @model_validator(mode="after")
    def _check_algorithm(self) -> Self:
        if self.algorithm not in ALGORITHMS:
            raise ValueError(
                f"unknown algorithm {self.algorithm!r}; choose from {list(ALGORITHMS)}"
            )
        return self

    def rule_for(self, tier: str, model: str) -> tuple[str, list[QuotaSpec]]:
        """Return ``(rule_name, quotas)`` for a tier and model.

        Models without an override share the tier's default rule (and its budget), so
        callers cannot get fresh quotas by inventing model names.

        Raises:
            KeyError: If ``tier`` is not configured.
        """
        spec = self.tiers[tier]
        if model in spec.models:
            return f"{tier}:{model}", spec.models[model]
        return f"{tier}:default", spec.default


def load_config(path: str | Path) -> LimitsConfig:
    """Load and validate a limits YAML file."""
    return parse_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def parse_config(data: Any) -> LimitsConfig:
    """Validate already-parsed YAML/JSON data."""
    return LimitsConfig.model_validate(data)
