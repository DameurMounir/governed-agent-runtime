from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_agent_runtime.cli import main


@pytest.mark.parametrize(
    ("scenario", "expected_code", "expected_decision"),
    [("pass", 0, "PASS"), ("blocked", 3, "BLOCKED"), ("fail", 4, "FAIL")],
)
def test_demo_scenarios(
    scenario: str,
    expected_code: int,
    expected_decision: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["demo", scenario])
    payload = json.loads(capsys.readouterr().out)
    assert code == expected_code
    assert payload["decision"] == expected_decision


def test_demo_writes_evidence(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "evidence.json"
    assert main(["demo", "pass", "--evidence-out", str(output)]) == 0
    records = json.loads(output.read_text(encoding="utf-8"))
    assert records[0]["kind"] == "workflow.started"


def test_run_example_contracts(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "run",
            "--workflow",
            "examples/pass-workflow.json",
            "--authority",
            "examples/pass-authority.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["decision"] == "PASS"


def test_run_reports_contract_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]\n", encoding="utf-8")
    code = main(
        [
            "run",
            "--workflow",
            str(invalid),
            "--authority",
            "examples/pass-authority.json",
        ]
    )
    assert code == 2
    assert "contract error" in capsys.readouterr().err


def test_semantics(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["semantics"]) == 0
    output = capsys.readouterr().out
    assert "PASS" in output and "BLOCKED" in output and "FAIL" in output


def test_entrypoint_raises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from governed_agent_runtime.cli import entrypoint

    monkeypatch.setattr("sys.argv", ["gar", "semantics"])
    with pytest.raises(SystemExit) as raised:
        entrypoint()
    assert raised.value.code == 0
