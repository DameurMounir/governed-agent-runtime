# Verification contract

## Purpose

Verification is part of the runtime design, not a publication afterthought. A change is not
accepted merely because it executes once. It must preserve authority semantics, terminal
decision semantics, evidence integrity, repository hygiene, and package reproducibility.

## Local gate

Create an isolated environment and run the same primary gates used by continuous integration:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-ci.txt -e .
make verify
```

The explicit commands are:

```bash
python scripts/check_repository.py
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest \
  --cov=governed_agent_runtime \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=95
python -m bandit -q -r src
python -m pip_audit -r requirements-ci.txt
python -m build
```

A tracked-file secret scan and the executable walking slices are also mandatory in CI.

## Gate matrix

| Gate | Failure meaning | Required response |
| --- | --- | --- |
| Repository hygiene | Required governance, contract, or source material is missing or malformed. | Correct the repository; do not waive the gate silently. |
| Ruff lint and format | Source does not satisfy the agreed static style contract. | Correct the source or add a narrow, justified suppression. |
| Strict mypy | A typed boundary is inconsistent or insufficiently explicit. | Correct the type contract before merge. |
| pytest | A behavioral invariant is broken or unproved. | Add or repair positive and negative tests. |
| Branch coverage | Important control paths lack executable evidence. | Add tests; do not exclude governed logic merely to raise coverage. |
| Bandit | Source contains a security-relevant pattern. | Remove it or document a precise, reviewed false positive. |
| detect-secrets | A tracked file resembles a credential or secret. | Remove and rotate real secrets; review false positives explicitly. |
| pip-audit | A pinned verification dependency has a known vulnerability. | Update the pin and re-run the complete suite. |
| Package build | The distributable source or wheel is invalid. | Correct metadata or package boundaries. |
| Walking slices | Terminal semantics or exit codes drifted. | Restore exact `PASS`, `BLOCKED`, and `FAIL` behavior. |

## Behavioral evidence

Every policy or runtime change requires evidence for both allowed and denied behavior. At a
minimum, reviewers should see tests for:

- matching and mismatching runtime subjects;
- matching and mismatching workflow scope;
- valid and expired authority;
- granted and missing capabilities;
- permitted and unpermitted effect classes;
- present and absent prerequisite evidence;
- registered and unknown agents;
- single-use authority acceptance and replay blocking;
- handler `PASS`, agent-level `BLOCKED`, and exception-driven `FAIL`;
- evidence-chain verification and mutation detection;
- no execution of later steps after any non-`PASS` result.

## Continuous-integration evidence

GitHub Actions runs the full quality job independently on Python 3.12 and Python 3.13. The
pull request head is acceptable only when every matrix job completes successfully against the
exact reviewed commit.

The workflow uses read-only repository contents permission, disables persisted checkout
credentials, installs the pinned verification requirements, and builds both source and wheel
artifacts.

## Publication evidence

A governed publication should retain:

1. exact repository, branch, commit, and tree identifiers;
2. the toolchain versions used for verification;
3. test and branch-coverage output;
4. static-analysis, security, dependency-audit, and package-build logs;
5. walking-slice results and exit codes;
6. a source manifest or bundle checksum;
7. the exact-head continuous-integration run;
8. an explicit statement of actions not performed, such as merge or deployment.

A safe stop is evidence of governance. It must not be rewritten as a success.
