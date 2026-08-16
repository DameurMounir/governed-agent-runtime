.PHONY: install versions hygiene lint format-check type test security secrets audit build walking-slices verify

install:
	python -m pip install -r requirements-ci.txt -e '.[langgraph]'

versions:
	python -c 'from importlib.metadata import version; expected={"langgraph":"1.2.10","langgraph-checkpoint":"4.2.0","langgraph-checkpoint-sqlite":"3.1.1"}; actual={name:version(name) for name in expected}; assert actual == expected, actual; print(actual)'

hygiene:
	python scripts/check_repository.py

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

secrets:
	detect-secrets scan --all-files --exclude-files '(^|/)(\.git|\.mypy_cache|\.pytest_cache|\.ruff_cache|__pycache__|build|dist|dist-a|dist-b)(/|$$)' > /tmp/gar-secrets.json
	python -c 'import json; from pathlib import Path; data=json.loads(Path("/tmp/gar-secrets.json").read_text()); results=data.get("results", {}); assert not results, sorted(results); print("Secret scan PASS")'

audit:
	python -m pip_audit -r requirements-ci.txt

build:
	python -m build

walking-slices:
	gar demo pass
	gar-langgraph demo pass
	gar-langgraph demo resume

verify: versions hygiene lint format-check type test security secrets audit build walking-slices
