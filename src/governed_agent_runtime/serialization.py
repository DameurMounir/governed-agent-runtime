"""Strict JSON boundary helpers for workflow and authority contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from governed_agent_runtime.decisions import EffectClass
from governed_agent_runtime.evidence import canonical_json, to_json_value
from governed_agent_runtime.models import (
    AuthorityGrant,
    StepResult,
    StepSpec,
    WorkflowResult,
    WorkflowSpec,
)


class ContractError(ValueError):
    """Raised when external JSON does not match a runtime contract."""


def read_mapping(path: Path) -> Mapping[str, object]:
    """Read a JSON object from disk."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"could not read JSON object from {path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"expected a JSON object in {path}")
    return cast(Mapping[str, object], value)


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return value


def _string_sequence(mapping: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = mapping.get(key, [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractError(f"{key} must be an array of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(f"{key} must contain only non-empty strings")
        result.append(item)
    return tuple(result)


def _mapping(mapping: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = mapping.get(key, {})
    if not isinstance(value, dict):
        raise ContractError(f"{key} must be a JSON object")
    return cast(Mapping[str, object], value)


def _datetime(mapping: Mapping[str, object], key: str) -> datetime:
    raw = _text(mapping, key)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(f"{key} must be an ISO-8601 datetime") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{key} must include a timezone")
    return value


def workflow_from_mapping(data: Mapping[str, object]) -> WorkflowSpec:
    """Build a validated workflow contract."""

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes, bytearray)):
        raise ContractError("steps must be an array")
    steps: list[StepSpec] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise ContractError(f"steps[{index}] must be an object")
        step = cast(Mapping[str, object], raw_step)
        effect_raw = step.get("effect", EffectClass.READ_ONLY.value)
        if not isinstance(effect_raw, str):
            raise ContractError(f"steps[{index}].effect must be a string")
        try:
            effect = EffectClass(effect_raw)
        except ValueError as exc:
            raise ContractError(f"steps[{index}].effect is not supported") from exc
        steps.append(
            StepSpec(
                step_id=_text(step, "step_id"),
                agent_id=_text(step, "agent_id"),
                action=_text(step, "action"),
                capability=_text(step, "capability"),
                effect=effect,
                requires_evidence_kinds=_string_sequence(step, "requires_evidence_kinds"),
                input_data=_mapping(step, "input_data"),
            )
        )
    try:
        return WorkflowSpec(
            workflow_id=_text(data, "workflow_id"),
            correlation_id=_text(data, "correlation_id"),
            steps=tuple(steps),
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def authority_from_mapping(data: Mapping[str, object]) -> AuthorityGrant:
    """Build a validated authority grant."""

    single_use_raw = data.get("single_use", True)
    if not isinstance(single_use_raw, bool):
        raise ContractError("single_use must be a boolean")
    try:
        return AuthorityGrant(
            authorization_id=_text(data, "authorization_id"),
            subject=_text(data, "subject"),
            workflow_id=_text(data, "workflow_id"),
            capabilities=frozenset(_string_sequence(data, "capabilities")),
            issued_at=_datetime(data, "issued_at"),
            expires_at=_datetime(data, "expires_at"),
            single_use=single_use_raw,
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def step_result_to_mapping(result: StepResult) -> dict[str, object]:
    """Convert a step result to JSON-compatible data."""

    return {
        "step_id": result.step_id,
        "decision": result.decision.value,
        "summary": result.summary,
        "evidence_ids": list(result.evidence_ids),
        "output": to_json_value(result.output),
    }


def result_to_mapping(result: WorkflowResult) -> dict[str, object]:
    """Convert a terminal workflow result to JSON-compatible data."""

    return {
        "workflow_id": result.workflow_id,
        "correlation_id": result.correlation_id,
        "decision": result.decision.value,
        "summary": result.summary,
        "authority_consumed": result.authority_consumed,
        "evidence_head": result.evidence_head,
        "steps": [step_result_to_mapping(step) for step in result.steps],
    }


def result_to_json(result: WorkflowResult) -> str:
    """Return canonical JSON for a terminal result."""

    return canonical_json(result_to_mapping(result))
