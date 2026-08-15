# ADR 0001: Evidence-bound sequential runtime

- Status: Accepted
- Date: 2026-08-15

## Context

Agent orchestration examples commonly optimize for routing and tool invocation while leaving authorization, evidence prerequisites, and safe refusal ambiguous. The first repository milestone needs to demonstrate governance semantics before adding distributed infrastructure.

## Decision

Implement a dependency-free, in-process, sequential runtime with:

- explicit workflow-bound authority grants;
- registered agent descriptors and capabilities;
- per-step evidence and effect checks;
- PASS, BLOCKED, and FAIL as separate terminal semantics;
- immediate stop on any non-PASS outcome;
- deterministic SHA-256 evidence chaining;
- injectable policy, clock, and authority-use state.

## Consequences

The design is easy to audit and test, and it prevents accidental continuation after refusal. It intentionally postpones parallel scheduling, durable replay protection, remote protocols, signatures, and persistent evidence storage. Future infrastructure must preserve the current semantic invariants.
