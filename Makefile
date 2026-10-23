.PHONY: install lint format typecheck test test-unit redis check

install:  ## Install the package and dev tools from the lockfile
	uv sync --locked

lint:  ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck:  ## mypy --strict on the library
	uv run mypy

test:  ## Run the test suite with coverage (Redis tests skip if REDIS_URL is unreachable)
	uv run pytest --cov --cov-report=term-missing

test-unit:  ## Run only the tests that need no Redis
	uv run pytest -m "not redis"

redis:  ## Start a throwaway Redis 7 on localhost:6379
	docker run --rm -d --name mlratelimit-redis -p 6379:6379 redis:7-alpine

check: lint typecheck test  ## Everything CI runs
