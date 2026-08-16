from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import governed_agent_runtime.langgraph_adapter as adapter_module
from governed_agent_runtime.clock import FrozenClock
from governed_agent_runtime.decisions import Decision, EffectClass
from governed_agent_runtime.evidence import EvidenceLedger
from governed_agent_runtime.execution_journal import JournalError, StepClaim, StepClaimState
from governed_agent_runtime.langgraph_adapter import (
    GovernedGraphRun,
    GovernedLangGraphExecutor,
    LangGraphExecutionError,
    ResumeDecision,
    workflow_result_from_run,
)
from governed_agent_runtime.models import (
    AgentDescriptor,
    AgentOutcome,
    AgentRequest,
    AuthorityGrant,
    EvidenceItem,
    StepSpec,
    WorkflowSpec,
)
from governed_agent_runtime.registry import AgentRegistry


def _instant() -> datetime:
    return datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _workflow(
    *,
    workflow_id: str = "workflow-1",
    agent_id: str = "agent.echo",
    capability: str = "agent:echo",
    required: tuple[str, ...] = (),
    steps: int = 1,
) -> WorkflowSpec:
    return WorkflowSpec(
        workflow_id=workflow_id,
        correlation_id=f"correlation-{workflow_id}",
        steps=tuple(
            StepSpec(
                step_id=f"step-{index}",
                agent_id=agent_id,
                action="echo",
                capability=capability,
                effect=EffectClass.READ_ONLY,
                requires_evidence_kinds=required,
                input_data={"index": index},
            )
            for index in range(1, steps + 1)
        ),
    )


def _grant(
    workflow: WorkflowSpec,
    *,
    authorization_id: str = "authority-1",
    capabilities: frozenset[str] | None = None,
) -> AuthorityGrant:
    instant = _instant()
    return AuthorityGrant(
        authorization_id=authorization_id,
        subject="runtime",
        workflow_id=workflow.workflow_id,
        capabilities=capabilities or frozenset({"agent:echo"}),
        issued_at=instant - timedelta(minutes=1),
        expires_at=instant + timedelta(minutes=30),
    )


def _registry(
    handler: Callable[[AgentRequest], AgentOutcome],
    *,
    agent_id: str = "agent.echo",
    capabilities: frozenset[str] | None = None,
) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentDescriptor(agent_id, "1.0.0", capabilities or frozenset({"agent:echo"})),
        handler,
    )
    return registry


def _decision(
    run: GovernedGraphRun,
    *,
    approved: bool,
    evidence: tuple[EvidenceItem, ...] = (),
    thread_id: str | None = None,
    workflow_id: str | None = None,
    step_id: str | None = None,
) -> ResumeDecision:
    interrupt_payload = run.interrupts[0]
    return ResumeDecision(
        thread_id=thread_id or run.thread_id,
        workflow_id=workflow_id or str(interrupt_payload["workflow_id"]),
        step_id=step_id or str(interrupt_payload["step_id"]),
        approved=approved,
        evidence=evidence,
    )


def _executor(
    tmp_path: Path,
    registry: AgentRegistry,
    *,
    suffix: str = "",
    clock_at: datetime | None = None,
) -> GovernedLangGraphExecutor:
    return GovernedLangGraphExecutor(
        subject="runtime",
        registry=registry,
        checkpoint_path=tmp_path / f"checkpoints{suffix}.sqlite",
        journal_path=tmp_path / f"journal{suffix}.sqlite",
        clock=FrozenClock(clock_at or _instant()),
    )


def test_pass_executes_one_governed_handler_and_returns_terminal_result(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: AgentRequest) -> AgentOutcome:
        calls.append(request.step_id)
        return AgentOutcome(
            Decision.PASS,
            "echo completed",
            output={"step_id": request.step_id},
            evidence=(EvidenceItem("agent.output", {"ok": True}),),
        )

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler)) as executor:
        run = executor.start(workflow, _grant(workflow), thread_id="thread-1")
        inspected = executor.inspect(thread_id="thread-1")

    assert run.status == "TERMINAL"
    assert run.decision is Decision.PASS
    assert run.summary == "all workflow steps passed"
    assert run.authority_consumed
    assert run.evidence_head is not None
    assert [step.decision for step in run.steps] == [Decision.PASS]
    assert calls == ["step-1"]
    assert inspected.as_mapping() == run.as_mapping()


def test_missing_evidence_interrupts_before_handler_and_survives_restart(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(_request: AgentRequest) -> AgentOutcome:
        calls.append("called")
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    registry = _registry(handler)
    executor = _executor(tmp_path, registry)
    run = executor.start(workflow, _grant(workflow), thread_id="thread-1")
    inspected = executor.inspect(thread_id="thread-1")
    executor.close()

    assert run.status == "INTERRUPTED"
    assert inspected.as_mapping() == run.as_mapping()
    assert run.decision is Decision.BLOCKED
    assert calls == []
    assert run.interrupts[0]["missing_evidence_kinds"] == ["human.approval"]

    with _executor(tmp_path, registry) as reopened:
        resumed = reopened.resume(
            _decision(
                run,
                approved=True,
                evidence=(EvidenceItem("human.approval", {"reviewer": "human"}),),
            )
        )

    assert resumed.status == "TERMINAL"
    assert resumed.decision is Decision.PASS
    assert calls == ["called"]


def test_empty_approved_resume_reinterrupts_without_execution(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(_request: AgentRequest) -> AgentOutcome:
        calls.append("called")
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    with _executor(tmp_path, _registry(handler)) as executor:
        executor.start(workflow, _grant(workflow), thread_id="thread-1")
        interrupted = executor.inspect(thread_id="thread-1")
        repeated = executor.resume(_decision(interrupted, approved=True))

    assert repeated.status == "INTERRUPTED"
    assert repeated.interrupts[0]["missing_evidence_kinds"] == ["human.approval"]
    assert calls == []


def test_authority_is_re_evaluated_and_can_expire_while_interrupted(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(_request: AgentRequest) -> AgentOutcome:
        calls.append("called")
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    registry = _registry(handler)
    grant = _grant(workflow)
    with _executor(tmp_path, registry) as executor:
        interrupted = executor.start(workflow, grant, thread_id="thread-1")
    assert interrupted.status == "INTERRUPTED"

    after_expiry = grant.expires_at + timedelta(seconds=1)
    with _executor(tmp_path, registry, clock_at=after_expiry) as reopened:
        resumed = reopened.resume(
            _decision(
                interrupted,
                approved=True,
                evidence=(EvidenceItem("human.approval", {"approved": True}),),
            )
        )

    assert resumed.status == "TERMINAL"
    assert resumed.decision is Decision.BLOCKED
    assert "no longer valid" in resumed.summary
    assert calls == []


def test_denied_resume_is_terminal_blocked_without_handler_execution(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(_request: AgentRequest) -> AgentOutcome:
        calls.append("called")
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    with _executor(tmp_path, _registry(handler)) as executor:
        assert (
            executor.start(workflow, _grant(workflow), thread_id="thread-1").status == "INTERRUPTED"
        )
        interrupted = executor.inspect(thread_id="thread-1")
        denied = executor.resume(_decision(interrupted, approved=False))

    assert denied.status == "TERMINAL"
    assert denied.decision is Decision.BLOCKED
    assert denied.steps[-1].decision is Decision.BLOCKED
    assert calls == []


def test_two_missing_evidence_kinds_can_be_resumed_in_two_interrupts(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(_request: AgentRequest) -> AgentOutcome:
        calls.append("called")
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval", "change.ticket"))
    with _executor(tmp_path, _registry(handler)) as executor:
        first = executor.start(workflow, _grant(workflow), thread_id="thread-1")
        second = executor.resume(
            _decision(
                first,
                approved=True,
                evidence=(EvidenceItem("human.approval", {"approved": True}),),
            )
        )
        terminal = executor.resume(
            _decision(
                second,
                approved=True,
                evidence=(EvidenceItem("change.ticket", {"ticket": "CHG-1"}),),
            )
        )

    assert first.status == "INTERRUPTED"
    assert second.status == "INTERRUPTED"
    assert second.interrupts[0]["missing_evidence_kinds"] == ["change.ticket"]
    assert terminal.decision is Decision.PASS
    assert calls == ["called"]


def test_handler_exception_is_controlled_fail_without_message_leak(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        raise RuntimeError("sensitive provider detail")

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler)) as executor:
        run = executor.start(workflow, _grant(workflow), thread_id="thread-1")

    assert run.status == "TERMINAL"
    assert run.decision is Decision.FAIL
    assert run.steps[0].summary == "agent handler raised an exception"
    assert run.steps[0].output == {"exception_type": "RuntimeError"}
    assert "sensitive" not in str(run.as_mapping())


def test_agent_level_blocked_outcome_stops_graph(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.BLOCKED, "business prerequisite is not satisfied")

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler)) as executor:
        run = executor.start(workflow, _grant(workflow), thread_id="thread-1")

    assert run.decision is Decision.BLOCKED
    assert run.steps[0].decision is Decision.BLOCKED


def test_multi_step_graph_stops_on_first_non_pass(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: AgentRequest) -> AgentOutcome:
        calls.append(request.step_id)
        decision = Decision.FAIL if request.step_id == "step-2" else Decision.PASS
        return AgentOutcome(decision, f"{request.step_id} result")

    workflow = _workflow(steps=3)
    with _executor(tmp_path, _registry(handler)) as executor:
        run = executor.start(workflow, _grant(workflow), thread_id="thread-1")

    assert run.decision is Decision.FAIL
    assert calls == ["step-1", "step-2"]
    assert [item.step_id for item in run.steps] == ["step-1", "step-2"]


def test_unknown_agent_and_expired_authority_fail_closed(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    unknown_workflow = _workflow(agent_id="missing.agent")
    with _executor(tmp_path, _registry(handler), suffix="-unknown") as executor:
        unknown = executor.start(
            unknown_workflow,
            _grant(unknown_workflow),
            thread_id="thread-unknown",
        )

    workflow = _workflow(workflow_id="expired")
    expired = AuthorityGrant(
        authorization_id="expired-authority",
        subject="runtime",
        workflow_id=workflow.workflow_id,
        capabilities=frozenset({"agent:echo"}),
        issued_at=_instant() - timedelta(minutes=20),
        expires_at=_instant() - timedelta(minutes=1),
    )
    with _executor(tmp_path, _registry(handler), suffix="-expired") as executor:
        expired_run = executor.start(workflow, expired, thread_id="thread-expired")

    assert unknown.decision is Decision.BLOCKED
    assert expired_run.decision is Decision.BLOCKED


def test_single_use_authority_is_bound_to_first_thread(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow()
    grant = _grant(workflow)
    with _executor(tmp_path, _registry(handler)) as executor:
        first = executor.start(workflow, grant, thread_id="thread-1")
        second = executor.start(workflow, grant, thread_id="thread-2")

    assert first.decision is Decision.PASS
    assert second.decision is Decision.BLOCKED


def test_duplicate_start_and_unknown_resume_are_rejected(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler)) as executor:
        executor.start(workflow, _grant(workflow), thread_id="thread-1")
        with pytest.raises(LangGraphExecutionError, match="not interrupted"):
            executor.resume(ResumeDecision("thread-1", workflow.workflow_id, "step-1", False))
        with pytest.raises(LangGraphExecutionError, match="thread already exists"):
            executor.start(workflow, _grant(workflow), thread_id="thread-1")
        with pytest.raises(LangGraphExecutionError, match="does not exist"):
            executor.resume(ResumeDecision("missing", workflow.workflow_id, "step-1", False))
        with pytest.raises(LangGraphExecutionError, match="does not exist"):
            executor.inspect(thread_id="missing")


def test_resume_rejects_unrequested_evidence_kind(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    with _executor(tmp_path, _registry(handler)) as executor:
        executor.start(workflow, _grant(workflow), thread_id="thread-1")
        with pytest.raises(LangGraphExecutionError, match="currently missing"):
            executor.resume(
                _decision(
                    executor.inspect(thread_id="thread-1"),
                    approved=True,
                    evidence=(EvidenceItem("unrequested", {}),),
                )
            )


def test_resume_contract_is_bound_to_active_thread_workflow_and_step(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    with _executor(tmp_path, _registry(handler)) as executor:
        interrupted = executor.start(workflow, _grant(workflow), thread_id="thread-1")
        with pytest.raises(LangGraphExecutionError, match="workflow_id"):
            executor.resume(
                _decision(
                    interrupted,
                    approved=True,
                    workflow_id="different-workflow",
                    evidence=(EvidenceItem("human.approval", {}),),
                )
            )
        with pytest.raises(LangGraphExecutionError, match="step_id"):
            executor.resume(
                _decision(
                    interrupted,
                    approved=True,
                    step_id="different-step",
                    evidence=(EvidenceItem("human.approval", {}),),
                )
            )


def test_terminal_run_converts_to_core_workflow_result(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler)) as executor:
        run = executor.start(workflow, _grant(workflow), thread_id="thread-1")

    result = workflow_result_from_run(run, workflow)
    assert result.workflow_id == workflow.workflow_id
    assert result.decision is Decision.PASS
    with pytest.raises(LangGraphExecutionError, match="terminal"):
        workflow_result_from_run(
            type(run)(
                thread_id=run.thread_id,
                status="RUNNING",
                decision=None,
                summary="running",
                current_step=0,
                authority_consumed=False,
                evidence_head=None,
                steps=(),
                interrupts=(),
            ),
            workflow,
        )


def test_checkpoint_and_journal_files_are_private(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    with _executor(tmp_path, _registry(handler)) as executor:
        checkpoint = executor.checkpoint_path
        journal = executor.journal_path

    assert checkpoint.stat().st_mode & 0o077 == 0
    assert journal.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("field_name", ["thread_id", "workflow_id", "step_id"])
def test_resume_decision_rejects_empty_identity(field_name: str) -> None:
    values = {
        "thread_id": "thread-1",
        "workflow_id": "workflow-1",
        "step_id": "step-1",
    }
    values[field_name] = " "
    with pytest.raises(ValueError, match=field_name):
        ResumeDecision(
            thread_id=values["thread_id"],
            workflow_id=values["workflow_id"],
            step_id=values["step_id"],
            approved=False,
        )


def test_resume_decision_rejects_duplicate_evidence_kinds() -> None:
    duplicate = EvidenceItem("human.approval", {})
    with pytest.raises(ValueError, match="unique"):
        ResumeDecision(
            "thread-1",
            "workflow-1",
            "step-1",
            True,
            evidence=(duplicate, duplicate),
        )


def test_langgraph_time_helpers_fail_closed() -> None:
    naive = datetime(2026, 8, 15, 12, 0)  # noqa: DTZ001 - intentional invalid input.
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter_module._utc_text(naive)
    with pytest.raises(LangGraphExecutionError, match="ISO-8601 string"):
        adapter_module._parse_utc(None, "created_at")
    with pytest.raises(LangGraphExecutionError, match="ISO-8601 datetime"):
        adapter_module._parse_utc("not-a-date", "created_at")
    with pytest.raises(LangGraphExecutionError, match="include a timezone"):
        adapter_module._parse_utc("2026-08-15T12:00:00", "created_at")
    assert adapter_module._parse_utc("2026-08-15T13:00:00+01:00", "created_at") == _instant()


def test_mapping_object_rejects_non_objects() -> None:
    with pytest.raises(LangGraphExecutionError, match="JSON object"):
        adapter_module._mapping_object([], "payload")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"summary": "ok"}, "decision must be a string"),
        ({"decision": "PASS"}, "summary must be a string"),
        ({"decision": "PASS", "summary": "ok", "output": []}, "output must be an object"),
        (
            {"decision": "PASS", "summary": "ok", "evidence": {}},
            "evidence must be an array",
        ),
        ({"decision": "UNKNOWN", "summary": "ok"}, "decision is unsupported"),
        (
            {"decision": "PASS", "summary": "ok", "evidence": [[]]},
            "must be a JSON object",
        ),
        (
            {"decision": "PASS", "summary": "ok", "evidence": [{"kind": ""}]},
            "kind must not be empty",
        ),
        (
            {
                "decision": "PASS",
                "summary": "ok",
                "evidence": [{"kind": "kind", "payload": []}],
            },
            "payload must be an object",
        ),
    ],
)
def test_stored_outcome_validation_is_fail_closed(value: dict[str, object], message: str) -> None:
    with pytest.raises(LangGraphExecutionError, match=message):
        adapter_module._outcome_from_mapping(value)


def test_stored_outcome_round_trip_preserves_evidence() -> None:
    outcome = AgentOutcome(
        Decision.PASS,
        "completed",
        output={"value": 1},
        evidence=(EvidenceItem("agent.output", {"value": 1}),),
    )
    restored = adapter_module._outcome_from_mapping(adapter_module._outcome_to_mapping(outcome))
    assert restored == outcome


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "step_id"),
        ({"step_id": "step-1"}, "decision must be a string"),
        ({"step_id": "step-1", "decision": "PASS"}, "summary must be a string"),
        (
            {"step_id": "step-1", "decision": "PASS", "summary": "ok", "evidence_ids": [1]},
            "evidence_ids",
        ),
        (
            {"step_id": "step-1", "decision": "PASS", "summary": "ok", "output": []},
            "output must be an object",
        ),
        (
            {"step_id": "step-1", "decision": "UNKNOWN", "summary": "ok"},
            "decision is unsupported",
        ),
    ],
)
def test_stored_step_result_validation_is_fail_closed(
    value: dict[str, object], message: str
) -> None:
    with pytest.raises(LangGraphExecutionError, match=message):
        adapter_module._step_result_from_mapping(value)


def test_stored_step_result_round_trip() -> None:
    result = adapter_module._step_result_from_mapping(
        {
            "step_id": "step-1",
            "decision": "PASS",
            "summary": "ok",
            "evidence_ids": ["ev-000001"],
            "output": {"value": 1},
        }
    )
    assert adapter_module._step_result_to_mapping(result)["decision"] == "PASS"


def _exported_evidence() -> list[dict[str, object]]:
    ledger = EvidenceLedger()
    ledger.append(
        producer="test",
        kind="test.evidence",
        payload={"value": 1},
        created_at=_instant(),
    )
    return ledger.export()


def test_checkpoint_evidence_round_trip() -> None:
    rebuilt = adapter_module._ledger_from_export(_exported_evidence())
    assert rebuilt.head_digest is not None


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("sequence", 2, "sequence"),
        ("producer", "", "producer"),
        ("kind", "", "kind"),
        ("payload", [], "payload"),
        ("digest", "0" * 64, "chain verification"),
        ("created_at", "not-a-date", "ISO-8601 datetime"),
    ],
)
def test_checkpoint_evidence_validation_is_fail_closed(
    field_name: str, value: object, message: str
) -> None:
    records = deepcopy(_exported_evidence())
    records[0][field_name] = value
    with pytest.raises(LangGraphExecutionError, match=message):
        adapter_module._ledger_from_export(records)


def test_snapshot_interrupts_ignores_non_sequence_task_values() -> None:
    snapshot = SimpleNamespace(
        tasks=(
            SimpleNamespace(interrupts="not-a-sequence"),
            SimpleNamespace(interrupts=(SimpleNamespace(value={"step_id": "step-1"}),)),
        )
    )
    assert len(adapter_module._snapshot_interrupts(snapshot)) == 1


def test_executor_rejects_empty_subject(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="subject"):
        GovernedLangGraphExecutor(
            subject=" ",
            registry=AgentRegistry(),
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            journal_path=tmp_path / "journal.sqlite",
        )


def test_executor_closes_checkpoint_connection_when_wal_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    class FakeConnection:
        def execute(self, statement: str) -> SimpleNamespace:
            if statement == "PRAGMA journal_mode = WAL":
                return SimpleNamespace(fetchone=lambda: ("delete",))
            return SimpleNamespace(fetchone=lambda: None)

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        adapter_module.sqlite3, "connect", lambda *_args, **_kwargs: FakeConnection()
    )
    with pytest.raises(LangGraphExecutionError, match="WAL mode"):
        GovernedLangGraphExecutor(
            subject="runtime",
            registry=AgentRegistry(),
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            journal_path=tmp_path / "journal.sqlite",
        )
    assert closed


def test_executor_closes_checkpoint_connection_when_journal_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    class FakeConnection:
        def execute(self, statement: str) -> SimpleNamespace:
            if statement == "PRAGMA journal_mode = WAL":
                return SimpleNamespace(fetchone=lambda: ("wal",))
            return SimpleNamespace(fetchone=lambda: None)

        def commit(self) -> None:
            return None

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        adapter_module.sqlite3, "connect", lambda *_args, **_kwargs: FakeConnection()
    )
    monkeypatch.setattr(
        adapter_module,
        "SqliteExecutionJournal",
        lambda _path: (_ for _ in ()).throw(JournalError("journal setup failed")),
    )
    with pytest.raises(JournalError, match="journal setup failed"):
        GovernedLangGraphExecutor(
            subject="runtime",
            registry=AgentRegistry(),
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            journal_path=tmp_path / "journal.sqlite",
        )
    assert closed


def _raw_state(
    workflow: WorkflowSpec,
    grant: AuthorityGrant,
    *,
    thread_id: str = "thread-1",
    current_step: int = 0,
) -> adapter_module.GovernedGraphState:
    return {
        "schema_version": "1.0",
        "thread_id": thread_id,
        "workflow": adapter_module._workflow_to_mapping(workflow),
        "authority": adapter_module._authority_to_mapping(grant),
        "current_step": current_step,
        "decision": None,
        "summary": "initialized",
        "terminal": False,
        "authority_consumed": False,
        "evidence": [],
        "step_results": [],
    }


def test_gate_handles_finalize_unknown_agent_and_non_evidence_policy_blocks(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler), suffix="-gate") as executor:
        finalizing = executor._gate_node(_raw_state(workflow, _grant(workflow), current_step=1))
        assert finalizing["summary"] == "all workflow steps are ready to finalize"

        missing = _workflow(workflow_id="missing", agent_id="missing.agent")
        blocked_missing = executor._gate_node(
            _raw_state(
                missing,
                _grant(missing, authorization_id="authority-missing"),
                thread_id="thread-missing",
            )
        )
        assert blocked_missing["decision"] == Decision.BLOCKED.value

        no_capability = _grant(
            workflow,
            authorization_id="authority-policy",
            capabilities=frozenset({"agent:other"}),
        )
        blocked_policy = executor._gate_node(
            _raw_state(workflow, no_capability, thread_id="thread-policy")
        )
        assert blocked_policy["decision"] == Decision.BLOCKED.value


def test_gate_rejects_authority_owned_by_another_thread(tmp_path: Path) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow()
    grant = _grant(workflow)
    with _executor(tmp_path, _registry(handler), suffix="-owner") as executor:
        executor._journal.claim_authority(
            grant=grant,
            thread_id="owner-thread",
            consumed_at=_instant(),
        )
        update = executor._gate_node(_raw_state(workflow, grant, thread_id="other-thread"))
    assert update["decision"] == Decision.BLOCKED.value


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            {
                "thread_id": "other",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": [],
            },
            "thread_id",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "other",
                "step_id": "step-1",
                "approved": True,
                "evidence": [],
            },
            "workflow_id",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "other",
                "approved": True,
                "evidence": [],
            },
            "step_id",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": "yes",
                "evidence": [],
            },
            "approved",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": {},
            },
            "evidence must be an array",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": [
                    {
                        "kind": "human.approval",
                        "payload": [],
                        "created_at": "2026-08-15T12:00:00Z",
                    }
                ],
            },
            "payload must be an object",
        ),
    ],
)
def test_gate_rejects_malformed_resume_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    message: str,
) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    monkeypatch.setattr(adapter_module, "interrupt", lambda _payload: response)
    with (
        _executor(tmp_path, _registry(handler), suffix=f"-{message.replace(' ', '-')}") as executor,
        pytest.raises(LangGraphExecutionError, match=message),
    ):
        executor._gate_node(_raw_state(workflow, _grant(workflow)))


def test_execute_node_fail_closed_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow()
    with _executor(tmp_path, _registry(handler), suffix="-execute") as executor:
        with pytest.raises(LangGraphExecutionError, match="no current step"):
            executor._execute_node(_raw_state(workflow, _grant(workflow), current_step=1))

        disappeared = _workflow(workflow_id="disappeared", agent_id="missing.agent")
        blocked = executor._execute_node(
            _raw_state(disappeared, _grant(disappeared), thread_id="thread-disappeared")
        )
        assert blocked["decision"] == Decision.BLOCKED.value

        monkeypatch.setattr(
            executor._journal,
            "claim_step",
            lambda **_kwargs: StepClaim(StepClaimState.IN_DOUBT),
        )
        in_doubt = executor._execute_node(_raw_state(workflow, _grant(workflow)))
        assert in_doubt["decision"] == Decision.BLOCKED.value

        monkeypatch.setattr(
            executor._journal,
            "claim_step",
            lambda **_kwargs: StepClaim(StepClaimState.COMPLETED),
        )
        with pytest.raises(JournalError, match="did not include an outcome"):
            executor._execute_node(_raw_state(workflow, _grant(workflow)))


def test_execute_node_replays_completed_journal_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def handler(_request: AgentRequest) -> AgentOutcome:
        nonlocal calls
        calls += 1
        return AgentOutcome(Decision.FAIL, "should not execute")

    workflow = _workflow()
    stored = adapter_module._outcome_to_mapping(
        AgentOutcome(
            Decision.PASS,
            "stored completion",
            output={"replayed": True},
            evidence=(EvidenceItem("stored.evidence", {"value": 1}),),
        )
    )
    with _executor(tmp_path, _registry(handler), suffix="-replay") as executor:
        monkeypatch.setattr(
            executor._journal,
            "claim_step",
            lambda **_kwargs: StepClaim(StepClaimState.COMPLETED, stored),
        )
        update = executor._execute_node(_raw_state(workflow, _grant(workflow)))

    assert calls == 0
    assert update["current_step"] == 1
    assert update["summary"] == "stored completion"


def test_config_and_multi_interrupt_resume_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="thread_id"):
        GovernedLangGraphExecutor._config(" ")

    def handler(_request: AgentRequest) -> AgentOutcome:
        return AgentOutcome(Decision.PASS, "completed")

    workflow = _workflow(required=("human.approval",))
    with _executor(tmp_path, _registry(handler), suffix="-interrupt-count") as executor:
        interrupted = executor.start(workflow, _grant(workflow), thread_id="thread-1")
        interrupt = SimpleNamespace(value=dict(interrupted.interrupts[0]))
        snapshot = SimpleNamespace(
            values={"present": True},
            tasks=(
                SimpleNamespace(interrupts=(interrupt,)),
                SimpleNamespace(interrupts=(interrupt,)),
            ),
        )
        monkeypatch.setattr(executor._graph, "get_state", lambda _config: snapshot)
        with pytest.raises(LangGraphExecutionError, match="unsupported number"):
            executor.resume(_decision(interrupted, approved=False))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"schema_version": "2.0"}, "schema_version"),
        ({"thread_id": "other"}, "thread_id"),
        ({"terminal": True, "decision": None}, "no decision"),
        ({"terminal": True, "decision": "UNKNOWN"}, "unsupported"),
        ({"summary": []}, "summary"),
        ({"current_step": "0"}, "current_step"),
        ({"authority_consumed": "yes"}, "authority_consumed"),
        ({"evidence": [1]}, "evidence"),
        ({"step_results": [1]}, "step_results"),
    ],
)
def test_run_from_output_rejects_malformed_checkpoint_state(
    updates: dict[str, object], message: str
) -> None:
    output: dict[str, object] = {
        "schema_version": "1.0",
        "thread_id": "thread-1",
        "current_step": 0,
        "decision": None,
        "summary": "running",
        "terminal": False,
        "authority_consumed": False,
        "evidence": [],
        "step_results": [],
    }
    output.update(updates)
    with pytest.raises(LangGraphExecutionError, match=message):
        GovernedLangGraphExecutor._run_from_output("thread-1", output)


def test_run_from_output_supports_running_and_ignores_non_object_interrupts() -> None:
    output: dict[str, object] = {
        "schema_version": "1.0",
        "thread_id": "thread-1",
        "current_step": 0,
        "decision": None,
        "summary": "running",
        "terminal": False,
        "authority_consumed": False,
        "evidence": [],
        "step_results": [],
        "__interrupt__": ["ignored", SimpleNamespace(value="also-ignored")],
    }
    run = GovernedLangGraphExecutor._run_from_output("thread-1", output)
    assert run.status == "RUNNING"
    assert run.decision is None
