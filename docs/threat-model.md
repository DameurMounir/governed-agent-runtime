# Threat Model

## Protected assets

- authority grants and their consumption state;
- workflow definitions and capability scope;
- evidence chain integrity;
- ordering and stop behavior;
- agent registration identity;
- terminal decision semantics;
- LangGraph checkpoint state and thread identity;
- human resume decisions and supplied evidence;
- durable step execution claims and sealed outcomes.

## Primary threats and controls

| Threat | Current control | Remaining limitation |
| --- | --- | --- |
| Unauthorized capability use | Grant and descriptor capability checks | Grant authenticity is not cryptographically verified |
| Authority replay | In-process core registry; SQLite thread ownership in the LangGraph candidate | SQLite journal is not distributed and has no cross-host consensus |
| Missing approval/evidence | Required evidence-kind policy plus persistent interrupt | Evidence provenance depends on producer integration |
| Resume decision applied to the wrong work | Resume contract binds exact thread, workflow, and step | Human identity/signature verification is not implemented |
| Agent substitution | Explicit registry and stable IDs | Registry configuration is process-local |
| Execution after refusal | Immediate stop on non-`PASS` | Parallel scheduling is intentionally absent |
| Duplicate external effect after restart | Durable pre-execution claim and stored-outcome replay | Crash after provider side effect but before journal sealing becomes in-doubt |
| Blind retry of uncertain work | `STARTED` without terminal outcome becomes `BLOCKED` | Human reconciliation and compensation remain later work |
| Evidence tampering | Canonical SHA-256 chain verification | No external anchoring or signature |
| Checkpoint deserialization attack | Patched package pins, strict msgpack mode, no pickle fallback, no custom-module allowlists, builtins-only state | Database integrity and package supply chain remain operational responsibilities |
| Checkpoint-file replacement or shared read/write access | Private parent/file permissions; symbolic-link and special-file rejection | No filesystem encryption or external integrity monitor |
| SQLite metadata-filter injection | Patched SQLite checkpointer; adapter exposes no untrusted list/filter API | Future query APIs require separate review |
| Exception detail leakage | Record exception type, not message | Agent-generated summaries still require data handling policy |
| Supply-chain compromise | Exact direct framework pins, exact CI toolchain, audited dependencies, secret scan, and full commit-SHA action pins | Transitive dependency resolution is not yet a cryptographically hashed cross-platform lockfile |

## Security invariants

1. A handler is not called when workflow authority is invalid.
2. A handler is not called when the registry cannot resolve its agent ID.
3. A step is not called when required capability, effect permission, or evidence is absent.
4. No later step is called after `BLOCKED` or `FAIL`.
5. A consumed single-use grant is not accepted by a different LangGraph thread.
6. A resume decision for another thread, workflow, or step is rejected.
7. A completed step outcome is replayed without invoking the handler again.
8. An unfinished step claim is treated as in-doubt and is not retried automatically.
9. Every attempted governed execution after authority consumption is represented on the evidence chain.
10. Checkpoint deserialization does not enable pickle fallback or arbitrary custom-module loading.
11. Checkpoint and journal databases are private regular files beneath private directories.
