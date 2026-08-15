from __future__ import annotations

from datetime import UTC

import pytest

from governed_agent_runtime.decisions import EffectClass
from governed_agent_runtime.serialization import (
    ContractError,
    authority_from_mapping,
    workflow_from_mapping,
)


def test_workflow_from_mapping() -> None:
    workflow = workflow_from_mapping(
        {
            "workflow_id": "w",
            "correlation_id": "c",
            "steps": [
                {
                    "step_id": "s",
                    "agent_id": "builtin.echo",
                    "action": "echo",
                    "capability": "agent:echo",
                    "effect": "READ_ONLY",
                    "input_data": {"message": "hello"},
                }
            ],
        }
    )
    assert workflow.steps[0].effect is EffectClass.READ_ONLY
    assert workflow.steps[0].input_data["message"] == "hello"


def test_authority_from_mapping() -> None:
    authority = authority_from_mapping(
        {
            "authorization_id": "a",
            "subject": "runtime",
            "workflow_id": "w",
            "capabilities": ["agent:echo"],
            "issued_at": "2026-08-15T11:00:00Z",
            "expires_at": "2026-08-15T13:00:00+00:00",
            "single_use": False,
        }
    )
    assert authority.issued_at.tzinfo == UTC
    assert not authority.single_use


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"workflow_id": "w", "correlation_id": "c", "steps": "bad"},
        {
            "workflow_id": "w",
            "correlation_id": "c",
            "steps": [{"step_id": "s"}],
        },
        {
            "workflow_id": "w",
            "correlation_id": "c",
            "steps": [
                {
                    "step_id": "s",
                    "agent_id": "a",
                    "action": "x",
                    "capability": "c",
                    "effect": "UNKNOWN",
                }
            ],
        },
    ],
)
def test_invalid_workflow_contracts(data: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        workflow_from_mapping(data)


def test_authority_rejects_naive_datetime() -> None:
    with pytest.raises(ContractError, match="timezone"):
        authority_from_mapping(
            {
                "authorization_id": "a",
                "subject": "runtime",
                "workflow_id": "w",
                "capabilities": ["cap"],
                "issued_at": "2026-01-01T00:00:00",
                "expires_at": "2026-01-02T00:00:00Z",
            }
        )


def test_read_mapping_reports_file_and_json_errors(tmp_path) -> None:
    from pathlib import Path

    from governed_agent_runtime.serialization import read_mapping

    missing = Path(tmp_path) / "missing.json"
    with pytest.raises(ContractError, match="could not read"):
        read_mapping(missing)
    invalid = Path(tmp_path) / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ContractError, match="could not read"):
        read_mapping(invalid)


@pytest.mark.parametrize(
    "step",
    [
        "not-an-object",
        {
            "step_id": "s",
            "agent_id": "a",
            "action": "x",
            "capability": "c",
            "effect": 1,
        },
        {
            "step_id": "s",
            "agent_id": "a",
            "action": "x",
            "capability": "c",
            "requires_evidence_kinds": "bad",
        },
        {
            "step_id": "s",
            "agent_id": "a",
            "action": "x",
            "capability": "c",
            "requires_evidence_kinds": ["ok", 2],
        },
        {
            "step_id": "s",
            "agent_id": "a",
            "action": "x",
            "capability": "c",
            "input_data": [],
        },
    ],
)
def test_more_invalid_step_contracts(step: object) -> None:
    with pytest.raises(ContractError):
        workflow_from_mapping({"workflow_id": "w", "correlation_id": "c", "steps": [step]})


def test_empty_workflow_is_reported_as_contract_error() -> None:
    with pytest.raises(ContractError, match="at least one"):
        workflow_from_mapping({"workflow_id": "w", "correlation_id": "c", "steps": []})


@pytest.mark.parametrize(
    "patch",
    [
        {"single_use": "yes"},
        {"issued_at": "not-a-date"},
        {"capabilities": []},
    ],
)
def test_more_invalid_authority_contracts(patch: dict[str, object]) -> None:
    data: dict[str, object] = {
        "authorization_id": "a",
        "subject": "runtime",
        "workflow_id": "w",
        "capabilities": ["cap"],
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-01-02T00:00:00Z",
    }
    data.update(patch)
    with pytest.raises(ContractError):
        authority_from_mapping(data)
