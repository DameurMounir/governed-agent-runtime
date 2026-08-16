"""Repository structure and hygiene checks that do not depend on GitHub."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    ".editorconfig",
    ".gitattributes",
    ".github/CODEOWNERS",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "docs/architecture.md",
    "docs/contracts/authority.schema.json",
    "docs/contracts/langgraph-resume.schema.json",
    "docs/contracts/workflow.schema.json",
    "docs/decisions/0001-evidence-bound-runtime.md",
    "docs/decisions/0002-langgraph-orchestrates-runtime-authorizes.md",
    "docs/langgraph-governed-execution.md",
    "docs/research/langgraph-security-baseline.md",
    "docs/roadmap.md",
    "docs/status/foundation-v0.1.0.md",
    "docs/status/langgraph-governed-execution.md",
    "docs/threat-model.md",
    "docs/verification.md",
    "examples/langgraph-authority.json",
    "examples/langgraph-resume.json",
    "examples/langgraph-workflow.json",
    "examples/pass-authority.json",
    "examples/pass-workflow.json",
    "pyproject.toml",
    "requirements-ci.txt",
    "src/governed_agent_runtime/execution_journal.py",
    "src/governed_agent_runtime/langgraph_adapter.py",
    "src/governed_agent_runtime/langgraph_cli.py",
    "src/governed_agent_runtime/langgraph_entrypoint.py",
    "src/governed_agent_runtime/py.typed",
    "src/governed_agent_runtime/runtime.py",
    "tests/test_execution_journal.py",
    "tests/test_langgraph_adapter.py",
    "tests/test_langgraph_cli.py",
    "tests/test_runtime.py",
)
README_MARKERS = (
    "# Governed Agent Runtime",
    "## Decision semantics",
    "## Architecture",
    "## Quick start",
    "## Core guarantees in v0.1.0",
    "## Repository map",
    "## Verification",
    "## Security and governance",
    "## Documentation index",
    "## Boundaries",
)
FORBIDDEN_PARTS = {
    ".coverage",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "dist-a",
    "dist-b",
    "htmlcov",
}
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
SCHEMAS = (
    "docs/contracts/workflow.schema.json",
    "docs/contracts/authority.schema.json",
    "docs/contracts/langgraph-resume.schema.json",
)
DIRECT_PINS = (
    "langgraph==1.2.10",
    "langgraph-checkpoint==4.2.0",
    "langgraph-checkpoint-sqlite==3.1.1",
)


def _validate_readme(problems: list[str]) -> None:
    readme_path = ROOT / "README.md"
    if not readme_path.is_file():
        return
    content = readme_path.read_text(encoding="utf-8")
    problems.extend(
        f"README section missing: {marker}" for marker in README_MARKERS if marker not in content
    )
    if "```mermaid" not in content:
        problems.append("README architecture diagram missing")
    problems.extend(
        f"README executable command missing: {command}"
        for command in (
            "gar demo pass",
            "gar demo blocked",
            "gar demo fail",
            "gar-langgraph demo pass",
            "gar-langgraph demo resume",
        )
        if command not in content
    )


def _validate_schemas(problems: list[str]) -> None:
    for relative in SCHEMAS:
        try:
            payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"invalid JSON schema {relative}: {exc}")
            continue
        if not isinstance(payload, dict) or payload.get("$schema") is None:
            problems.append(f"schema metadata missing: {relative}")


def _validate_langgraph_surface(problems: list[str]) -> None:
    try:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements-ci.txt").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        adapter = (ROOT / "src/governed_agent_runtime/langgraph_adapter.py").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        problems.append(f"could not read LangGraph governance surface: {exc}")
        return

    problems.extend(
        f"direct LangGraph pin missing or inconsistent: {pin}"
        for pin in DIRECT_PINS
        if pin not in pyproject or pin not in requirements
    )
    if 'gar-langgraph = "governed_agent_runtime.langgraph_entrypoint:entrypoint"' not in pyproject:
        problems.append("gar-langgraph console entry point missing")
    if 'LANGGRAPH_STRICT_MSGPACK: "true"' not in workflow:
        problems.append("CI strict msgpack setting missing")
    problems.extend(
        f"LangGraph serializer implementation marker missing: {marker}"
        for marker in (
            "pickle_fallback=False",
            "allowed_json_modules=None",
            "allowed_msgpack_modules=None",
        )
        if marker not in adapter
    )


def main() -> int:
    """Validate professional repository structure and text-file hygiene."""

    problems = [
        f"missing required file: {relative}"
        for relative in REQUIRED
        if not (ROOT / relative).is_file()
    ]

    _validate_readme(problems)
    _validate_schemas(problems)
    _validate_langgraph_surface(problems)

    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            problems.append(f"forbidden generated path: {relative}")
            continue
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\r\n" in data:
            problems.append(f"CRLF line ending: {relative}")
        if data and not data.endswith(b"\n"):
            problems.append(f"missing final newline: {relative}")
        for number, line in enumerate(data.splitlines(), start=1):
            if line.rstrip(b" \t") != line:
                problems.append(f"trailing whitespace: {relative}:{number}")

    if problems:
        print("Repository validation failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("Repository validation PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
