.PHONY: install lint format typecheck test check

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

test:  ## Run the test suite with coverage
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck test  ## Everything CI runs
