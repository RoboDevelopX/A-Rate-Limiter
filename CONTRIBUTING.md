# Contributing

How to work on this repository: commands, conventions and architecture.

## What this is

`mlratelimit`: an async Python rate limiting library for ML/LLM inference APIs, plus a demo
FastAPI service. Five algorithms behind one `RateLimiter` interface, in-memory and Redis backends.

## Commands

| Task | Command |
| --- | --- |
| Install (from lockfile, all extras) | `make install` (`uv sync --locked --all-extras`) |
| Lint + format check | `make lint` |
| Auto-fix + format | `make format` |
| Type check (mypy --strict on `src/`) | `make typecheck` |
| Tests with coverage | `make test` |
| Tests without Redis | `make test-unit` |
| Start a local Redis | `make redis` (or `redis-server --daemonize yes`) |
| Run the demo API | `make run` (memory backend; `REDIS_URL=redis://localhost:6379/0 make run` for Redis) |
| Demo + Redis in Docker | `make up` (detached, waits for health), `make down` |
| End-to-end smoke test | `make smoke` (against a running `make up`) |
| Benchmarks | `make bench` (needs Redis; writes `bench/results/*.json` and `*.md`) |
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
    fixed_window.py  sliding_log.py  sliding_counter.py  token_bucket.py  leaky_bucket.py
    __init__.py       ALGORITHMS: name -> instance (used by config and parametrized tests)
  backends/
    base.py           Backend protocol: apply(algorithm, [LimitRequest]) -> [RateLimitResult]
    memory.py         OrderedDict store, write-order eviction of expired entries
    redis.py          RedisBackend: one Lua script per algorithm via EVALSHA
    lua/              _prelude.lua + <algorithm>.lua + _driver.lua, concatenated at load
  composite.py        CompositeLimiter: several NamedQuotas (requests or tokens) in one batch
  config.py           pydantic LimitsConfig (tiers x models, global limit, concurrency) + YAML
  registry.py         LimiterRegistry: one CompositeLimiter per (tier, model) rule
  resilience.py       CircuitBreaker, FailureGuard (timeout + breaker), ResilientBackend
  concurrency.py      in-flight caps: MemoryConcurrencyLimiter, RedisConcurrencyLimiter
  keys.py             ClientIP (trusted proxies), api_key (hashed), user_id (request.state)
  metrics.py          Prometheus Metrics + InstrumentedLimiter wrapper
  log.py              JsonFormatter, configure_logging (apps call it, the library never does)
  fastapi/            RateLimitMiddleware, RateLimitDependency, ConcurrencyDependency, headers
demo/
  app.py              create_app(Settings): /predict, /healthz, /metrics
  limits.yaml         demo limits, fake models, demo-only API keys
bench/
  run.py              latency/throughput per algorithm x backend, with CPU accounting
  accuracy.py         peak-in-window accuracy and Redis bytes per key
  results/            committed outputs; README tables are copied from here
scripts/smoke.sh      end-to-end check used by CI's docker job
```

Key rules:

- Algorithms never store state; backends do. `evaluate` must not change semantics of the
  stored state; persisting happens only through `Evaluation.commit()`.
- `Backend.apply` is atomic and all-or-nothing across the batch (a denied quota charges no
  other quota in the same batch).
- Memory backend: no `await` between reading and writing state. Keep it that way.
- Each `lua/<name>.lua` mirrors `algorithms/<name>.py` operation for operation, so floats
  match bit for bit. Change both together; `tests/integration/test_parity.py` compares them.
- Lua scripts use Redis `TIME` unless the backend has a test clock. Floats cross the
  Lua/Redis boundary only as `fmt()` strings (`%.17g`); never pass a raw Lua float.
- Metric labels hold rule names (`free:default`), never caller keys: keep cardinality bounded.
- Demo `/predict` dependency order is auth -> GPU slot -> rate limit, so a 503 (capacity) does
  not charge quota. 429 = caller's quota; 503 = server capacity.
- README numbers must come from `bench/results/` (rerun `make bench` after changing algorithms
  or backends, then update the README tables). Never edit numbers by hand.
- Redis tests use `REDIS_URL` (default `redis://localhost:6379/15`) and are marked `redis`.
  They skip when Redis is down, except with `REQUIRE_REDIS=1` (CI), where they fail.
- Storage keys look like `rl:{<caller>}:<name>:<algorithm>`; the braces are a Redis Cluster
  hash tag.
- Tests: `tests/algorithms/` use the `make_limiter` fixture, which is parametrized over
  backends, so every behaviour test runs against every backend. Shared constants live in
  `tests/helpers.py` (`T0` is aligned to every test period).
- `tests/property/` drives algorithms through `tests.helpers.Simulator` with hypothesis. Times
  are multiples of 1/64 s and periods powers of two so float math is exact; keep it that way
  or invariants will need tolerances.
