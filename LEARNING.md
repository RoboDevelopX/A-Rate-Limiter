# LEARNING.md

Short notes on the backend and infrastructure concepts this project uses, written for someone
coming from ML. Each milestone adds a few.

## Milestone 1: project skeleton, tooling, CI

**Lockfiles and reproducible installs.** `pyproject.toml` says which packages you need and which
version ranges are acceptable; `uv.lock` records the exact versions that were resolved. Installing
with `uv sync --locked` on your laptop, in CI, and in Docker gives the same versions everywhere,
so "works on my machine" bugs from silent upgrades go away. It is the same idea as pinning a
`requirements.txt`, but generated and checked automatically.

**Continuous integration (CI).** CI is a machine that runs your checks on every push and pull
request, so a broken change is caught before it is merged rather than after. Here GitHub Actions
runs two jobs in parallel: lint plus type check, and tests. The test job starts a real Redis as a
*service container*: a Docker container that lives next to the job for its duration, reachable on
`localhost:6379`, then thrown away.

**Strict type checking.** `mypy --strict` refuses untyped functions and implicit `Any`. In a
library this matters more than in a notebook: callers rely on the signatures, and a type checker
catches a wrong argument or a forgotten `None` case before any test runs. Think of it as a
compile step for Python.

**Linting vs formatting.** A formatter (`ruff format`) only changes layout, so code reviews never
argue about spacing. A linter (`ruff check`) finds likely bugs and bad patterns: unused imports,
mutable default arguments, `except:` that swallows `KeyboardInterrupt`, `assert` in production
code. Both run in CI, so the main branch stays clean without anyone policing it by hand.
