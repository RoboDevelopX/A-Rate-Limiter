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

Planned (filled in as milestones land):

- `types` / `clock`: result and quota types, injectable clock.
- `algorithms/`: pure per-algorithm state transitions.
- `backends/`: in-memory and Redis (Lua) storage of algorithm state.
- `fastapi/`: middleware, per-route dependency, headers.
