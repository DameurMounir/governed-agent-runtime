from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from governed_agent_runtime.decisions import Decision, EffectClass
from governed_agent_runtime.evidence import EvidenceLedger
from governed_agent_runtime.models import AgentDescriptor, AuthorityGrant, StepSpec, WorkflowSpec
from governed_agent_runtime.policy import PolicyReason, RuntimePolicy


def _workflow() -> WorkflowSpec:
    return WorkflowSpec("workflow-1", "corr", (StepSpec("s", "echo", "run", "agent:echo"),))


def test_workflow_policy_accepts_valid_grant(instant: datetime, grant: AuthorityGrant) -> None:
    result = RuntimePolicy().evaluate_workflow(
        workflow=_workflow(), grant=grant, runtime_subject="runtime", now=instant
    )
    assert result.decision is Decision.PASS
    assert result.reasons == (PolicyReason.ALLOWED,)


def test_workflow_policy_collects_scope_reasons(instant: datetime, grant: AuthorityGrant) -> None:
    invalid = replace(
        grant,
        subject="other",
        workflow_id="other-workflow",
        expires_at=instant - timedelta(seconds=1),
        issued_at=instant - timedelta(minutes=2),
    )
    result = RuntimePolicy().evaluate_workflow(
        workflow=_workflow(), grant=invalid, runtime_subject="runtime", now=instant
    )
    assert result.decision is Decision.BLOCKED
    assert set(result.reasons) == {
        PolicyReason.AUTHORITY_SUBJECT_MISMATCH,
        PolicyReason.AUTHORITY_WORKFLOW_MISMATCH,
        PolicyReason.AUTHORITY_EXPIRED,
    }


def test_step_policy_requires_grant_and_agent_capabilities(grant: AuthorityGrant) -> None:
    step = StepSpec("s", "echo", "run", "agent:other")
    descriptor = AgentDescriptor("echo", "1", frozenset({"agent:echo"}))
    result = RuntimePolicy().evaluate_step(
        step=step, descriptor=descriptor, grant=grant, evidence=EvidenceLedger()
    )
    assert result.decision is Decision.BLOCKED
    assert PolicyReason.CAPABILITY_NOT_GRANTED in result.reasons
    assert PolicyReason.AGENT_CAPABILITY_MISMATCH in result.reasons


def test_step_policy_requires_effect_capability(grant: AuthorityGrant) -> None:
    step = StepSpec("s", "echo", "run", "agent:echo", effect=EffectClass.IRREVERSIBLE)
    descriptor = AgentDescriptor("echo", "1", frozenset({"agent:echo"}))
    result = RuntimePolicy().evaluate_step(
        step=step, descriptor=descriptor, grant=grant, evidence=EvidenceLedger()
    )
    assert PolicyReason.EFFECT_CAPABILITY_NOT_GRANTED in result.reasons


def test_step_policy_requires_evidence(grant: AuthorityGrant, instant: datetime) -> None:
    step = StepSpec(
        "s",
        "echo",
        "run",
        "agent:echo",
        requires_evidence_kinds=("approval.recorded",),
    )
    descriptor = AgentDescriptor("echo", "1", frozenset({"agent:echo"}))
    ledger = EvidenceLedger()
    blocked = RuntimePolicy().evaluate_step(
        step=step, descriptor=descriptor, grant=grant, evidence=ledger
    )
    assert PolicyReason.REQUIRED_EVIDENCE_MISSING in blocked.reasons
    ledger.append(producer="approver", kind="approval.recorded", payload={}, created_at=instant)
    passed = RuntimePolicy().evaluate_step(
        step=step, descriptor=descriptor, grant=grant, evidence=ledger
    )
    assert passed.decision is Decision.PASS


def test_reversible_effect_is_allowed_when_granted(grant: AuthorityGrant) -> None:
    step = StepSpec("s", "echo", "run", "agent:echo", effect=EffectClass.REVERSIBLE)
    descriptor = AgentDescriptor("echo", "1", frozenset({"agent:echo"}))
    result = RuntimePolicy().evaluate_step(
        step=step, descriptor=descriptor, grant=grant, evidence=EvidenceLedger()
    )
    assert result.decision is Decision.PASS
