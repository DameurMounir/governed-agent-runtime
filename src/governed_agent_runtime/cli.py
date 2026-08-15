"""Command-line entry point for validation and executable walking slices."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from governed_agent_runtime.builtin_agents import blocking_agent, echo_agent, failing_agent
from governed_agent_runtime.clock import FrozenClock
from governed_agent_runtime.decisions import Decision, EffectClass
from governed_agent_runtime.evidence import EvidenceLedger
from governed_agent_runtime.models import AgentDescriptor, AuthorityGrant, StepSpec, WorkflowSpec
from governed_agent_runtime.registry import AgentRegistry
from governed_agent_runtime.runtime import GovernedRuntime
from governed_agent_runtime.serialization import (
    ContractError,
    authority_from_mapping,
    read_mapping,
    result_to_json,
    workflow_from_mapping,
)

EXIT_BY_DECISION = {Decision.PASS: 0, Decision.BLOCKED: 3, Decision.FAIL: 4}


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentDescriptor("builtin.echo", "1.0.0", frozenset({"agent:echo"})), echo_agent
    )
    registry.register(
        AgentDescriptor("builtin.block", "1.0.0", frozenset({"agent:block"})),
        blocking_agent,
    )
    registry.register(
        AgentDescriptor("builtin.fail", "1.0.0", frozenset({"agent:fail"})), failing_agent
    )
    return registry


def _write_evidence(path: Path | None, ledger: EvidenceLedger) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger.export(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_contracts(workflow_path: Path, authority_path: Path, evidence_out: Path | None) -> int:
    workflow = workflow_from_mapping(read_mapping(workflow_path))
    authority = authority_from_mapping(read_mapping(authority_path))
    ledger = EvidenceLedger()
    runtime = GovernedRuntime(subject="governed-runtime", registry=_registry())
    result = runtime.execute(workflow, authority, evidence=ledger)
    print(result_to_json(result))
    _write_evidence(evidence_out, ledger)
    return EXIT_BY_DECISION[result.decision]


def _demo(scenario: str, evidence_out: Path | None) -> int:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    if scenario == "pass":
        agent_id = "builtin.echo"
        capability = "agent:echo"
    elif scenario == "blocked":
        agent_id = "builtin.block"
        capability = "agent:block"
    else:
        agent_id = "builtin.fail"
        capability = "agent:fail"

    workflow = WorkflowSpec(
        workflow_id=f"demo-{scenario}",
        correlation_id=f"corr-{scenario}",
        steps=(
            StepSpec(
                step_id="step-1",
                agent_id=agent_id,
                action=scenario,
                capability=capability,
                effect=EffectClass.READ_ONLY,
                input_data={"scenario": scenario},
            ),
        ),
    )
    authority = AuthorityGrant(
        authorization_id=f"auth-demo-{scenario}",
        subject="governed-runtime",
        workflow_id=workflow.workflow_id,
        capabilities=frozenset({capability}),
        issued_at=instant - timedelta(minutes=1),
        expires_at=instant + timedelta(minutes=10),
    )
    ledger = EvidenceLedger()
    runtime = GovernedRuntime(
        subject="governed-runtime",
        registry=_registry(),
        clock=FrozenClock(instant),
    )
    result = runtime.execute(workflow, authority, evidence=ledger)
    print(result_to_json(result))
    _write_evidence(evidence_out, ledger)
    return EXIT_BY_DECISION[result.decision]


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        prog="gar",
        description="Governed orchestration runtime for evidence-bound workflows",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    demo = subcommands.add_parser("demo", help="run a deterministic walking slice")
    demo.add_argument("scenario", choices=("pass", "blocked", "fail"))
    demo.add_argument("--evidence-out", type=Path)

    run = subcommands.add_parser("run", help="run workflow and authority JSON contracts")
    run.add_argument("--workflow", type=Path, required=True)
    run.add_argument("--authority", type=Path, required=True)
    run.add_argument("--evidence-out", type=Path)

    subcommands.add_parser("semantics", help="explain PASS, BLOCKED, and FAIL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the command-line interface."""

    args = build_parser().parse_args(argv)
    try:
        if args.command == "demo":
            return _demo(args.scenario, args.evidence_out)
        if args.command == "run":
            return _run_contracts(args.workflow, args.authority, args.evidence_out)
        print(
            "PASS means all authorized steps completed; BLOCKED means execution stopped "
            "safely because a prerequisite or business decision was not satisfied; FAIL "
            "means an attempted execution or contract failed."
        )
        return 0
    except ContractError as exc:
        print(f"contract error: {exc}", file=sys.stderr)
        return 2


def entrypoint() -> None:
    """Console-script wrapper."""

    raise SystemExit(main())
