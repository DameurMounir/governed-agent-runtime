from __future__ import annotations

from dataclasses import replace

from governed_agent_runtime.clock import FrozenClock
from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.evidence import EvidenceLedger
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
from governed_agent_runtime.runtime import AuthorityUseRegistry, GovernedRuntime


def passing_handler(request: AgentRequest) -> AgentOutcome:
    return AgentOutcome(
        Decision.PASS,
        "done",
        output={"action": request.action},
        evidence=(EvidenceItem("agent.completed", {"step": request.step_id}),),
    )


def blocking_handler(request: AgentRequest) -> AgentOutcome:
    return AgentOutcome(Decision.BLOCKED, f"blocked {request.step_id}")


def failing_handler(request: AgentRequest) -> AgentOutcome:
    del request
    raise RuntimeError("boom")


def _runtime(
    registry: AgentRegistry, clock: FrozenClock, uses: AuthorityUseRegistry | None = None
) -> GovernedRuntime:
    return GovernedRuntime(subject="runtime", registry=registry, clock=clock, authority_uses=uses)


def _workflow(*steps: StepSpec) -> WorkflowSpec:
    return WorkflowSpec("workflow-1", "corr-1", tuple(steps))


def test_pass_workflow_records_evidence(
    registry: AgentRegistry,
    echo_descriptor: AgentDescriptor,
    clock: FrozenClock,
    grant: AuthorityGrant,
) -> None:
    registry.register(echo_descriptor, passing_handler)
    workflow = _workflow(
        StepSpec("s1", "echo", "first", "agent:echo"),
        StepSpec(
            "s2",
            "echo",
            "second",
            "agent:echo",
            requires_evidence_kinds=("agent.completed",),
        ),
    )
    ledger = EvidenceLedger()
    result = _runtime(registry, clock).execute(workflow, grant, evidence=ledger)
    assert result.decision is Decision.PASS
    assert result.authority_consumed
    assert [step.decision for step in result.steps] == [Decision.PASS, Decision.PASS]
    assert result.steps[0].output == {"action": "first"}
    assert ledger.kinds[0] == "workflow.started"
    assert ledger.kinds[-1] == "workflow.completed"
    ledger.verify()


def test_invalid_workflow_authority_blocks_before_consumption(
    registry: AgentRegistry,
    echo_descriptor: AgentDescriptor,
    clock: FrozenClock,
    grant: AuthorityGrant,
) -> None:
    calls = 0

    def counting_handler(request: AgentRequest) -> AgentOutcome:
        nonlocal calls
        calls += 1
        return passing_handler(request)

    registry.register(echo_descriptor, counting_handler)
    result = _runtime(registry, clock).execute(
        _workflow(StepSpec("s1", "echo", "run", "agent:echo")),
        replace(grant, subject="wrong"),
    )
    assert result.decision is Decision.BLOCKED
    assert not result.authority_consumed
    assert calls == 0


def test_unknown_agent_blocks_before_consumption(
    registry: AgentRegistry, clock: FrozenClock, grant: AuthorityGrant
) -> None:
    result = _runtime(registry, clock).execute(
        _workflow(StepSpec("s1", "unknown", "run", "agent:echo")), grant
    )
    assert result.decision is Decision.BLOCKED
    assert not result.authority_consumed
    assert result.evidence_head is None


def test_missing_evidence_blocks_after_authority_consumption(
    registry: AgentRegistry,
    echo_descriptor: AgentDescriptor,
    clock: FrozenClock,
    grant: AuthorityGrant,
) -> None:
    registry.register(echo_descriptor, passing_handler)
    result = _runtime(registry, clock).execute(
        _workflow(
            StepSpec(
                "s1",
                "echo",
                "run",
                "agent:echo",
                requires_evidence_kinds=("approval",),
            )
        ),
        grant,
    )
    assert result.decision is Decision.BLOCKED
    assert result.authority_consumed
    assert result.steps[0].decision is Decision.BLOCKED


def test_agent_block_stops_following_steps(
    registry: AgentRegistry, clock: FrozenClock, grant: AuthorityGrant
) -> None:
    calls: list[str] = []

    def after_handler(request: AgentRequest) -> AgentOutcome:
        calls.append(request.step_id)
        return passing_handler(request)

    registry.register(AgentDescriptor("block", "1", frozenset({"agent:echo"})), blocking_handler)
    registry.register(AgentDescriptor("after", "1", frozenset({"agent:echo"})), after_handler)
    result = _runtime(registry, clock).execute(
        _workflow(
            StepSpec("s1", "block", "run", "agent:echo"),
            StepSpec("s2", "after", "run", "agent:echo"),
        ),
        grant,
    )
    assert result.decision is Decision.BLOCKED
    assert calls == []
    assert len(result.steps) == 1


def test_agent_exception_becomes_fail(
    registry: AgentRegistry, clock: FrozenClock, grant: AuthorityGrant
) -> None:
    registry.register(AgentDescriptor("fail", "1", frozenset({"agent:echo"})), failing_handler)
    result = _runtime(registry, clock).execute(
        _workflow(StepSpec("s1", "fail", "run", "agent:echo")), grant
    )
    assert result.decision is Decision.FAIL
    assert result.steps[0].decision is Decision.FAIL


def test_single_use_authority_replay_is_blocked(
    registry: AgentRegistry,
    echo_descriptor: AgentDescriptor,
    clock: FrozenClock,
    grant: AuthorityGrant,
) -> None:
    registry.register(echo_descriptor, passing_handler)
    uses = AuthorityUseRegistry()
    runtime = _runtime(registry, clock, uses)
    workflow = _workflow(StepSpec("s1", "echo", "run", "agent:echo"))
    assert runtime.execute(workflow, grant).decision is Decision.PASS
    replay = runtime.execute(workflow, grant)
    assert replay.decision is Decision.BLOCKED
    assert not replay.authority_consumed


def test_reusable_authority_can_execute_twice(
    registry: AgentRegistry,
    echo_descriptor: AgentDescriptor,
    clock: FrozenClock,
    grant: AuthorityGrant,
) -> None:
    registry.register(echo_descriptor, passing_handler)
    reusable = replace(grant, single_use=False)
    runtime = _runtime(registry, clock)
    workflow = _workflow(StepSpec("s1", "echo", "run", "agent:echo"))
    assert runtime.execute(workflow, reusable).decision is Decision.PASS
    assert runtime.execute(workflow, reusable).decision is Decision.PASS


def test_runtime_rejects_empty_subject(registry: AgentRegistry) -> None:
    try:
        GovernedRuntime(subject=" ", registry=registry)
    except ValueError as exc:
        assert "subject" in str(exc)
    else:
        raise AssertionError("empty subject should be rejected")
