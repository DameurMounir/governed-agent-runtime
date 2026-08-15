.PHONY: install lint format-check type test security audit build verify

install:
	python -m pip install -r requirements-ci.txt -e .

lint:
	python -m ruff check .

format-check:
	python -m ruff format --check .

type:
	python -m mypy

test:
	python -m pytest --cov=governed_agent_runtime --cov-branch --cov-fail-under=95

security:
	python -m bandit -q -r src

audit:
	python -m pip_audit -r requirements-ci.txt

build:
	python -m build

verify: lint format-check type test security audit build
	python scripts/check_repository.py
