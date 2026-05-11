# ReviewGate — common local and container workflows.
# Prefer an activated venv; targets use `python3` / `pip` from PATH.

.DEFAULT_GOAL := help

.PHONY: help venv install install-dev install-core-dev clean test test-verbose \
	lint fmt format build docker-build docker-run-api docker-run-worker \
	docker-shell alembic-upgrade alembic-downgrade-base check lock-uv sync-uv

PY ?= python3
PIP ?= $(PY) -m pip
IMAGE ?= reviewgate:local
DOCKER ?= docker
VENV ?= .venv

help: ## Show documented targets (default).
	@echo "ReviewGate Makefile"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?##' "$(CURDIR)/Makefile" \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

venv: ## Create $(VENV) with $(PY) (activate: source $(VENV)/bin/activate).
	$(PY) -m venv "$(VENV)"
	@echo "Activate: source $(VENV)/bin/activate"

install: ## Editable install with hosted app extras (API, worker, DB stack).
	$(PIP) install -U pip
	$(PIP) install -e ".[app]"

install-dev: ## Editable install matching CI (tests + app extras).
	$(PIP) install -U pip
	$(PIP) install -e ".[dev,app]"

install-core-dev: ## Engine + pytest only (no FastAPI/Postgres extras).
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

clean: ## Remove local build artifacts and caches (not $(VENV)).
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov \
		.coverage .coverage.* *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

test: ## Run the full pytest suite (requires install-dev).
	$(PY) -m pytest -q

test-verbose: ## Run pytest with high verbosity.
	$(PY) -m pytest -vv

lint: ## Ruff check on src/ and tests/ (installed by install-dev).
	@$(PY) -m ruff --version >/dev/null 2>&1 || ( \
		echo "ruff is not installed. Install with: pip install ruff"; exit 1)
	$(PY) -m ruff check src tests

fmt: ## Alias for `make format`.
	@$(MAKE) format

format: ## Ruff format src/ and tests/ (installed by install-dev).
	@$(PY) -m ruff --version >/dev/null 2>&1 || ( \
		echo "ruff is not installed. Install with: pip install ruff"; exit 1)
	$(PY) -m ruff format src tests

build: ## Build sdist and wheel into dist/ (installs build tool if needed).
	$(PY) -m pip install -q build
	$(PY) -m build

docker-build: ## Build $(IMAGE) from the Dockerfile.
	$(DOCKER) build -t "$(IMAGE)" .

docker-run-api: ## Run API on port 8000 (export REVIEWGATE_* as needed).
	$(DOCKER) run --rm -p 8000:8000 \
		-e REVIEWGATE_DATABASE_URL \
		-e REVIEWGATE_REDIS_URL \
		-e REVIEWGATE_HTTP_PORT=8000 \
		"$(IMAGE)"

docker-run-worker: ## Run Dramatiq worker (needs REVIEWGATE_REDIS_URL).
	$(DOCKER) run --rm \
		-e REVIEWGATE_DATABASE_URL \
		-e REVIEWGATE_REDIS_URL \
		"$(IMAGE)" reviewgate-worker

docker-shell: ## Interactive shell in the image (debugging).
	$(DOCKER) run --rm -it --entrypoint /bin/bash "$(IMAGE)"

alembic-upgrade: ## alembic upgrade head (requires REVIEWGATE_DATABASE_URL).
	@test -n "$$REVIEWGATE_DATABASE_URL" || ( \
		echo "Set REVIEWGATE_DATABASE_URL (same DSN as runtime)."; exit 1)
	$(PY) -m alembic upgrade head

alembic-downgrade-base: ## Roll back all migrations (destructive; dev only).
	@test -n "$$REVIEWGATE_DATABASE_URL" || ( \
		echo "Set REVIEWGATE_DATABASE_URL."; exit 1)
	$(PY) -m alembic downgrade base

check: ## Run tests and Ruff.
	@$(MAKE) test
	@$(MAKE) lint

lock-uv: ## Regenerate uv.lock (gitignored; for optional uv workflows).
	@command -v uv >/dev/null 2>&1 || (echo "Install uv: https://docs.astral.sh/uv/"; exit 1)
	uv lock

sync-uv: ## uv sync with dev+app extras (run lock-uv first if no lockfile).
	@command -v uv >/dev/null 2>&1 || (echo "Install uv: https://docs.astral.sh/uv/"; exit 1)
	uv sync --extra dev --extra app
