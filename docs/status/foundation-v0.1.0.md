# Foundation v0.1.0 status

## Implemented

- Typed contracts for workflows, steps, grants, agents, evidence, and results.
- Workflow and step policy enforcement.
- Single-process authority replay protection.
- Sequential stop-on-non-PASS orchestration.
- Deterministic evidence hashing and chain verification.
- Executable PASS, BLOCKED, and FAIL walking slices.
- Static analysis, tests, branch coverage, security scans, dependency audit, and package build in CI.

## Explicitly not implemented

- Durable or distributed authority state.
- Database-backed evidence.
- Cryptographic grant or evidence signatures.
- Remote A2A, MCP, queue, scheduler, or model-provider integration.
- Deployment, observability export, customer tenancy, or production SLOs.

These exclusions prevent the repository from overstating readiness.
