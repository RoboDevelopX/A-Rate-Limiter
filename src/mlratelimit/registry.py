"""Builds and caches one limiter per (tier, model) rule from a :class:`LimitsConfig`."""

from mlratelimit.algorithms import ALGORITHMS
from mlratelimit.backends.base import Backend
from mlratelimit.composite import CompositeLimiter
from mlratelimit.config import LimitsConfig
from mlratelimit.limiter import Limiter


class LimiterRegistry:
    """Hands out the limiter for a tier and model, creating each one once.

    Args:
        config: Validated limits.
        backend: Shared by every limiter.
        prefix: Storage key namespace.
    """

    def __init__(self, config: LimitsConfig, backend: Backend, *, prefix: str = "rl") -> None:
        self._config = config
        self._backend = backend
        self._prefix = prefix
        self._algorithm = ALGORITHMS[config.algorithm]
        self._limiters: dict[str, CompositeLimiter] = {}

    def for_rule(self, tier: str, model: str) -> CompositeLimiter:
        """The limiter for ``tier`` and ``model``. Raises ``KeyError`` for unknown tiers."""
        name, quotas = self._config.rule_for(tier, model)
        limiter = self._limiters.get(name)
        if limiter is None:
            limiter = CompositeLimiter(
                self._algorithm,
                [q.to_named_quota() for q in quotas],
                self._backend,
                name=name,
                prefix=self._prefix,
            )
            self._limiters[name] = limiter
        return limiter

    def global_limiter(self) -> Limiter | None:
        """The per-client limit applied to every request, if configured."""
        spec = self._config.global_limit
        if spec is None:
            return None
        return Limiter(
            self._algorithm,
            spec.to_named_quota().quota,
            self._backend,
            name=f"global:{spec.name}",
            prefix=self._prefix,
        )
