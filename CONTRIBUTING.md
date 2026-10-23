# Contributing

How to work on this repository: commands, conventions and architecture.

## What this is

`mlratelimit`: an async Python rate limiting library for ML/LLM inference APIs, plus a demo
FastAPI service. Five algorithms behind one `RateLimiter` interface, in-memory and Redis backends.

## Commands

| Task | Command |
| --- | --- |
| Install (from lockfile) | `make install` (`uv sync --locked`) |
| Lint + format check | `make lint` |
| Auto-fix + format | `make format` |
| Type check (mypy --strict on `src/`) | `make typecheck` |
| Tests with coverage | `make test` |
| Everything CI runs | `make check` |

## Conventions

- Python 3.12, `src/` layout, dependencies managed by uv (`uv.lock` is committed).
- Fully typed; `mypy --strict` must pass on `src/`. Tests are type-annotated but not strict-checked.
- Small focused modules; docstrings on every public class and function.
- No bare `except`; catch the narrowest exception that can actually happen.
- Library code logs through `logging.getLogger("mlratelimit...")` and never configures logging itself.
- Tests must be deterministic: inject a clock instead of sleeping.
- One conventional commit per milestone (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- After each milestone, add 3-5 notes to `LEARNING.md` and keep this file current.

## Architecture

```
src/mlratelimit/
  types.py            Quota, RateLimitResult, LimitRequest (frozen dataclasses)
  clock.py            Clock protocol, SystemClock, ManualClock (tests)
  limiter.py          RateLimiter protocol; Limiter = algorithm + quota + backend
  algorithms/         pure transitions: evaluate(state, now, quota, cost) -> Evaluation
    base.py           Algorithm ABC, Evaluation (result, commit(), expires_at)
    fixed_window.py   token_bucket.py
  backends/
    base.py           Backend protocol: apply(algorithm, [LimitRequest]) -> [RateLimitResult]
    memory.py         OrderedDict store, write-order eviction of expired entries
```

Key rules:

- Algorithms never store state; backends do. `evaluate` must not change semantics of the
  stored state; persisting happens only through `Evaluation.commit()`.
- `Backend.apply` is atomic and all-or-nothing across the batch (a denied quota charges no
  other quota in the same batch).
- Memory backend: no `await` between reading and writing state. Keep it that way.
- Storage keys look like `rl:{<caller>}:<name>:<algorithm>`; the braces are a Redis Cluster
  hash tag.
- Tests: `tests/algorithms/` use the `make_limiter` fixture, which is parametrized over
  backends, so every behaviour test runs against every backend. Shared constants live in
  `tests/helpers.py` (`T0` is aligned to every test period).
