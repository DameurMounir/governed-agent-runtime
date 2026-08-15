from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.models import (
    AgentDescriptor,
    AgentOutcome,
    AuthorityGrant,
    EvidenceItem,
    StepSpec,
    WorkflowSpec,
)


def test_descriptor_normalizes_values() -> None:
    descriptor = AgentDescriptor(" echo ", " 1.0 ", frozenset({" agent:echo "}))
    assert descriptor.agent_id == "echo"
    assert descriptor.version == "1.0"
    assert descriptor.capabilities == frozenset({"agent:echo"})


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: EvidenceItem(" "), "kind"),
        (lambda: AgentDescriptor("a", "1", frozenset()), "capabilities"),
        (
            lambda: WorkflowSpec("w", "c", ()),
            "at least one step",
        ),
    ],
)
def test_required_contract_fields(factory: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_workflow_rejects_duplicate_step_ids() -> None:
    step = StepSpec("s", "a", "run", "cap")
    with pytest.raises(ValueError, match="step_id"):
        WorkflowSpec("w", "c", (step, step))


def test_step_rejects_duplicate_evidence_requirements() -> None:
    with pytest.raises(ValueError, match="unique"):
        StepSpec("s", "a", "run", "cap", requires_evidence_kinds=("x", "x"))


def test_authority_requires_aware_ordered_dates() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="later"):
        AuthorityGrant("a", "s", "w", frozenset({"cap"}), instant, instant)
    with pytest.raises(ValueError, match="timezone-aware"):
        AuthorityGrant(
            "a",
            "s",
            "w",
            frozenset({"cap"}),
            datetime(2026, 1, 1),  # noqa: DTZ001
            datetime(2026, 1, 2),  # noqa: DTZ001
        )


def test_authority_expiration() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    grant = AuthorityGrant(
        "a",
        "s",
        "w",
        frozenset({"cap"}),
        instant,
        instant + timedelta(minutes=1),
    )
    assert not grant.is_expired(instant)
    assert grant.is_expired(instant + timedelta(minutes=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        grant.is_expired(datetime(2026, 1, 1))  # noqa: DTZ001


def test_agent_outcome_freezes_copied_mapping() -> None:
    output: dict[str, object] = {"value": 1}
    outcome = AgentOutcome(Decision.PASS, "done", output=output)
    output["value"] = 2
    assert outcome.output["value"] == 1


def test_authority_rejects_empty_capability_set() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="capabilities"):
        AuthorityGrant(
            "a",
            "s",
            "w",
            frozenset(),
            instant,
            instant + timedelta(minutes=1),
        )
