"""Canonical evidence serialization and tamper-evident hash chaining."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class EvidenceChainError(ValueError):
    """Raised when evidence cannot be serialized or the chain is inconsistent."""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceChainError("datetime values must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def to_json_value(value: object) -> JsonValue:
    """Convert supported Python values into a deterministic JSON value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceChainError("non-finite floats are not valid evidence")
        return value
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidenceChainError("evidence mapping keys must be strings")
            result[key] = to_json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_json_value(item) for item in value]
    raise EvidenceChainError(f"unsupported evidence value type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialize a supported value with stable key order and separators."""

    return json.dumps(
        to_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One immutable link in an evidence chain."""

    sequence: int
    evidence_id: str
    producer: str
    kind: str
    payload: Mapping[str, object]
    created_at: datetime
    previous_digest: str | None
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def without_digest(self) -> EvidenceRecord:
        """Return a copy useful for negative tests and verification."""

        return replace(self, digest="")


class EvidenceLedger:
    """Append-only in-memory evidence ledger with SHA-256 chaining."""

    def __init__(self) -> None:
        self._records: list[EvidenceRecord] = []

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        """Return an immutable snapshot of records."""

        return tuple(self._records)

    @property
    def head_digest(self) -> str | None:
        """Return the current chain head."""

        if not self._records:
            return None
        return self._records[-1].digest

    @property
    def kinds(self) -> tuple[str, ...]:
        """Return evidence kinds in append order."""

        return tuple(record.kind for record in self._records)

    def contains_kind(self, kind: str) -> bool:
        """Return whether at least one record has the requested kind."""

        return any(record.kind == kind for record in self._records)

    def append(
        self,
        *,
        producer: str,
        kind: str,
        payload: Mapping[str, object],
        created_at: datetime,
    ) -> EvidenceRecord:
        """Append one canonical record and return it."""

        sequence = len(self._records) + 1
        evidence_id = f"ev-{sequence:06d}"
        previous_digest = self.head_digest
        material = {
            "sequence": sequence,
            "evidence_id": evidence_id,
            "producer": producer,
            "kind": kind,
            "payload": payload,
            "created_at": created_at,
            "previous_digest": previous_digest,
        }
        digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
        record = EvidenceRecord(
            sequence=sequence,
            evidence_id=evidence_id,
            producer=producer,
            kind=kind,
            payload=dict(payload),
            created_at=created_at,
            previous_digest=previous_digest,
            digest=digest,
        )
        self._records.append(record)
        return record

    def verify(self) -> None:
        """Raise when any sequence, link, or digest is inconsistent."""

        previous: str | None = None
        for expected_sequence, record in enumerate(self._records, start=1):
            if record.sequence != expected_sequence:
                raise EvidenceChainError("evidence sequence is not contiguous")
            if record.evidence_id != f"ev-{expected_sequence:06d}":
                raise EvidenceChainError("evidence_id does not match sequence")
            if record.previous_digest != previous:
                raise EvidenceChainError("previous_digest link mismatch")
            material = {
                "sequence": record.sequence,
                "evidence_id": record.evidence_id,
                "producer": record.producer,
                "kind": record.kind,
                "payload": record.payload,
                "created_at": record.created_at,
                "previous_digest": record.previous_digest,
            }
            expected_digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
            if record.digest != expected_digest:
                raise EvidenceChainError("evidence digest mismatch")
            previous = record.digest

    def export(self) -> list[dict[str, object]]:
        """Return JSON-compatible records for persistence or transport."""

        return [
            {
                "sequence": record.sequence,
                "evidence_id": record.evidence_id,
                "producer": record.producer,
                "kind": record.kind,
                "payload": to_json_value(record.payload),
                "created_at": _utc_text(record.created_at),
                "previous_digest": record.previous_digest,
                "digest": record.digest,
            }
            for record in self._records
        ]
