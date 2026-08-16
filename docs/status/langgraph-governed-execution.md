# LangGraph governed execution status

**Branch:** `agent/02-langgraph-governed-execution`
**State:** implementation candidate
**Production claim:** none

## Implemented

- optional, pinned LangGraph integration without making the dependency-free core import it;
- typed, JSON-safe `GovernedGraphState`;
- stable `authorize`, `gate`, `execute`, and `finalize` nodes;
- SQLite checkpoints keyed by explicit `thread_id`;
- strict checkpoint serializer configuration with pickle fallback disabled;
- human evidence interruption and `Command(resume=...)` continuation;
- resume contracts bound to the active thread, workflow, and step;
- durable single-use authority ownership by workflow thread;
- durable step claims, completed-outcome replay, and in-doubt blocking;
- private-path and symbolic-link checks for SQLite state files;
- `PASS`, interrupted `BLOCKED`, denied `BLOCKED`, resumed `PASS`, handler `BLOCKED`, and
  `FAIL` semantics;
- persistent `start`, `resume`, and `inspect` CLI commands;
- deterministic walking slices and negative/restart tests.

## Required merge evidence

- exact-head Python 3.12 and 3.13 CI;
- Ruff lint/format and strict mypy;
- at least 95% branch coverage;
- Bandit, detect-secrets, and dependency audit;
- deterministic wheel and source builds;
- installed-wheel execution of `PASS`, `BLOCKED`, resumed `PASS`, and `FAIL` scenarios;
- persistent start/restart/resume/inspect evidence;
- no regression in the original dependency-free `gar` CLI and its 69 foundation tests.

## Explicit non-claims

This branch does not provide a distributed authority store, production database, queue, HTTP API,
model provider, LangChain tool registry, Deep Agents delegation, compensation transaction, or
production deployment. SQLite is used for a bounded local adapter proof. Irreversible external
effects still require a later transactional/outbox or reconciliation design.
