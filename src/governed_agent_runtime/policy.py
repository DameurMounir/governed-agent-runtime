"""Fail-closed authority and evidence policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from governed_agent_runtime.decisions import Decision, EffectClass
from governed_agent_runtime.evidence import EvidenceLedger
from governed_agent_runtime.models import AgentDescriptor, AuthorityGrant, StepSpec, WorkflowSpec


class PolicyReason(StrEnum):
    """Stable machine-readable policy reasons."""

    ALLOWED = "ALLOWED"
    AUTHORITY_EXPIRED = "AUTHORITY_EXPIRED"
    AUTHORITY_SUBJECT_MISMATCH = "AUTHORITY_SUBJECT_MISMATCH"
    AUTHORITY_WORKFLOW_MISMATCH = "AUTHORITY_WORKFLOW_MISMATCH"
    CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"
    AGENT_CAPABILITY_MISMATCH = "AGENT_CAPABILITY_MISMATCH"
    EFFECT_CAPABILITY_NOT_GRANTED = "EFFECT_CAPABILITY_NOT_GRANTED"
    REQUIRED_EVIDENCE_MISSING = "REQUIRED_EVIDENCE_MISSING"


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Policy result for one workflow or step."""

    decision: Decision
    reasons: tuple[PolicyReason, ...]
    detail: str


class RuntimePolicy:
    """Default fail-closed runtime policy."""

    def evaluate_workflow(
        self,
        *,
        workflow: WorkflowSpec,
        grant: AuthorityGrant,
        runtime_subject: str,
        now: datetime,
    ) -> PolicyEvaluation:
        """Evaluate grant identity, scope, and time before any execution."""

        reasons: list[PolicyReason] = []
        if grant.subject != runtime_subject:
            reasons.append(PolicyReason.AUTHORITY_SUBJECT_MISMATCH)
        if grant.workflow_id != workflow.workflow_id:
            reasons.append(PolicyReason.AUTHORITY_WORKFLOW_MISMATCH)
        if grant.is_expired(now):
            reasons.append(PolicyReason.AUTHORITY_EXPIRED)
        if reasons:
            return PolicyEvaluation(
                Decision.BLOCKED,
                tuple(reasons),
                "workflow authority preconditions were not satisfied",
            )
        return PolicyEvaluation(
            Decision.PASS,
            (PolicyReason.ALLOWED,),
            "workflow authority accepted",
        )

    def evaluate_step(
        self,
        *,
        step: StepSpec,
        descriptor: AgentDescriptor,
        grant: AuthorityGrant,
        evidence: EvidenceLedger,
    ) -> PolicyEvaluation:
        """Evaluate capability, effect class, and evidence prerequisites."""

        reasons: list[PolicyReason] = []
        if step.capability not in grant.capabilities:
            reasons.append(PolicyReason.CAPABILITY_NOT_GRANTED)
        if step.capability not in descriptor.capabilities:
            reasons.append(PolicyReason.AGENT_CAPABILITY_MISMATCH)

        effect_capability = self._effect_capability(step.effect)
        if effect_capability is not None and effect_capability not in grant.capabilities:
            reasons.append(PolicyReason.EFFECT_CAPABILITY_NOT_GRANTED)

        missing = tuple(
            kind for kind in step.requires_evidence_kinds if not evidence.contains_kind(kind)
        )
        if missing:
            reasons.append(PolicyReason.REQUIRED_EVIDENCE_MISSING)

        if reasons:
            detail = "step policy blocked: " + ", ".join(reason.value for reason in reasons)
            if missing:
                detail += "; missing evidence: " + ", ".join(missing)
            return PolicyEvaluation(Decision.BLOCKED, tuple(reasons), detail)
        return PolicyEvaluation(Decision.PASS, (PolicyReason.ALLOWED,), "step policy accepted")

    @staticmethod
    def _effect_capability(effect: EffectClass) -> str | None:
        if effect is EffectClass.READ_ONLY:
            return None
        if effect is EffectClass.REVERSIBLE:
            return "effect:reversible"
        return "effect:irreversible"
