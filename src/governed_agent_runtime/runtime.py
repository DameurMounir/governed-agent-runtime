"""Governed workflow orchestration with explicit authority and evidence."""

from __future__ import annotations

from dataclasses import dataclass

from governed_agent_runtime.clock import Clock, SystemClock
from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.evidence import EvidenceLedger
from governed_agent_runtime.models import (
    AgentRequest,
    AuthorityGrant,
    StepResult,
    WorkflowResult,
    WorkflowSpec,
)
from governed_agent_runtime.policy import RuntimePolicy
from governed_agent_runtime.registry import AgentRegistry, RegisteredAgent


@dataclass(slots=True)
class AuthorityUseRegistry:
    """Tracks consumed single-use grants within the runtime process."""

    _consumed: set[str]

    def __init__(self) -> None:
        self._consumed = set()

    def is_consumed(self, authorization_id: str) -> bool:
        """Return whether a grant ID has already been consumed."""

        return authorization_id in self._consumed

    def consume(self, grant: AuthorityGrant) -> bool:
        """Consume a single-use grant, returning false on replay."""

        if not grant.single_use:
            return True
        if self.is_consumed(grant.authorization_id):
            return False
        self._consumed.add(grant.authorization_id)
        return True


class GovernedRuntime:
    """Sequential, stop-on-non-PASS orchestration runtime."""

    def __init__(
        self,
        *,
        subject: str,
        registry: AgentRegistry,
        policy: RuntimePolicy | None = None,
        clock: Clock | None = None,
        authority_uses: AuthorityUseRegistry | None = None,
    ) -> None:
        if not subject.strip():
            raise ValueError("subject must not be empty")
        self._subject = subject.strip()
        self._registry = registry
        self._policy = policy or RuntimePolicy()
        self._clock = clock or SystemClock()
        self._authority_uses = authority_uses or AuthorityUseRegistry()

    def execute(
        self,
        workflow: WorkflowSpec,
        grant: AuthorityGrant,
        *,
        evidence: EvidenceLedger | None = None,
    ) -> WorkflowResult:
        """Evaluate and run a workflow without executing after any non-PASS result."""

        ledger = evidence or EvidenceLedger()
        now = self._clock.now()
        workflow_policy = self._policy.evaluate_workflow(
            workflow=workflow,
            grant=grant,
            runtime_subject=self._subject,
            now=now,
        )
        if workflow_policy.decision is Decision.BLOCKED:
            return self._terminal(
                workflow=workflow,
                decision=Decision.BLOCKED,
                summary=workflow_policy.detail,
                steps=(),
                ledger=ledger,
                authority_consumed=False,
            )

        resolved = self._resolve_all(workflow)
        if resolved is None:
            return self._terminal(
                workflow=workflow,
                decision=Decision.BLOCKED,
                summary="one or more workflow agents are not registered",
                steps=(),
                ledger=ledger,
                authority_consumed=False,
            )

        if not self._authority_uses.consume(grant):
            return self._terminal(
                workflow=workflow,
                decision=Decision.BLOCKED,
                summary="single-use authority has already been consumed",
                steps=(),
                ledger=ledger,
                authority_consumed=False,
            )

        ledger.append(
            producer="runtime",
            kind="workflow.started",
            payload={
                "workflow_id": workflow.workflow_id,
                "correlation_id": workflow.correlation_id,
                "authorization_id": grant.authorization_id,
            },
            created_at=now,
        )

        step_results: list[StepResult] = []
        for step, registered in zip(workflow.steps, resolved, strict=True):
            policy_result = self._policy.evaluate_step(
                step=step,
                descriptor=registered.descriptor,
                grant=grant,
                evidence=ledger,
            )
            if policy_result.decision is Decision.BLOCKED:
                record = ledger.append(
                    producer="runtime",
                    kind="step.blocked",
                    payload={
                        "step_id": step.step_id,
                        "agent_id": step.agent_id,
                        "detail": policy_result.detail,
                        "reasons": [reason.value for reason in policy_result.reasons],
                    },
                    created_at=self._clock.now(),
                )
                step_results.append(
                    StepResult(
                        step_id=step.step_id,
                        decision=Decision.BLOCKED,
                        summary=policy_result.detail,
                        evidence_ids=(record.evidence_id,),
                    )
                )
                return self._terminal(
                    workflow=workflow,
                    decision=Decision.BLOCKED,
                    summary=f"workflow stopped at blocked step {step.step_id}",
                    steps=tuple(step_results),
                    ledger=ledger,
                    authority_consumed=True,
                )

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
            except Exception as exc:  # noqa: BLE001 - failures are converted to evidence, not hidden
                record = ledger.append(
                    producer="runtime",
                    kind="step.failed",
                    payload={
                        "step_id": step.step_id,
                        "agent_id": step.agent_id,
                        "exception_type": type(exc).__name__,
                    },
                    created_at=self._clock.now(),
                )
                step_results.append(
                    StepResult(
                        step_id=step.step_id,
                        decision=Decision.FAIL,
                        summary="agent handler raised an exception",
                        evidence_ids=(record.evidence_id,),
                    )
                )
                return self._terminal(
                    workflow=workflow,
                    decision=Decision.FAIL,
                    summary=f"workflow failed at step {step.step_id}",
                    steps=tuple(step_results),
                    ledger=ledger,
                    authority_consumed=True,
                )

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
                producer="runtime",
                kind=f"step.{outcome.decision.value.lower()}",
                payload={
                    "step_id": step.step_id,
                    "agent_id": step.agent_id,
                    "summary": outcome.summary,
                },
                created_at=self._clock.now(),
            )
            evidence_ids.append(completion.evidence_id)
            step_results.append(
                StepResult(
                    step_id=step.step_id,
                    decision=outcome.decision,
                    summary=outcome.summary,
                    evidence_ids=tuple(evidence_ids),
                    output=outcome.output,
                )
            )
            if outcome.decision is not Decision.PASS:
                return self._terminal(
                    workflow=workflow,
                    decision=outcome.decision,
                    summary=f"workflow stopped at {outcome.decision.value} step {step.step_id}",
                    steps=tuple(step_results),
                    ledger=ledger,
                    authority_consumed=True,
                )

        ledger.append(
            producer="runtime",
            kind="workflow.completed",
            payload={"workflow_id": workflow.workflow_id, "decision": Decision.PASS.value},
            created_at=self._clock.now(),
        )
        ledger.verify()
        return self._terminal(
            workflow=workflow,
            decision=Decision.PASS,
            summary="all workflow steps passed",
            steps=tuple(step_results),
            ledger=ledger,
            authority_consumed=True,
        )

    def _resolve_all(self, workflow: WorkflowSpec) -> tuple[RegisteredAgent, ...] | None:
        resolved: list[RegisteredAgent] = []
        for step in workflow.steps:
            registered = self._registry.resolve(step.agent_id)
            if registered is None:
                return None
            resolved.append(registered)
        return tuple(resolved)

    @staticmethod
    def _terminal(
        *,
        workflow: WorkflowSpec,
        decision: Decision,
        summary: str,
        steps: tuple[StepResult, ...],
        ledger: EvidenceLedger,
        authority_consumed: bool,
    ) -> WorkflowResult:
        ledger.verify()
        return WorkflowResult(
            workflow_id=workflow.workflow_id,
            correlation_id=workflow.correlation_id,
            decision=decision,
            summary=summary,
            steps=steps,
            evidence_head=ledger.head_digest,
            authority_consumed=authority_consumed,
        )
