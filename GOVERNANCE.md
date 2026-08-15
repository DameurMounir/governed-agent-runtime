# Governance

## Maintainer authority

Repository maintainers control source governance. Runtime authority remains an explicit input contract and is never inferred from GitHub ownership, code authorship, or agent identity.

## Change classes

| Class | Examples | Minimum evidence |
| --- | --- | --- |
| Documentation | Clarification without behavioral change | Review and link validation |
| Contract | Workflow, authority, result, or evidence shape | Compatibility analysis and tests |
| Policy | Capability, effect, expiry, or evidence decision | Positive and negative policy tests |
| Runtime | Sequencing, execution, stop, or replay behavior | Walking slices, branch coverage, threat analysis |
| Supply chain | Dependencies, actions, packaging | Dependency audit and reproducible CI |

## Protected direction

The project follows an evidence-first, fail-closed direction:

- no implicit authority;
- no execution after a non-PASS result;
- no silent degradation from policy checks;
- no claim of production readiness without durable state and external integration evidence;
- no force push or history rewrite for governed releases.
