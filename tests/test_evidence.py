from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from governed_agent_runtime.evidence import (
    EvidenceChainError,
    EvidenceLedger,
    canonical_json,
    to_json_value,
)


def test_canonical_json_is_order_independent() -> None:
    first = canonical_json({"b": 2, "a": [1, True, None]})
    second = canonical_json({"a": [1, True, None], "b": 2})
    assert first == second == '{"a":[1,true,null],"b":2}'


def test_json_conversion_supports_aware_datetime() -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    assert to_json_value(instant) == "2026-08-15T12:00:00Z"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}, object()])
def test_json_conversion_rejects_unsafe_values(value: object) -> None:
    with pytest.raises(EvidenceChainError):
        canonical_json(value)


def test_ledger_appends_and_verifies(instant: datetime) -> None:
    ledger = EvidenceLedger()
    first = ledger.append(producer="runtime", kind="start", payload={}, created_at=instant)
    second = ledger.append(
        producer="agent", kind="result", payload={"ok": True}, created_at=instant
    )
    ledger.verify()
    assert first.evidence_id == "ev-000001"
    assert second.previous_digest == first.digest
    assert ledger.head_digest == second.digest
    assert ledger.kinds == ("start", "result")
    assert ledger.contains_kind("result")
    assert not ledger.contains_kind("missing")


def test_ledger_detects_tampering(instant: datetime) -> None:
    ledger = EvidenceLedger()
    record = ledger.append(producer="runtime", kind="start", payload={}, created_at=instant)
    ledger._records[0] = replace(record, digest="0" * 64)
    with pytest.raises(EvidenceChainError, match="digest"):
        ledger.verify()


def test_ledger_export_is_json_compatible(instant: datetime) -> None:
    ledger = EvidenceLedger()
    ledger.append(producer="runtime", kind="start", payload={"n": 1}, created_at=instant)
    exported = ledger.export()
    assert exported[0]["created_at"] == "2026-08-15T12:00:00Z"
    assert exported[0]["previous_digest"] is None


def test_json_conversion_supports_finite_float_and_enum() -> None:
    from governed_agent_runtime.decisions import Decision

    assert to_json_value(1.5) == 1.5
    assert to_json_value(Decision.PASS) == "PASS"


def test_json_conversion_rejects_naive_datetime() -> None:
    with pytest.raises(EvidenceChainError, match="timezone-aware"):
        canonical_json(datetime(2026, 8, 15, 12, 0))  # noqa: DTZ001


def test_record_without_digest_and_records_snapshot(instant: datetime) -> None:
    ledger = EvidenceLedger()
    record = ledger.append(producer="runtime", kind="start", payload={}, created_at=instant)
    assert ledger.records == (record,)
    assert record.without_digest().digest == ""


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sequence": 2}, "sequence"),
        ({"evidence_id": "ev-wrong"}, "evidence_id"),
        ({"previous_digest": "wrong"}, "previous_digest"),
    ],
)
def test_ledger_detects_link_metadata_tampering(
    instant: datetime, changes: dict[str, object], message: str
) -> None:
    ledger = EvidenceLedger()
    record = ledger.append(producer="runtime", kind="start", payload={}, created_at=instant)
    ledger._records[0] = replace(record, **changes)  # type: ignore[arg-type]
    with pytest.raises(EvidenceChainError, match=message):
        ledger.verify()
