"""Immutable contracts exchanged by policies, agents, and the runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from governed_agent_runtime.decisions import Decision, EffectClass


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _frozen_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """Agent-supplied evidence that the runtime will place on the hash chain."""

    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _required(self.kind, "kind"))
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Static identity and capability envelope for a registered agent."""

    agent_id: str
    version: str
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _required(self.agent_id, "agent_id"))
        object.__setattr__(self, "version", _required(self.version, "version"))
        normalized = frozenset(_required(item, "capability") for item in self.capabilities)
        if not normalized:
            raise ValueError("capabilities must not be empty")
        object.__setattr__(self, "capabilities", normalized)


@dataclass(frozen=True, slots=True)
class StepSpec:
    """One policy-governed unit of work."""

    step_id: str
    agent_id: str
    action: str
    capability: str
    effect: EffectClass = EffectClass.READ_ONLY
    requires_evidence_kinds: tuple[str, ...] = ()
    input_data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _required(self.step_id, "step_id"))
        object.__setattr__(self, "agent_id", _required(self.agent_id, "agent_id"))
        object.__setattr__(self, "action", _required(self.action, "action"))
        object.__setattr__(self, "capability", _required(self.capability, "capability"))
        required_kinds = tuple(
            _required(item, "requires_evidence_kind") for item in self.requires_evidence_kinds
        )
        if len(set(required_kinds)) != len(required_kinds):
            raise ValueError("requires_evidence_kinds must be unique")
        object.__setattr__(self, "requires_evidence_kinds", required_kinds)
        object.__setattr__(self, "input_data", _frozen_mapping(self.input_data))


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    """Ordered workflow evaluated and executed as one governed transaction."""

    workflow_id: str
    correlation_id: str
    steps: tuple[StepSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _required(self.workflow_id, "workflow_id"))
        object.__setattr__(self, "correlation_id", _required(self.correlation_id, "correlation_id"))
        if not self.steps:
            raise ValueError("workflow must contain at least one step")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("workflow step_id values must be unique")


@dataclass(frozen=True, slots=True)
class AuthorityGrant:
    """Time-bound, workflow-bound capability grant."""

    authorization_id: str
    subject: str
    workflow_id: str
    capabilities: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    single_use: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authorization_id",
            _required(self.authorization_id, "authorization_id"),
        )
        object.__setattr__(self, "subject", _required(self.subject, "subject"))
        object.__setattr__(self, "workflow_id", _required(self.workflow_id, "workflow_id"))
        normalized = frozenset(_required(item, "capability") for item in self.capabilities)
        if not normalized:
            raise ValueError("capabilities must not be empty")
        object.__setattr__(self, "capabilities", normalized)
        for field_name, value in (("issued_at", self.issued_at), ("expires_at", self.expires_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")

    def is_expired(self, now: datetime) -> bool:
        """Return true when the grant is no longer valid."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return now >= self.expires_at


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """Read-only request delivered to an agent handler."""

    workflow_id: str
    correlation_id: str
    step_id: str
    action: str
    input_data: Mapping[str, object]
    evidence_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_data", _frozen_mapping(self.input_data))


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    """Result returned by a registered agent."""

    decision: Decision
    summary: str
    output: Mapping[str, object] = field(default_factory=dict)
    evidence: tuple[EvidenceItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _required(self.summary, "summary"))
        object.__setattr__(self, "output", _frozen_mapping(self.output))


@dataclass(frozen=True, slots=True)
class StepResult:
    """Auditable result of one attempted workflow step."""

    step_id: str
    decision: Decision
    summary: str
    evidence_ids: tuple[str, ...]
    output: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _frozen_mapping(self.output))


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Terminal workflow result."""

    workflow_id: str
    correlation_id: str
    decision: Decision
    summary: str
    steps: tuple[StepResult, ...]
    evidence_head: str | None
    authority_consumed: bool
