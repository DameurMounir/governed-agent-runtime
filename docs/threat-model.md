# Threat Model

## Protected assets

- authority grants and their consumption state;
- workflow definitions and capability scope;
- evidence chain integrity;
- ordering and stop behavior;
- agent registration identity;
- terminal decision semantics.

## Primary threats and controls

| Threat | Current control | Remaining limitation |
| --- | --- | --- |
| Unauthorized capability use | Grant and descriptor capability checks | Grant authenticity is not cryptographically verified |
| Authority replay | In-process single-use registry | Not durable or distributed |
| Missing approval/evidence | Required evidence-kind policy | Evidence provenance depends on producer integration |
| Agent substitution | Explicit registry and stable IDs | Registry configuration is process-local |
| Execution after refusal | Immediate stop on non-PASS | Parallel scheduling is intentionally absent |
| Evidence tampering | Canonical SHA-256 chain verification | No external anchoring or signature |
| Exception detail leakage | Record exception type, not message | Agent-generated summaries still require data handling policy |
| Supply-chain compromise | Exact CI toolchain, audited dependencies, secret scan, and full commit-SHA action pins | Pinned action revisions still require provenance review before updates |

## Security invariants

1. A handler is not called when workflow authority is invalid.
2. A handler is not called when the registry cannot resolve its agent ID.
3. A step is not called when required capability, effect permission, or evidence is absent.
4. No later step is called after BLOCKED or FAIL.
5. A consumed single-use grant is not accepted again by the same authority-use registry.
6. Every attempted governed execution after authority consumption is represented on the evidence chain.
