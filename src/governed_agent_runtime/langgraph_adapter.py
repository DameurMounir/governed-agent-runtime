"""LangGraph adapter that keeps the governed runtime as the authority boundary.

LangGraph owns orchestration, checkpointing, interruption, and resume. This
adapter owns authority checks, capability/effect/evidence policy, a durable
execution journal, deterministic evidence, and terminal PASS/BLOCKED/FAIL
semantics. The graph state contains JSON-safe builtins only.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from governed_agent_runtime.clock import Clock, SystemClock
from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.evidence import EvidenceLedger, canonical_json, to_json_value
from governed_agent_runtime.execution_journal import (
    JournalError,
    SqliteExecutionJournal,
    StepClaimState,
    ensure_private_sqlite_path,
)
from governed_agent_runtime.models import (
    AgentOutcome,
    AgentRequest,
    AuthorityGrant,
    EvidenceItem,
    StepResult,
    StepSpec,
    WorkflowResult,
    WorkflowSpec,
)
from governed_agent_runtime.policy import PolicyReason, RuntimePolicy
from governed_agent_runtime.registry import AgentRegistry
from governed_agent_runtime.serialization import authority_from_mapping, workflow_from_mapping

GraphStatus = Literal["INTERRUPTED", "TERMINAL", "RUNNING"]


class GovernedGraphState(TypedDict):
    """Checkpoint-safe state shared by the governed LangGraph nodes."""

    schema_version: str
    thread_id: str
    workflow: dict[str, object]
    authority: dict[str, object]
    current_step: int
    decision: str | None
    summary: str
    terminal: bool
    authority_consumed: bool
    evidence: list[dict[str, object]]
    step_results: list[dict[str, object]]


class GovernedGraphUpdate(TypedDict, total=False):
    current_step: int
    decision: str | None
    summary: str
    terminal: bool
    authority_consumed: bool
    evidence: list[dict[str, object]]
    step_results: list[dict[str, object]]


class LangGraphExecutionError(RuntimeError):
    """Raised for malformed state, unsafe resume payloads, or thread misuse."""


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    """Human decision bound to one exact interrupted workflow step."""

    thread_id: str
    workflow_id: str
    step_id: str
    approved: bool
    evidence: tuple[EvidenceItem, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("thread_id", self.thread_id),
            ("workflow_id", self.workflow_id),
            ("step_id", self.step_id),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        kinds = tuple(item.kind for item in self.evidence)
        if len(set(kinds)) != len(kinds):
            raise ValueError("resume evidence kinds must be unique")


@dataclass(frozen=True, slots=True)
class GovernedGraphRun:
    """Public, JSON-serializable view of one graph invocation."""

    thread_id: str
    status: GraphStatus
    decision: Decision | None
    summary: str
    current_step: int
    authority_consumed: bool
    evidence_head: str | None
    steps: tuple[StepResult, ...]
    interrupts: tuple[Mapping[str, object], ...]

    def as_mapping(self) -> dict[str, object]:
        """Return a stable JSON-compatible representation."""

        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "decision": None if self.decision is None else self.decision.value,
            "summary": self.summary,
            "current_step": self.current_step,
            "authority_consumed": self.authority_consumed,
            "evidence_head": self.evidence_head,
            "steps": [_step_result_to_mapping(item) for item in self.steps],
            "interrupts": [dict(item) for item in self.interrupts],
        }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise LangGraphExecutionError(f"{field_name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LangGraphExecutionError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LangGraphExecutionError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _workflow_to_mapping(workflow: WorkflowSpec) -> dict[str, object]:
    return {
        "workflow_id": workflow.workflow_id,
        "correlation_id": workflow.correlation_id,
        "steps": [
            {
                "step_id": step.step_id,
                "agent_id": step.agent_id,
                "action": step.action,
                "capability": step.capability,
                "effect": step.effect.value,
                "requires_evidence_kinds": list(step.requires_evidence_kinds),
                "input_data": to_json_value(step.input_data),
            }
            for step in workflow.steps
        ],
    }


def _authority_to_mapping(grant: AuthorityGrant) -> dict[str, object]:
    return {
        "authorization_id": grant.authorization_id,
        "subject": grant.subject,
        "workflow_id": grant.workflow_id,
        "capabilities": sorted(grant.capabilities),
        "issued_at": _utc_text(grant.issued_at),
        "expires_at": _utc_text(grant.expires_at),
        "single_use": grant.single_use,
    }


def _step_to_mapping(step: StepSpec) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "agent_id": step.agent_id,
        "action": step.action,
        "capability": step.capability,
        "effect": step.effect.value,
        "requires_evidence_kinds": list(step.requires_evidence_kinds),
        "input_data": to_json_value(step.input_data),
    }


def _evidence_item_to_mapping(item: EvidenceItem) -> dict[str, object]:
    return {"kind": item.kind, "payload": to_json_value(item.payload)}


def _outcome_to_mapping(outcome: AgentOutcome) -> dict[str, object]:
    return {
        "decision": outcome.decision.value,
        "summary": outcome.summary,
        "output": to_json_value(outcome.output),
        "evidence": [_evidence_item_to_mapping(item) for item in outcome.evidence],
    }


def _mapping_object(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise LangGraphExecutionError(f"{field_name} must be a JSON object")
    return cast(Mapping[str, object], value)


def _outcome_from_mapping(value: Mapping[str, object]) -> AgentOutcome:
    raw_decision = value.get("decision")
    raw_summary = value.get("summary")
    raw_output = value.get("output", {})
    raw_evidence = value.get("evidence", [])
    if not isinstance(raw_decision, str):
        raise LangGraphExecutionError("stored outcome decision must be a string")
    if not isinstance(raw_summary, str):
        raise LangGraphExecutionError("stored outcome summary must be a string")
    if not isinstance(raw_output, dict):
        raise LangGraphExecutionError("stored outcome output must be an object")
    if not isinstance(raw_evidence, list):
        raise LangGraphExecutionError("stored outcome evidence must be an array")
    try:
        decision = Decision(raw_decision)
    except ValueError as exc:
        raise LangGraphExecutionError("stored outcome decision is unsupported") from exc
    evidence: list[EvidenceItem] = []
    for index, raw_item in enumerate(raw_evidence):
        item = _mapping_object(raw_item, f"stored outcome evidence[{index}]")
        kind = item.get("kind")
        payload = item.get("payload", {})
        if not isinstance(kind, str) or not kind:
            raise LangGraphExecutionError("stored outcome evidence kind must not be empty")
        if not isinstance(payload, dict):
            raise LangGraphExecutionError("stored outcome evidence payload must be an object")
        evidence.append(EvidenceItem(kind, cast(Mapping[str, object], payload)))
    return AgentOutcome(
        decision,
        raw_summary,
        output=cast(Mapping[str, object], raw_output),
        evidence=tuple(evidence),
    )


def _step_result_to_mapping(result: StepResult) -> dict[str, object]:
    return {
        "step_id": result.step_id,
        "decision": result.decision.value,
        "summary": result.summary,
        "evidence_ids": list(result.evidence_ids),
        "output": to_json_value(result.output),
    }


def _step_result_from_mapping(value: Mapping[str, object]) -> StepResult:
    step_id = value.get("step_id")
    decision_raw = value.get("decision")
    summary = value.get("summary")
    evidence_ids_raw = value.get("evidence_ids", [])
    output = value.get("output", {})
    if not isinstance(step_id, str) or not step_id:
        raise LangGraphExecutionError("step result step_id must not be empty")
    if not isinstance(decision_raw, str):
        raise LangGraphExecutionError("step result decision must be a string")
    if not isinstance(summary, str):
        raise LangGraphExecutionError("step result summary must be a string")
    if not isinstance(evidence_ids_raw, list) or not all(
        isinstance(item, str) for item in evidence_ids_raw
    ):
        raise LangGraphExecutionError("step result evidence_ids must be strings")
    if not isinstance(output, dict):
        raise LangGraphExecutionError("step result output must be an object")
    try:
        decision = Decision(decision_raw)
    except ValueError as exc:
        raise LangGraphExecutionError("step result decision is unsupported") from exc
    return StepResult(
        step_id=step_id,
        decision=decision,
        summary=summary,
        evidence_ids=tuple(cast(list[str], evidence_ids_raw)),
        output=cast(Mapping[str, object], output),
    )


def _ledger_from_export(records: Sequence[Mapping[str, object]]) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for index, raw in enumerate(records, start=1):
        sequence = raw.get("sequence")
        evidence_id = raw.get("evidence_id")
        producer = raw.get("producer")
        kind = raw.get("kind")
        payload = raw.get("payload")
        created_at = _parse_utc(raw.get("created_at"), f"evidence[{index}].created_at")
        if sequence != index or evidence_id != f"ev-{index:06d}":
            raise LangGraphExecutionError("checkpoint evidence sequence is not contiguous")
        if not isinstance(producer, str) or not producer:
            raise LangGraphExecutionError("checkpoint evidence producer must not be empty")
        if not isinstance(kind, str) or not kind:
            raise LangGraphExecutionError("checkpoint evidence kind must not be empty")
        if not isinstance(payload, dict):
            raise LangGraphExecutionError("checkpoint evidence payload must be an object")
        rebuilt = ledger.append(
            producer=producer,
            kind=kind,
            payload=cast(Mapping[str, object], payload),
            created_at=created_at,
        )
        if ledger.export()[-1] != dict(raw) or rebuilt.digest != raw.get("digest"):
            raise LangGraphExecutionError("checkpoint evidence chain verification failed")
    ledger.verify()
    return ledger


def _state_records(state: GovernedGraphState) -> Sequence[Mapping[str, object]]:
    return state["evidence"]


def _state_ledger(state: GovernedGraphState) -> EvidenceLedger:
    return _ledger_from_export(_state_records(state))


def _state_workflow(state: GovernedGraphState) -> WorkflowSpec:
    return workflow_from_mapping(state["workflow"])


def _state_authority(state: GovernedGraphState) -> AuthorityGrant:
    return authority_from_mapping(state["authority"])


def _snapshot_interrupts(snapshot: Any) -> tuple[object, ...]:
    interrupts: list[object] = []
    for task in getattr(snapshot, "tasks", ()):
        raw = getattr(task, "interrupts", ())
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            interrupts.extend(raw)
    return tuple(interrupts)


def _state_step_results(state: GovernedGraphState) -> list[StepResult]:
    return [_step_result_from_mapping(item) for item in state["step_results"]]


def _request_digest(
    *,
    thread_id: str,
    workflow: WorkflowSpec,
    grant: AuthorityGrant,
    step: StepSpec,
    evidence_head: str | None,
) -> str:
    material = {
        "thread_id": thread_id,
        "workflow_id": workflow.workflow_id,
        "authorization_id": grant.authorization_id,
        "step": _step_to_mapping(step),
        "evidence_head": evidence_head,
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _terminal_blocked(
    *,
    state: GovernedGraphState,
    ledger: EvidenceLedger,
    step: StepSpec | None,
    summary: str,
    kind: str,
    created_at: datetime,
) -> GovernedGraphUpdate:
    payload: dict[str, object] = {"detail": summary}
    if step is not None:
        payload.update({"step_id": step.step_id, "agent_id": step.agent_id})
    record = ledger.append(
        producer="langgraph-adapter",
        kind=kind,
        payload=payload,
        created_at=created_at,
    )
    results = _state_step_results(state)
    if step is not None:
        results.append(
            StepResult(
                step_id=step.step_id,
                decision=Decision.BLOCKED,
                summary=summary,
                evidence_ids=(record.evidence_id,),
            )
        )
    return {
        "decision": Decision.BLOCKED.value,
        "summary": summary,
        "terminal": True,
        "evidence": ledger.export(),
        "step_results": [_step_result_to_mapping(item) for item in results],
    }


class GovernedLangGraphExecutor:
    """Synchronous LangGraph orchestration behind explicit runtime governance."""

    def __init__(
        self,
        *,
        subject: str,
        registry: AgentRegistry,
        checkpoint_path: Path,
        journal_path: Path,
        policy: RuntimePolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not subject.strip():
            raise ValueError("subject must not be empty")
        self._subject = subject.strip()
        self._registry = registry
        self._policy = policy or RuntimePolicy()
        self._clock = clock or SystemClock()
        self._checkpoint_path = ensure_private_sqlite_path(checkpoint_path)
        self._checkpoint_connection = sqlite3.connect(
            self._checkpoint_path,
            timeout=30.0,
            check_same_thread=False,
        )
        try:
            self._checkpoint_connection.execute("PRAGMA busy_timeout = 30000")
            mode = self._checkpoint_connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                raise LangGraphExecutionError("checkpoint store could not enable WAL mode")
            self._checkpoint_connection.execute("PRAGMA synchronous = FULL")
            self._checkpoint_connection.execute("PRAGMA trusted_schema = OFF")
            self._checkpoint_connection.execute("PRAGMA secure_delete = ON")
            serde = JsonPlusSerializer(
                pickle_fallback=False,
                allowed_json_modules=None,
                allowed_msgpack_modules=None,
            )
            self._checkpointer = SqliteSaver(self._checkpoint_connection, serde=serde)
            self._journal = SqliteExecutionJournal(journal_path)
            self._graph = self._build_graph()
        except Exception:
            self._checkpoint_connection.close()
            raise

    @property
    def checkpoint_path(self) -> Path:
        return self._checkpoint_path

    @property
    def journal_path(self) -> Path:
        return self._journal.path

    def __enter__(self) -> GovernedLangGraphExecutor:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close checkpoint and journal connections."""

        self._journal.close()
        self._checkpoint_connection.close()

    def _build_graph(self) -> Any:
        builder = StateGraph(GovernedGraphState)
        builder.add_node("authorize", self._authorize_node)
        builder.add_node("gate", self._gate_node)
        builder.add_node("execute", self._execute_node)
        builder.add_node("finalize", self._finalize_node)
        builder.add_edge(START, "authorize")
        builder.add_conditional_edges("authorize", self._route_after_authorize)
        builder.add_conditional_edges("gate", self._route_after_gate)
        builder.add_conditional_edges("execute", self._route_after_execute)
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self._checkpointer, name="governed-agent-runtime")

    def _authorize_node(self, state: GovernedGraphState) -> GovernedGraphUpdate:
        workflow = _state_workflow(state)
        grant = _state_authority(state)
        ledger = _state_ledger(state)
        now = self._clock.now()
        policy = self._policy.evaluate_workflow(
            workflow=workflow,
            grant=grant,
            runtime_subject=self._subject,
            now=now,
        )
        if policy.decision is Decision.BLOCKED:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=None,
                summary=policy.detail,
                kind="workflow.blocked",
                created_at=now,
            )

        for step in workflow.steps:
            if self._registry.resolve(step.agent_id) is None:
                return _terminal_blocked(
                    state=state,
                    ledger=ledger,
                    step=None,
                    summary="one or more workflow agents are not registered",
                    kind="workflow.blocked",
                    created_at=now,
                )

        claim = self._journal.claim_authority(
            grant=grant,
            thread_id=state["thread_id"],
            consumed_at=now,
        )
        if not claim.allowed:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=None,
                summary=claim.detail,
                kind="workflow.blocked",
                created_at=now,
            )

        ledger.append(
            producer="langgraph-adapter",
            kind="workflow.started",
            payload={
                "workflow_id": workflow.workflow_id,
                "correlation_id": workflow.correlation_id,
                "authorization_id": grant.authorization_id,
                "thread_id": state["thread_id"],
            },
            created_at=now,
        )
        return {
            "authority_consumed": claim.consumed,
            "summary": claim.detail,
            "evidence": ledger.export(),
        }

    @staticmethod
    def _route_after_authorize(state: GovernedGraphState) -> str:
        return END if state["terminal"] else "gate"

    def _gate_node(self, state: GovernedGraphState) -> GovernedGraphUpdate:
        workflow = _state_workflow(state)
        grant = _state_authority(state)
        ledger = _state_ledger(state)
        now = self._clock.now()
        workflow_policy = self._policy.evaluate_workflow(
            workflow=workflow,
            grant=grant,
            runtime_subject=self._subject,
            now=now,
        )
        if workflow_policy.decision is Decision.BLOCKED:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=None,
                summary="workflow authority is no longer valid on resume",
                kind="workflow.blocked",
                created_at=now,
            )
        authority_claim = self._journal.claim_authority(
            grant=grant,
            thread_id=state["thread_id"],
            consumed_at=now,
        )
        if not authority_claim.allowed:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=None,
                summary=authority_claim.detail,
                kind="workflow.blocked",
                created_at=now,
            )
        index = state["current_step"]
        if index >= len(workflow.steps):
            return {"summary": "all workflow steps are ready to finalize"}
        step = workflow.steps[index]
        registered = self._registry.resolve(step.agent_id)
        if registered is None:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=step,
                summary="workflow agent is not registered",
                kind="step.blocked",
                created_at=self._clock.now(),
            )

        while True:
            evaluation = self._policy.evaluate_step(
                step=step,
                descriptor=registered.descriptor,
                grant=grant,
                evidence=ledger,
            )
            if evaluation.decision is Decision.PASS:
                return {
                    "summary": f"step policy accepted: {step.step_id}",
                    "evidence": ledger.export(),
                }

            missing = tuple(
                kind for kind in step.requires_evidence_kinds if not ledger.contains_kind(kind)
            )
            only_missing_evidence = bool(missing) and set(evaluation.reasons) == {
                PolicyReason.REQUIRED_EVIDENCE_MISSING
            }
            if not only_missing_evidence:
                return _terminal_blocked(
                    state=state,
                    ledger=ledger,
                    step=step,
                    summary=evaluation.detail,
                    kind="step.blocked",
                    created_at=self._clock.now(),
                )

            resume_value = interrupt(
                {
                    "schema_version": "1.0",
                    "decision": Decision.BLOCKED.value,
                    "thread_id": state["thread_id"],
                    "workflow_id": workflow.workflow_id,
                    "step_id": step.step_id,
                    "effect": step.effect.value,
                    "missing_evidence_kinds": list(missing),
                    "evidence_head": ledger.head_digest,
                    "message": "human evidence is required before execution",
                }
            )
            response = _mapping_object(resume_value, "resume payload")
            if response.get("thread_id") != state["thread_id"]:
                raise LangGraphExecutionError("resume thread_id does not match the interruption")
            if response.get("workflow_id") != workflow.workflow_id:
                raise LangGraphExecutionError("resume workflow_id does not match the interruption")
            if response.get("step_id") != step.step_id:
                raise LangGraphExecutionError("resume step_id does not match the interruption")
            approved = response.get("approved")
            raw_evidence = response.get("evidence", [])
            if not isinstance(approved, bool):
                raise LangGraphExecutionError("resume approved must be a boolean")
            if not isinstance(raw_evidence, list):
                raise LangGraphExecutionError("resume evidence must be an array")
            if not approved:
                return _terminal_blocked(
                    state=state,
                    ledger=ledger,
                    step=step,
                    summary="human reviewer denied the blocked step",
                    kind="step.blocked",
                    created_at=self._clock.now(),
                )

            for evidence_index, raw_item in enumerate(raw_evidence):
                item = _mapping_object(raw_item, f"resume evidence[{evidence_index}]")
                kind = item.get("kind")
                payload = item.get("payload", {})
                created_at = _parse_utc(
                    item.get("created_at"), f"resume evidence[{evidence_index}].created_at"
                )
                if not isinstance(kind, str) or kind not in missing:
                    raise LangGraphExecutionError(
                        "resume evidence kind must match a currently missing requirement"
                    )
                if not isinstance(payload, dict):
                    raise LangGraphExecutionError("resume evidence payload must be an object")
                ledger.append(
                    producer="human-review",
                    kind=kind,
                    payload=cast(Mapping[str, object], payload),
                    created_at=created_at,
                )

    @staticmethod
    def _route_after_gate(state: GovernedGraphState) -> str:
        if state["terminal"]:
            return END
        workflow = workflow_from_mapping(state["workflow"])
        return "finalize" if state["current_step"] >= len(workflow.steps) else "execute"

    def _execute_node(self, state: GovernedGraphState) -> GovernedGraphUpdate:
        workflow = _state_workflow(state)
        grant = _state_authority(state)
        ledger = _state_ledger(state)
        index = state["current_step"]
        if index >= len(workflow.steps):
            raise LangGraphExecutionError("execute node has no current step")
        step = workflow.steps[index]
        registered = self._registry.resolve(step.agent_id)
        if registered is None:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=step,
                summary="workflow agent disappeared after authorization",
                kind="step.blocked",
                created_at=self._clock.now(),
            )

        request_digest = _request_digest(
            thread_id=state["thread_id"],
            workflow=workflow,
            grant=grant,
            step=step,
            evidence_head=ledger.head_digest,
        )
        claim = self._journal.claim_step(
            thread_id=state["thread_id"],
            workflow_id=workflow.workflow_id,
            step_id=step.step_id,
            request_digest=request_digest,
            started_at=self._clock.now(),
        )
        if claim.state is StepClaimState.IN_DOUBT:
            return _terminal_blocked(
                state=state,
                ledger=ledger,
                step=step,
                summary="step execution is in-doubt; human reconciliation is required",
                kind="step.in_doubt",
                created_at=self._clock.now(),
            )

        if claim.state in {StepClaimState.COMPLETED, StepClaimState.FAILED}:
            if claim.outcome is None:
                raise JournalError("terminal step claim did not include an outcome")
            outcome = _outcome_from_mapping(claim.outcome)
            replayed = True
        else:
            request = AgentRequest(
                workflow_id=workflow.workflow_id,
                correlation_id=workflow.correlation_id,
                step_id=step.step_id,
                action=step.action,
                input_data=step.input_data,
                evidence_kinds=ledger.kinds,
            )
            try:
                outcome = registered.handler(request)
            except Exception as exc:  # noqa: BLE001 - converted to controlled evidence.
                outcome = AgentOutcome(
                    Decision.FAIL,
                    "agent handler raised an exception",
                    output={"exception_type": type(exc).__name__},
                )
            outcome_mapping = _outcome_to_mapping(outcome)
            self._journal.finish_step(
                thread_id=state["thread_id"],
                workflow_id=workflow.workflow_id,
                step_id=step.step_id,
                request_digest=request_digest,
                outcome=outcome_mapping,
                failed=outcome.decision is Decision.FAIL,
                finished_at=self._clock.now(),
            )
            replayed = False

        evidence_ids: list[str] = []
        for item in outcome.evidence:
            record = ledger.append(
                producer=registered.descriptor.agent_id,
                kind=item.kind,
                payload=item.payload,
                created_at=self._clock.now(),
            )
            evidence_ids.append(record.evidence_id)
        completion = ledger.append(
            producer="langgraph-adapter",
            kind=f"step.{outcome.decision.value.lower()}",
            payload={
                "step_id": step.step_id,
                "agent_id": step.agent_id,
                "summary": outcome.summary,
                "journal_replay": replayed,
                "request_digest": request_digest,
            },
            created_at=self._clock.now(),
        )
        evidence_ids.append(completion.evidence_id)
        results = _state_step_results(state)
        results.append(
            StepResult(
                step_id=step.step_id,
                decision=outcome.decision,
                summary=outcome.summary,
                evidence_ids=tuple(evidence_ids),
                output=outcome.output,
            )
        )
        update: GovernedGraphUpdate = {
            "step_results": [_step_result_to_mapping(item) for item in results],
            "evidence": ledger.export(),
            "summary": outcome.summary,
        }
        if outcome.decision is Decision.PASS:
            update["current_step"] = index + 1
            return update
        update["decision"] = outcome.decision.value
        update["summary"] = f"workflow stopped at {outcome.decision.value} step {step.step_id}"
        update["terminal"] = True
        return update

    @staticmethod
    def _route_after_execute(state: GovernedGraphState) -> str:
        if state["terminal"]:
            return END
        workflow = workflow_from_mapping(state["workflow"])
        return "finalize" if state["current_step"] >= len(workflow.steps) else "gate"

    def _finalize_node(self, state: GovernedGraphState) -> GovernedGraphUpdate:
        workflow = _state_workflow(state)
        ledger = _state_ledger(state)
        ledger.append(
            producer="langgraph-adapter",
            kind="workflow.completed",
            payload={"workflow_id": workflow.workflow_id, "decision": Decision.PASS.value},
            created_at=self._clock.now(),
        )
        ledger.verify()
        return {
            "decision": Decision.PASS.value,
            "summary": "all workflow steps passed",
            "terminal": True,
            "evidence": ledger.export(),
        }

    @staticmethod
    def _config(thread_id: str) -> RunnableConfig:
        if not thread_id.strip():
            raise ValueError("thread_id must not be empty")
        return {"configurable": {"thread_id": thread_id.strip()}}

    def start(
        self,
        workflow: WorkflowSpec,
        grant: AuthorityGrant,
        *,
        thread_id: str,
        evidence: EvidenceLedger | None = None,
    ) -> GovernedGraphRun:
        """Start one new persistent graph thread."""

        config = self._config(thread_id)
        if self._checkpointer.get_tuple(config) is not None:
            raise LangGraphExecutionError("thread already exists; use resume or inspect")
        ledger = evidence or EvidenceLedger()
        ledger.verify()
        initial: GovernedGraphState = {
            "schema_version": "1.0",
            "thread_id": thread_id.strip(),
            "workflow": _workflow_to_mapping(workflow),
            "authority": _authority_to_mapping(grant),
            "current_step": 0,
            "decision": None,
            "summary": "graph initialized",
            "terminal": False,
            "authority_consumed": False,
            "evidence": ledger.export(),
            "step_results": [],
        }
        output = cast(Mapping[str, object], self._graph.invoke(initial, config=config))
        return self._run_from_output(thread_id.strip(), output)

    def resume(self, decision: ResumeDecision) -> GovernedGraphRun:
        """Resume one interrupted thread with an exact, context-bound human decision."""

        thread_id = decision.thread_id.strip()
        config = self._config(thread_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise LangGraphExecutionError("thread does not exist")
        raw_interrupts = _snapshot_interrupts(snapshot)
        if not raw_interrupts:
            raise LangGraphExecutionError("thread is not interrupted")
        if len(raw_interrupts) != 1:
            raise LangGraphExecutionError("thread has an unsupported number of interrupts")
        raw_value = getattr(raw_interrupts[0], "value", raw_interrupts[0])
        interruption = _mapping_object(raw_value, "interrupt payload")
        expected = {
            "thread_id": decision.thread_id,
            "workflow_id": decision.workflow_id,
            "step_id": decision.step_id,
        }
        for field_name, supplied in expected.items():
            if interruption.get(field_name) != supplied:
                raise LangGraphExecutionError(
                    f"resume {field_name} does not match the active interruption"
                )
        created_at = _utc_text(self._clock.now())
        payload = {
            **expected,
            "approved": decision.approved,
            "evidence": [
                {
                    **_evidence_item_to_mapping(item),
                    "created_at": created_at,
                }
                for item in decision.evidence
            ],
        }
        output = cast(
            Mapping[str, object],
            self._graph.invoke(Command(resume=payload), config=config),
        )
        return self._run_from_output(thread_id, output)

    def inspect(self, *, thread_id: str) -> GovernedGraphRun:
        """Read the latest checkpoint without executing a node."""

        config = self._config(thread_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise LangGraphExecutionError("thread does not exist")
        values = dict(cast(Mapping[str, object], snapshot.values))
        interrupts = _snapshot_interrupts(snapshot)
        if interrupts:
            values["__interrupt__"] = list(interrupts)
        return self._run_from_output(thread_id.strip(), values)

    @staticmethod
    def _run_from_output(thread_id: str, output: Mapping[str, object]) -> GovernedGraphRun:
        if output.get("schema_version") != "1.0":
            raise LangGraphExecutionError("graph state schema_version is unsupported")
        if output.get("thread_id") != thread_id:
            raise LangGraphExecutionError(
                "graph state thread_id does not match the requested thread"
            )
        raw_interrupts = output.get("__interrupt__", ())
        interrupt_payloads: list[Mapping[str, object]] = []
        if isinstance(raw_interrupts, Sequence) and not isinstance(
            raw_interrupts, (str, bytes, bytearray)
        ):
            for item in raw_interrupts:
                value = getattr(item, "value", item)
                if isinstance(value, dict):
                    interrupt_payloads.append(cast(Mapping[str, object], value))

        decision_raw = output.get("decision")
        terminal = output.get("terminal") is True
        if interrupt_payloads:
            status: GraphStatus = "INTERRUPTED"
            decision = Decision.BLOCKED
        elif terminal:
            status = "TERMINAL"
            if not isinstance(decision_raw, str):
                raise LangGraphExecutionError("terminal graph state has no decision")
            try:
                decision = Decision(decision_raw)
            except ValueError as exc:
                raise LangGraphExecutionError("terminal graph decision is unsupported") from exc
        else:
            status = "RUNNING"
            decision = None

        summary = output.get("summary", "")
        current_step = output.get("current_step", 0)
        authority_consumed = output.get("authority_consumed", False)
        raw_evidence = output.get("evidence", [])
        raw_steps = output.get("step_results", [])
        if not isinstance(summary, str):
            raise LangGraphExecutionError("graph summary must be a string")
        if not isinstance(current_step, int):
            raise LangGraphExecutionError("graph current_step must be an integer")
        if not isinstance(authority_consumed, bool):
            raise LangGraphExecutionError("graph authority_consumed must be a boolean")
        if not isinstance(raw_evidence, list) or not all(
            isinstance(item, dict) for item in raw_evidence
        ):
            raise LangGraphExecutionError("graph evidence must be an array of objects")
        if not isinstance(raw_steps, list) or not all(isinstance(item, dict) for item in raw_steps):
            raise LangGraphExecutionError("graph step_results must be an array of objects")
        ledger = _ledger_from_export([cast(Mapping[str, object], item) for item in raw_evidence])
        steps = tuple(
            _step_result_from_mapping(cast(Mapping[str, object], item)) for item in raw_steps
        )
        return GovernedGraphRun(
            thread_id=thread_id,
            status=status,
            decision=decision,
            summary=summary,
            current_step=current_step,
            authority_consumed=authority_consumed,
            evidence_head=ledger.head_digest,
            steps=steps,
            interrupts=tuple(interrupt_payloads),
        )


def workflow_result_from_run(run: GovernedGraphRun, workflow: WorkflowSpec) -> WorkflowResult:
    """Convert a terminal graph run to the core runtime result contract."""

    if run.status != "TERMINAL" or run.decision is None:
        raise LangGraphExecutionError("only terminal graph runs can become WorkflowResult")
    return WorkflowResult(
        workflow_id=workflow.workflow_id,
        correlation_id=workflow.correlation_id,
        decision=run.decision,
        summary=run.summary,
        steps=run.steps,
        evidence_head=run.evidence_head,
        authority_consumed=run.authority_consumed,
    )
