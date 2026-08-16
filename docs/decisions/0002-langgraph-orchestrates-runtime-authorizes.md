# ADR 0002: LangGraph orchestrates; the governed runtime authorizes

## Status

Accepted for the `agent/02-langgraph-governed-execution` implementation candidate.

## Context

The runtime needs persistent graph execution, human interruption, checkpoint resume, and a visible
framework integration. Moving authority into graph routing would make orchestration state an
implicit permission system and would bypass the repository's evidence-first controls.

LangGraph interrupts restart the interrupted node from its beginning. Any side effect executed
before an interrupt can therefore run again. Checkpoints also protect graph state, not an arbitrary
external side effect that completed immediately before a process crash.

Checkpoint storage is integrity-sensitive. A compromised checkpoint database can become a code
execution path when permissive deserialization or pickle fallback is enabled. Resume contracts can
also be misapplied unless they are bound to the exact interrupted context.

## Decision

LangGraph owns node sequencing, checkpoints, interruption, and resume. The governed runtime remains
responsible for:

- workflow and single-use authority checks;
- agent registration and capability checks;
- effect-class and evidence policy;
- `PASS`, `BLOCKED`, and `FAIL` terminal meanings;
- append-only evidence;
- durable authority ownership and step execution claims;
- context binding of every human resume decision.

Graph state contains JSON-safe builtins only. SQLite checkpoints use `JsonPlusSerializer` with
pickle fallback disabled and both JSON and msgpack custom-module allowlists set to `None`.
`LANGGRAPH_STRICT_MSGPACK=true` is also set by CI and documented for operators.

A step is written to a separate durable execution journal before its handler runs. Completed
outcomes are replayed without invoking the handler. A `STARTED` row without a terminal outcome is
in-doubt and blocks automatic re-execution pending human reconciliation.

Checkpoint and journal paths are constrained to private regular files beneath private directories.
Symbolic links, special files, and group/world-accessible paths are rejected before SQLite opens
them.

## Consequences

- A graph checkpoint cannot grant authority.
- A resume contract for another thread, workflow, or step is rejected.
- A resumed interrupt cannot silently add capabilities or change the step request.
- Checkpoint replay does not automatically duplicate a completed handler call.
- There remains an unavoidable crash window between an external side effect and journal completion;
  that window becomes `BLOCKED`/in-doubt rather than automatic retry.
- The SQLite implementation is evidence for a local walking slice, not a horizontally scalable or
  production authority service.
- LangChain model/tool adapters and Deep Agents supervision remain later, separately governed work.
