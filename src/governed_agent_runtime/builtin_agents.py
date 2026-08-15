"""Small deterministic agents used by examples and the command-line interface."""

from __future__ import annotations

from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.models import AgentOutcome, AgentRequest, EvidenceItem


def echo_agent(request: AgentRequest) -> AgentOutcome:
    """Echo the provided input as evidence and output."""

    return AgentOutcome(
        decision=Decision.PASS,
        summary="echo completed",
        output={"echo": dict(request.input_data)},
        evidence=(EvidenceItem("agent.echo.output", {"input": dict(request.input_data)}),),
    )


def blocking_agent(request: AgentRequest) -> AgentOutcome:
    """Return a safe business-level block without raising."""

    reason = request.input_data.get("reason", "agent declined execution")
    return AgentOutcome(
        decision=Decision.BLOCKED,
        summary=str(reason),
        evidence=(EvidenceItem("agent.block.reason", {"reason": str(reason)}),),
    )


def failing_agent(request: AgentRequest) -> AgentOutcome:
    """Raise a deterministic exception to exercise FAIL semantics."""

    del request
    raise RuntimeError("deterministic example failure")
