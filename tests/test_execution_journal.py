from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import governed_agent_runtime.execution_journal as journal_module
from governed_agent_runtime.execution_journal import (
    JournalError,
    SqliteExecutionJournal,
    StepClaimState,
    authority_digest,
)
from governed_agent_runtime.models import AuthorityGrant


def _grant(*, authorization_id: str = "authority-1") -> AuthorityGrant:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    return AuthorityGrant(
        authorization_id=authorization_id,
        subject="runtime",
        workflow_id="workflow-1",
        capabilities=frozenset({"agent:echo"}),
        issued_at=instant - timedelta(minutes=1),
        expires_at=instant + timedelta(minutes=30),
    )


def test_authority_claim_is_durable_and_same_thread_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with SqliteExecutionJournal(path) as journal:
        first = journal.claim_authority(grant=_grant(), thread_id="thread-1", consumed_at=instant)
        second = journal.claim_authority(grant=_grant(), thread_id="thread-1", consumed_at=instant)

    with SqliteExecutionJournal(path) as reopened:
        third = reopened.claim_authority(grant=_grant(), thread_id="thread-1", consumed_at=instant)
        assert reopened.authority_owner("authority-1") == "thread-1"

    assert first.allowed and first.consumed
    assert second.allowed and second.consumed
    assert third.allowed and third.consumed
    assert path.stat().st_mode & 0o077 == 0


def test_single_use_authority_cannot_move_to_another_thread(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        assert journal.claim_authority(
            grant=_grant(), thread_id="thread-1", consumed_at=instant
        ).allowed
        blocked = journal.claim_authority(grant=_grant(), thread_id="thread-2", consumed_at=instant)

    assert not blocked.allowed
    assert not blocked.consumed


def test_authorization_id_cannot_be_reused_with_mutated_content(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    grant = _grant()
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        journal.claim_authority(grant=grant, thread_id="thread-1", consumed_at=instant)
        with pytest.raises(JournalError, match="different grant content"):
            journal.claim_authority(
                grant=replace(grant, capabilities=frozenset({"agent:other"})),
                thread_id="thread-1",
                consumed_at=instant,
            )


def test_multi_use_authority_is_not_recorded_as_consumed(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    grant = replace(_grant(), single_use=False)
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        claim = journal.claim_authority(grant=grant, thread_id="thread-1", consumed_at=instant)
        assert journal.authority_owner(grant.authorization_id) is None

    assert claim.allowed
    assert not claim.consumed


def test_step_claim_completion_and_replay_are_idempotent(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    outcome = {
        "decision": "PASS",
        "summary": "completed",
        "output": {"value": 1},
        "evidence": [],
    }
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        claim = journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="a" * 64,
            started_at=instant,
        )
        assert claim.state is StepClaimState.NEW
        journal.finish_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="a" * 64,
            outcome=outcome,
            failed=False,
            finished_at=instant,
        )
        replay = journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="a" * 64,
            started_at=instant,
        )
        journal.finish_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="a" * 64,
            outcome=outcome,
            failed=False,
            finished_at=instant,
        )

    assert replay.state is StepClaimState.COMPLETED
    assert replay.outcome == outcome


def test_unfinished_step_is_in_doubt_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with SqliteExecutionJournal(path) as journal:
        journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="b" * 64,
            started_at=instant,
        )
    with SqliteExecutionJournal(path) as reopened:
        claim = reopened.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="b" * 64,
            started_at=instant,
        )

    assert claim.state is StepClaimState.IN_DOUBT
    assert claim.outcome is None


def test_failed_step_replays_controlled_failure(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    outcome = {
        "decision": "FAIL",
        "summary": "controlled failure",
        "output": {},
        "evidence": [],
    }
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="c" * 64,
            started_at=instant,
        )
        journal.finish_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="c" * 64,
            outcome=outcome,
            failed=True,
            finished_at=instant,
        )
        replay = journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="c" * 64,
            started_at=instant,
        )

    assert replay.state is StepClaimState.FAILED
    assert replay.outcome == outcome


def test_journal_rejects_request_mutation_and_terminal_rewrite(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    path = tmp_path / "journal.sqlite"
    with SqliteExecutionJournal(path) as journal:
        journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="d" * 64,
            started_at=instant,
        )
        with pytest.raises(JournalError, match="different request content"):
            journal.claim_step(
                thread_id="thread-1",
                workflow_id="workflow-1",
                step_id="step-1",
                request_digest="e" * 64,
                started_at=instant,
            )
        outcome = {"decision": "PASS", "summary": "ok", "output": {}, "evidence": []}
        journal.finish_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="d" * 64,
            outcome=outcome,
            failed=False,
            finished_at=instant,
        )
        with pytest.raises(JournalError, match="cannot be changed"):
            journal.finish_step(
                thread_id="thread-1",
                workflow_id="workflow-1",
                step_id="step-1",
                request_digest="d" * 64,
                outcome={**outcome, "summary": "changed"},
                failed=False,
                finished_at=instant,
            )


def test_finish_requires_existing_matching_claim(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with (
        SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal,
        pytest.raises(JournalError, match="unclaimed"),
    ):
        journal.finish_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="f" * 64,
            outcome={},
            failed=False,
            finished_at=instant,
        )


def test_journal_validates_inputs_and_authority_digest_is_stable(tmp_path: Path) -> None:
    grant = _grant()
    assert authority_digest(grant) == authority_digest(grant)
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        with pytest.raises(ValueError, match="thread_id"):
            journal.claim_authority(
                grant=grant,
                thread_id=" ",
                consumed_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            )
        with pytest.raises(ValueError, match="identifiers"):
            journal.claim_step(
                thread_id="",
                workflow_id="workflow-1",
                step_id="step-1",
                request_digest="g" * 64,
                started_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            )


def test_private_sqlite_path_rejects_public_parent_and_database(tmp_path: Path) -> None:
    public_parent = tmp_path / "public"
    public_parent.mkdir(mode=0o755)
    public_parent.chmod(0o755)
    with pytest.raises(JournalError, match="parent directory"):
        SqliteExecutionJournal(public_parent / "journal.sqlite")

    private_parent = tmp_path / "private"
    private_parent.mkdir(mode=0o700)
    database = private_parent / "journal.sqlite"
    database.touch(mode=0o600)
    database.chmod(0o644)
    with pytest.raises(JournalError, match="database file"):
        SqliteExecutionJournal(database)


def test_private_sqlite_path_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite"
    target.touch(mode=0o600)
    link = tmp_path / "link.sqlite"
    link.symlink_to(target)

    with pytest.raises(JournalError, match="symbolic link"):
        SqliteExecutionJournal(link)


def test_journal_rejects_naive_timestamps(tmp_path: Path) -> None:
    naive = datetime(2026, 8, 15, 12, 0)  # noqa: DTZ001 - intentional invalid input
    with (
        SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal,
        pytest.raises(ValueError, match="timezone-aware"),
    ):
        journal.claim_authority(
            grant=_grant(),
            thread_id="thread-1",
            consumed_at=naive,
        )


def test_private_sqlite_path_rejects_missing_filename() -> None:
    with pytest.raises(JournalError, match="file name"):
        journal_module.ensure_private_sqlite_path(Path())


def test_private_sqlite_path_rejects_non_directory_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_parent = tmp_path / "private"
    monkeypatch.setattr(Path, "resolve", lambda self, *, strict=False: self)
    monkeypatch.setattr(journal_module.stat, "S_ISDIR", lambda _mode: False)

    with pytest.raises(JournalError, match="parent path"):
        journal_module.ensure_private_sqlite_path(private_parent / "journal.sqlite")


def test_private_sqlite_path_rechecks_symlink_after_parent_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_parent = tmp_path / "private"
    private_parent.mkdir(mode=0o700)
    private_parent.chmod(0o700)
    calls = 0
    original = Path.is_symlink

    def changing_link_state(path: Path) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            return True
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", changing_link_state)
    with pytest.raises(JournalError, match="symbolic link"):
        journal_module.ensure_private_sqlite_path(private_parent / "journal.sqlite")


def test_private_sqlite_path_rejects_existing_non_regular_path(tmp_path: Path) -> None:
    private_parent = tmp_path / "private"
    private_parent.mkdir(mode=0o700)
    private_parent.chmod(0o700)
    database = private_parent / "journal.sqlite"
    database.mkdir(mode=0o700)

    with pytest.raises(JournalError, match="regular file"):
        journal_module.ensure_private_sqlite_path(database)


def test_private_sqlite_path_fails_closed_on_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_parent = tmp_path / "private"
    private_parent.mkdir(mode=0o700)
    private_parent.chmod(0o700)

    def collided_open(_path: Path, _flags: int, _mode: int) -> int:
        raise FileExistsError

    monkeypatch.setattr(journal_module.os, "open", collided_open)
    with pytest.raises(JournalError, match="changed during creation"):
        journal_module.ensure_private_sqlite_path(private_parent / "journal.sqlite")


def test_journal_closes_connection_when_wal_cannot_be_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    class FakeConnection:
        row_factory: object | None = None

        def execute(self, statement: str) -> SimpleNamespace:
            if statement == "PRAGMA journal_mode = WAL":
                return SimpleNamespace(fetchone=lambda: ("delete",))
            return SimpleNamespace(fetchone=lambda: None)

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        journal_module.sqlite3, "connect", lambda *_args, **_kwargs: FakeConnection()
    )

    with pytest.raises(JournalError, match="WAL mode"):
        SqliteExecutionJournal(tmp_path / "journal.sqlite")
    assert closed


@pytest.mark.parametrize(
    ("stored_value", "message"),
    [
        (None, "no outcome"),
        ("not-json", "not valid JSON"),
        ("[]", "JSON object"),
    ],
)
def test_terminal_step_replay_rejects_corrupt_stored_outcome(
    tmp_path: Path, stored_value: str | None, message: str
) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="a" * 64,
            started_at=instant,
        )
        journal._connection.execute(
            """
            UPDATE step_executions
            SET status = 'COMPLETED', outcome_json = ?
            WHERE thread_id = ? AND workflow_id = ? AND step_id = ?
            """,
            (stored_value, "thread-1", "workflow-1", "step-1"),
        )
        with pytest.raises(JournalError, match=message):
            journal.claim_step(
                thread_id="thread-1",
                workflow_id="workflow-1",
                step_id="step-1",
                request_digest="a" * 64,
                started_at=instant,
            )


def test_finish_step_rejects_mismatched_request_digest(tmp_path: Path) -> None:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with SqliteExecutionJournal(tmp_path / "journal.sqlite") as journal:
        journal.claim_step(
            thread_id="thread-1",
            workflow_id="workflow-1",
            step_id="step-1",
            request_digest="a" * 64,
            started_at=instant,
        )
        with pytest.raises(JournalError, match="different request digest"):
            journal.finish_step(
                thread_id="thread-1",
                workflow_id="workflow-1",
                step_id="step-1",
                request_digest="b" * 64,
                outcome={},
                failed=False,
                finished_at=instant,
            )
