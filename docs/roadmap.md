# Governed implementation roadmap

## Direction

The runtime is developed from the governance core outward. Framework integrations are
adapters behind explicit contracts; they are not allowed to redefine authority, evidence, or
terminal decision semantics implicitly.

Each milestone must deliver an executable walking slice, negative tests, threat analysis, and
an exact evidence record before the next milestone is represented as complete.

## Branch sequence

| Milestone | Planned branch | Outcome | Minimum acceptance evidence |
| --- | --- | --- | --- |
| G0 | `migration/01-foundation-import-v0-1-0` | Typed in-process runtime, policy boundary, evidence ledger, CLI, and three walking slices. | Python 3.12/3.13 CI, strict typing, branch coverage, security gates, package build, and exact terminal semantics. |
| G1 | `feature/02-durable-authority-store` | Transactional authority consumption and durable evidence persistence. | Replay tests across processes, concurrency tests, rollback evidence, migrations, and failure recovery. |
| G2 | `feature/03-langgraph-checkpoint-adapter` | Governed checkpoint, resume, interruption, and recovery adapter for graph workflows. | Policy re-evaluation on resume, stale-authority rejection, checkpoint tamper tests, and recovery walking slice. |
| G3 | `feature/04-langchain-tool-policy` | Tool registration adapter with declared capability, effect class, input contract, and evidence output. | Allowed and denied tool tests, irreversible-effect controls, timeout handling, and sanitized failures. |
| G4 | `feature/05-deep-agent-supervision` | Supervision and delegation with bounded child authority and explicit escalation. | No privilege amplification, delegation depth limits, human-approval interruption, and attack tests. |
| G5 | `feature/06-observability-and-evaluation` | Correlated traces, metrics, replayable evaluation cases, and evidence export. | Trace-to-evidence correlation, deterministic replay, evaluation baselines, and privacy controls. |
| G6 | `feature/07-adversarial-recovery` | Injection resistance, tool-output distrust, partial-failure recovery, and compensation policy. | Adversarial test corpus, recovery walking slices, compensation evidence, and residual-risk review. |
| G7 | `release/0.2.0` | Versioned integration release after all accepted gates. | Compatibility report, release provenance, signed checksums where supported, and protected normal merge. |

Branch names after G0 are planned names. They are not claims that the corresponding work
already exists.

## Cross-cutting requirements

Every milestone must preserve:

- explicit authority input;
- no capability amplification;
- stop-on-non-`PASS` sequencing;
- evidence before claims;
- controlled external effects;
- deterministic contracts where practical;
- human authority for irreversible or policy-sensitive actions;
- normal reviewed history without force push or hidden baseline replacement.

## Framework integration rule

LangChain, LangGraph, Deep Agents, model providers, MCP, A2A, queues, databases, and external
tools may extend execution, but they do not become sources of authority. Every adapter must
translate external behavior into the runtime's typed capability, effect, evidence, and
terminal-decision contracts.

## Definition of done

A milestone is complete only when all of the following are true:

1. the implementation and documentation agree;
2. positive, negative, failure, and recovery tests pass;
3. the trust-boundary and threat-model changes are reviewed;
4. quality, security, dependency, and packaging gates pass;
5. an executable end-to-end walking slice proves the promised outcome;
6. the exact branch head and CI evidence are recorded;
7. unsupported production claims remain explicitly excluded.
