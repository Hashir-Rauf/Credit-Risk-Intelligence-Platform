.PHONY: help setup lint format test security repro up down clean

PY ?= python

help:  ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

setup:  ## Install dev dependencies and git hooks
	$(PY) -m pip install -U pip
	$(PY) -m pip install -r requirements-dev.txt
	pre-commit install

lint:  ## Ruff + black check
	ruff check .
	black --check .

format:  ## Auto-format
	ruff check --fix .
	black .

test:  ## Run tests with coverage
	pytest --cov

security:  ## bandit + pip-audit (same as CI)
	bandit -r src -c pyproject.toml -ll
	pip-audit -r requirements.lock.txt --no-deps --disable-pip -s osv --ignore-vuln PYSEC-2026-2447

repro:  ## Run the DVC pipeline
	dvc repro

up:  ## Start the local stack
	docker compose up --build -d

down:  ## Stop the local stack
	docker compose down

clean:  ## Remove caches
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
