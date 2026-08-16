from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

import governed_agent_runtime.langgraph_cli as cli_module
import governed_agent_runtime.langgraph_entrypoint as entrypoint_module
from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.langgraph_adapter import GovernedGraphRun, LangGraphExecutionError
from governed_agent_runtime.langgraph_cli import main


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("pass", 0), ("blocked", 3), ("resume", 0), ("fail", 4)],
)
def test_demo_exit_codes_and_json_output(
    scenario: str,
    expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", scenario]) == expected
    payload = json.loads(capsys.readouterr().out)
    assert payload["thread_id"] == f"thread-{scenario}"
    if scenario == "blocked":
        assert payload["status"] == "INTERRUPTED"
        assert payload["decision"] == "BLOCKED"
    else:
        assert payload["status"] == "TERMINAL"


def test_persistent_start_resume_and_inspect_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow = tmp_path / "workflow.json"
    authority = tmp_path / "authority.json"
    resume = tmp_path / "resume.json"
    checkpoint = tmp_path / "checkpoints.sqlite"
    journal = tmp_path / "journal.sqlite"
    workflow.write_text(
        json.dumps(
            {
                "workflow_id": "cli-workflow",
                "correlation_id": "cli-correlation",
                "steps": [
                    {
                        "step_id": "step-1",
                        "agent_id": "builtin.echo",
                        "action": "echo",
                        "capability": "agent:echo",
                        "effect": "READ_ONLY",
                        "requires_evidence_kinds": ["human.approval"],
                        "input_data": {"message": "hello"},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    authority.write_text(
        json.dumps(
            {
                "authorization_id": "cli-authority",
                "subject": "governed-runtime",
                "workflow_id": "cli-workflow",
                "capabilities": ["agent:echo"],
                "issued_at": "2026-01-01T00:00:00Z",
                "expires_at": "2030-01-01T00:00:00Z",
                "single_use": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    resume.write_text(
        json.dumps(
            {
                "thread_id": "cli-thread",
                "workflow_id": "cli-workflow",
                "step_id": "step-1",
                "approved": True,
                "evidence": [{"kind": "human.approval", "payload": {"approved": True}}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    common = [
        "--thread-id",
        "cli-thread",
        "--checkpoint-db",
        str(checkpoint),
        "--journal-db",
        str(journal),
    ]

    assert main(["start", *common, "--workflow", str(workflow), "--authority", str(authority)]) == 3
    started = json.loads(capsys.readouterr().out)
    assert started["status"] == "INTERRUPTED"

    assert main(["resume", *common, "--resume-contract", str(resume)]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["decision"] == "PASS"

    assert main(["inspect", *common]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["evidence_head"] == resumed["evidence_head"]


def test_cli_reports_contract_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]\n", encoding="utf-8")
    result = main(
        [
            "start",
            "--thread-id",
            "thread",
            "--checkpoint-db",
            str(tmp_path / "checkpoints.sqlite"),
            "--journal-db",
            str(tmp_path / "journal.sqlite"),
            "--workflow",
            str(invalid),
            "--authority",
            str(invalid),
        ]
    )

    assert result == 2
    assert "contract error" in capsys.readouterr().err


def test_cli_rejects_resume_contract_for_another_thread(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contract = tmp_path / "resume.json"
    contract.write_text(
        json.dumps(
            {
                "thread_id": "other-thread",
                "workflow_id": "workflow",
                "step_id": "step",
                "approved": False,
                "evidence": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = main(
        [
            "resume",
            "--thread-id",
            "requested-thread",
            "--checkpoint-db",
            str(tmp_path / "checkpoints.sqlite"),
            "--journal-db",
            str(tmp_path / "journal.sqlite"),
            "--resume-contract",
            str(contract),
        ]
    )

    assert result == 2
    assert "thread_id" in capsys.readouterr().err


def _running_run(*, interrupts: tuple[dict[str, object], ...] = ()) -> GovernedGraphRun:
    return GovernedGraphRun(
        thread_id="thread-1",
        status="INTERRUPTED" if interrupts else "RUNNING",
        decision=Decision.BLOCKED if interrupts else None,
        summary="running",
        current_step=0,
        authority_consumed=False,
        evidence_head=None,
        steps=(),
        interrupts=interrupts,
    )


def test_cli_helpers_validate_run_and_interrupt_identity() -> None:
    assert cli_module._exit_code(_running_run()) == 2
    with pytest.raises(LangGraphExecutionError, match="one active interruption"):
        cli_module._resume_for_run(_running_run(), approved=False)

    malformed = _running_run(interrupts=({"workflow_id": 1, "step_id": None},))
    with pytest.raises(LangGraphExecutionError, match="no workflow or step identity"):
        cli_module._resume_for_run(malformed, approved=False)

    with pytest.raises(cli_module.ContractError, match="non-empty string"):
        cli_module._required_text({}, "thread_id")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": "yes",
            },
            "approved",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": "invalid",
            },
            "evidence must be an array",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": [[]],
            },
            "must be an object",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": [{"kind": ""}],
            },
            "kind must be a non-empty string",
        ),
        (
            {
                "thread_id": "thread-1",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": [{"kind": "human.approval", "payload": []}],
            },
            "payload must be an object",
        ),
        (
            {
                "thread_id": " ",
                "workflow_id": "workflow-1",
                "step_id": "step-1",
                "approved": True,
                "evidence": [],
            },
            "thread_id",
        ),
    ],
)
def test_resume_contract_parser_rejects_malformed_data(
    tmp_path: Path, payload: dict[str, object], message: str
) -> None:
    path = tmp_path / "resume.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(cli_module.ContractError, match=message):
        cli_module._load_resume(path)


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (LangGraphExecutionError("blocked"), "governed execution error"),
        (ValueError("invalid"), "governed execution error"),
        (OSError("storage"), "storage error"),
    ],
)
def test_cli_main_sanitizes_expected_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exception: Exception,
    message: str,
) -> None:
    def fail(_scenario: str) -> int:
        raise exception

    monkeypatch.setattr(cli_module, "_demo", fail)
    assert cli_module.main(["demo", "pass"]) == 2
    assert message in capsys.readouterr().err


def test_cli_console_entrypoint_returns_main_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "main", lambda: 7)
    with pytest.raises(SystemExit, match="7"):
        cli_module.entrypoint()


def test_optional_entrypoint_reports_missing_langgraph(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original_import = builtins.__import__

    def missing_langgraph(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "governed_agent_runtime.langgraph_cli":
            error = ModuleNotFoundError("No module named 'langgraph'")
            error.name = "langgraph"
            raise error
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_langgraph)
    with pytest.raises(SystemExit, match="2"):
        entrypoint_module.entrypoint()
    assert "LangGraph support is not installed" in capsys.readouterr().err


def test_optional_entrypoint_reraises_unrelated_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def missing_other(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "governed_agent_runtime.langgraph_cli":
            error = ModuleNotFoundError("No module named 'other_dependency'")
            error.name = "other_dependency"
            raise error
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_other)
    with pytest.raises(ModuleNotFoundError, match="other_dependency"):
        entrypoint_module.entrypoint()


def test_optional_entrypoint_propagates_cli_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("governed_agent_runtime.langgraph_cli")

    def fake_main() -> int:
        return 9

    fake_module.main = fake_main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "governed_agent_runtime.langgraph_cli", fake_module)
    with pytest.raises(SystemExit, match="9"):
        entrypoint_module.entrypoint()
