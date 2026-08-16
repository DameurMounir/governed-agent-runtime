"""Durable SQLite journal for authority consumption and step execution claims.

The journal closes the replay gap between LangGraph checkpoints and external
side effects. A step is claimed before its handler runs. A completed claim can
be replayed without invoking the handler again; an unfinished claim is treated
as in-doubt and blocks automatic re-execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from governed_agent_runtime.evidence import canonical_json
from governed_agent_runtime.models import AuthorityGrant


class JournalError(RuntimeError):
    """Raised when durable journal state is missing, inconsistent, or tampered."""


class StepClaimState(StrEnum):
    """Durable status returned when a step execution is claimed."""

    NEW = "NEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    IN_DOUBT = "IN_DOUBT"


@dataclass(frozen=True, slots=True)
class AuthorityClaim:
    """Result of a durable authority-consumption attempt."""

    allowed: bool
    consumed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class StepClaim:
    """Result of a durable step claim, optionally carrying a stored outcome."""

    state: StepClaimState
    outcome: Mapping[str, object] | None = None


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("journal timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def authority_digest(grant: AuthorityGrant) -> str:
    """Return the canonical digest that binds a durable authority claim."""

    return _sha256(
        {
            "authorization_id": grant.authorization_id,
            "subject": grant.subject,
            "workflow_id": grant.workflow_id,
            "capabilities": sorted(grant.capabilities),
            "issued_at": grant.issued_at,
            "expires_at": grant.expires_at,
            "single_use": grant.single_use,
        }
    )


def ensure_private_sqlite_path(path: Path) -> Path:
    """Return a private regular-file path or fail before SQLite opens it.

    Checkpoint files are integrity-sensitive. The parent directory and existing
    database must not be group/world accessible, and symbolic links or special
    files are rejected. A new database is pre-created with mode ``0600`` to
    narrow the creation race before SQLite opens it.
    """

    candidate = path.expanduser()
    if not candidate.name or candidate.name in {".", ".."}:
        raise JournalError("SQLite database path must include a file name")
    if candidate.is_symlink():
        raise JournalError("SQLite database path must not be a symbolic link")

    parent = candidate.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = parent.resolve(strict=True)
    parent_stat = parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise JournalError("SQLite parent path must be a directory")
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise JournalError("SQLite parent directory must have private permissions")

    resolved = parent / candidate.name
    if resolved.is_symlink():
        raise JournalError("SQLite database path must not be a symbolic link")
    if resolved.exists():
        existing = resolved.stat()
        if not stat.S_ISREG(existing.st_mode):
            raise JournalError("SQLite database path must be a regular file")
        if stat.S_IMODE(existing.st_mode) & 0o077:
            raise JournalError("SQLite database file must have private permissions")
        return resolved

    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(resolved, flags, 0o600)
    except FileExistsError as exc:
        raise JournalError("SQLite database path changed during creation") from exc
    os.close(descriptor)
    resolved.chmod(0o600)
    return resolved


class SqliteExecutionJournal:
    """Fail-closed local journal for one lightweight runtime deployment.

    The implementation uses only parameterized SQL. It is suitable for the
    repository's local walking slices and small-project adapter evidence. It is
    not represented as a distributed authority service.
    """

    def __init__(self, path: Path) -> None:
        self._path = ensure_private_sqlite_path(path)
        self._connection = sqlite3.connect(
            self._path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._connection.execute("PRAGMA busy_timeout = 30000")
            mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                raise JournalError("SQLite journal could not enable WAL mode")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA trusted_schema = OFF")
            self._connection.execute("PRAGMA secure_delete = ON")
            self._setup()
        except Exception:
            self._connection.close()
            raise
        self._path.chmod(0o600)

    @property
    def path(self) -> Path:
        """Return the resolved journal path."""

        return self._path

    def close(self) -> None:
        """Close the SQLite connection."""

        with self._lock:
            self._connection.close()

    def __enter__(self) -> SqliteExecutionJournal:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _setup(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS authority_uses (
                    authorization_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    grant_digest TEXT NOT NULL,
                    consumed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS step_executions (
                    thread_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('STARTED', 'COMPLETED', 'FAILED')),
                    outcome_json TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    PRIMARY KEY (thread_id, workflow_id, step_id)
                );
                """
            )

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def claim_authority(
        self,
        *,
        grant: AuthorityGrant,
        thread_id: str,
        consumed_at: datetime,
    ) -> AuthorityClaim:
        """Consume a single-use grant durably or permit its same-thread resume."""

        if not thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if not grant.single_use:
            return AuthorityClaim(True, False, "multi-use authority accepted")

        digest = authority_digest(grant)
        with self._write_transaction():
            row = self._connection.execute(
                """
                SELECT thread_id, workflow_id, grant_digest
                FROM authority_uses
                WHERE authorization_id = ?
                """,
                (grant.authorization_id,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO authority_uses (
                        authorization_id, thread_id, workflow_id, grant_digest, consumed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        grant.authorization_id,
                        thread_id,
                        grant.workflow_id,
                        digest,
                        _utc_text(consumed_at),
                    ),
                )
                return AuthorityClaim(True, True, "single-use authority consumed")

            stored_thread = cast(str, row["thread_id"])
            stored_workflow = cast(str, row["workflow_id"])
            stored_digest = cast(str, row["grant_digest"])
            if stored_digest != digest:
                raise JournalError("authorization_id was reused with different grant content")
            if stored_thread == thread_id and stored_workflow == grant.workflow_id:
                return AuthorityClaim(True, True, "same-thread authority resume accepted")
            return AuthorityClaim(False, False, "single-use authority belongs to another thread")

    def claim_step(
        self,
        *,
        thread_id: str,
        workflow_id: str,
        step_id: str,
        request_digest: str,
        started_at: datetime,
    ) -> StepClaim:
        """Claim one execution or return its durable terminal/in-doubt state."""

        if not all(value.strip() for value in (thread_id, workflow_id, step_id, request_digest)):
            raise ValueError("step claim identifiers must not be empty")
        with self._write_transaction():
            row = self._connection.execute(
                """
                SELECT request_digest, status, outcome_json
                FROM step_executions
                WHERE thread_id = ? AND workflow_id = ? AND step_id = ?
                """,
                (thread_id, workflow_id, step_id),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO step_executions (
                        thread_id, workflow_id, step_id, request_digest,
                        status, outcome_json, started_at, finished_at
                    ) VALUES (?, ?, ?, ?, 'STARTED', NULL, ?, NULL)
                    """,
                    (thread_id, workflow_id, step_id, request_digest, _utc_text(started_at)),
                )
                return StepClaim(StepClaimState.NEW)

            stored_digest = cast(str, row["request_digest"])
            if stored_digest != request_digest:
                raise JournalError("step was replayed with different request content")
            status_value = cast(str, row["status"])
            if status_value == "STARTED":
                return StepClaim(StepClaimState.IN_DOUBT)
            raw_outcome = row["outcome_json"]
            if not isinstance(raw_outcome, str):
                raise JournalError("terminal step journal row has no outcome")
            try:
                parsed = json.loads(raw_outcome)
            except json.JSONDecodeError as exc:
                raise JournalError("terminal step outcome is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise JournalError("terminal step outcome must be a JSON object")
            outcome = cast(Mapping[str, object], parsed)
            claim_state = (
                StepClaimState.COMPLETED if status_value == "COMPLETED" else StepClaimState.FAILED
            )
            return StepClaim(claim_state, outcome)

    def finish_step(
        self,
        *,
        thread_id: str,
        workflow_id: str,
        step_id: str,
        request_digest: str,
        outcome: Mapping[str, object],
        failed: bool,
        finished_at: datetime,
    ) -> None:
        """Seal a claimed step with its canonical outcome."""

        outcome_json = canonical_json(outcome)
        terminal_status = "FAILED" if failed else "COMPLETED"
        with self._write_transaction():
            row = self._connection.execute(
                """
                SELECT request_digest, status, outcome_json
                FROM step_executions
                WHERE thread_id = ? AND workflow_id = ? AND step_id = ?
                """,
                (thread_id, workflow_id, step_id),
            ).fetchone()
            if row is None:
                raise JournalError("cannot finish an unclaimed step")
            if cast(str, row["request_digest"]) != request_digest:
                raise JournalError("cannot finish a step with a different request digest")

            stored_status = cast(str, row["status"])
            if stored_status == "STARTED":
                self._connection.execute(
                    """
                    UPDATE step_executions
                    SET status = ?, outcome_json = ?, finished_at = ?
                    WHERE thread_id = ? AND workflow_id = ? AND step_id = ?
                    """,
                    (
                        terminal_status,
                        outcome_json,
                        _utc_text(finished_at),
                        thread_id,
                        workflow_id,
                        step_id,
                    ),
                )
                return

            stored_outcome = row["outcome_json"]
            if stored_status != terminal_status or stored_outcome != outcome_json:
                raise JournalError("terminal step outcome cannot be changed")

    def authority_owner(self, authorization_id: str) -> str | None:
        """Return the owning thread for diagnostics and tests."""

        with self._lock:
            row = self._connection.execute(
                "SELECT thread_id FROM authority_uses WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
        return None if row is None else cast(str, row["thread_id"])
