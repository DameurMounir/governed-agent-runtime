# Changelog

All notable changes are documented here.

## Unreleased

### Added

- Optional, exactly pinned LangGraph checkpoint/interrupt adapter.
- Explicit `editables==0.6` build-backend helper for reproducible Hatchling editable installs.
- JSON-safe graph state and stable authorize, gate, execute, and finalize nodes.
- Context-bound human resume contracts for one exact thread, workflow, and step.
- SQLite execution journal for durable authority ownership, stored-outcome replay, and in-doubt
  blocking.
- Persistent `gar-langgraph start`, `resume`, and `inspect` commands plus deterministic walking
  slices.
- Private SQLite path validation, strict checkpoint deserialization, security research baseline,
  restart/recovery tests, deterministic builds, and installed-wheel smoke gates.

### Security

- Disable checkpoint pickle fallback and arbitrary JSON/msgpack custom-module reconstruction.
- Require strict msgpack mode in CI and document it for operators.
- Reject symbolic-link, special-file, group/world-accessible checkpoint and journal paths.
- Pin direct framework versions above the reviewed deserialization and SQLite injection advisory
  floors recorded in `docs/research/langgraph-security-baseline.md`.

## 0.1.0 - 2026-08-15

### Added

- Typed workflow, step, authority, agent, result, and evidence contracts.
- Fail-closed policy evaluation for identity, scope, expiry, capability, effect risk, and evidence.
- Sequential governed runtime with explicit PASS, BLOCKED, and FAIL semantics.
- SHA-256 evidence chain with deterministic canonical JSON.
- Built-in walking-slice agents and CLI.
- Python 3.12/3.13 CI, strict static analysis, security scans, dependency audit, and packaging.
- Professional repository governance, support, verification, roadmap, pull-request, and issue
  templates.
