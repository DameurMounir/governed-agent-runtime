# Contributing

Changes should be small, reviewable, and evidence-backed.

## Local verification

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-ci.txt -e .
python scripts/check_repository.py
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest --cov=governed_agent_runtime --cov-branch --cov-fail-under=95
python -m bandit -q -r src
python -m pip_audit -r requirements-ci.txt
python -m build
```

## Pull requests

A pull request should state:

1. the governed behavior being changed;
2. the authority or evidence implication;
3. the validation performed;
4. the rollback path;
5. any capability, effect, or trust boundary introduced.

Do not rewrite shared history. Use normal reviewed merges and normal revert pull requests.
