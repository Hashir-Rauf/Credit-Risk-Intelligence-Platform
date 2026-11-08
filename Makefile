.PHONY: help setup lint format test repro up down clean

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

repro:  ## Run the DVC pipeline
	dvc repro

up:  ## Start the local stack
	docker compose up --build -d

down:  ## Stop the local stack
	docker compose down

clean:  ## Remove caches
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
