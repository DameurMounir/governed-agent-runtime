"""CLI for persistent governed LangGraph walking slices and contract execution."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from governed_agent_runtime.builtin_agents import blocking_agent, echo_agent, failing_agent
from governed_agent_runtime.clock import FrozenClock
from governed_agent_runtime.decisions import Decision, EffectClass
from governed_agent_runtime.evidence import canonical_json
from governed_agent_runtime.execution_journal import JournalError
from governed_agent_runtime.langgraph_adapter import (
    GovernedGraphRun,
    GovernedLangGraphExecutor,
    LangGraphExecutionError,
    ResumeDecision,
)
from governed_agent_runtime.models import (
    AgentDescriptor,
    AuthorityGrant,
    EvidenceItem,
    StepSpec,
    WorkflowSpec,
)
from governed_agent_runtime.registry import AgentRegistry
from governed_agent_runtime.serialization import (
    ContractError,
    authority_from_mapping,
    read_mapping,
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


def _print_run(run: GovernedGraphRun) -> None:
    print(canonical_json(run.as_mapping()))


def _exit_code(run: GovernedGraphRun) -> int:
    if run.status == "INTERRUPTED":
        return EXIT_BY_DECISION[Decision.BLOCKED]
    if run.decision is None:
        return 2
    return EXIT_BY_DECISION[run.decision]


def _demo_contracts(scenario: str) -> tuple[WorkflowSpec, AuthorityGrant, FrozenClock]:
    instant = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    if scenario in {"pass", "blocked", "resume"}:
        agent_id = "builtin.echo"
        capability = "agent:echo"
    else:
        agent_id = "builtin.fail"
        capability = "agent:fail"
    required = ("human.approval",) if scenario in {"blocked", "resume"} else ()
    workflow = WorkflowSpec(
        workflow_id=f"langgraph-demo-{scenario}",
        correlation_id=f"langgraph-correlation-{scenario}",
        steps=(
            StepSpec(
                step_id="step-1",
                agent_id=agent_id,
                action=scenario,
                capability=capability,
                effect=EffectClass.READ_ONLY,
                requires_evidence_kinds=required,
                input_data={"scenario": scenario},
            ),
        ),
    )
    grant = AuthorityGrant(
        authorization_id=f"langgraph-authority-{scenario}",
        subject="governed-runtime",
        workflow_id=workflow.workflow_id,
        capabilities=frozenset({capability}),
        issued_at=instant - timedelta(minutes=1),
        expires_at=instant + timedelta(minutes=30),
    )
    return workflow, grant, FrozenClock(instant)


def _resume_for_run(
    run: GovernedGraphRun,
    *,
    approved: bool,
    evidence: tuple[EvidenceItem, ...] = (),
) -> ResumeDecision:
    if run.status != "INTERRUPTED" or len(run.interrupts) != 1:
        raise LangGraphExecutionError("run does not contain one active interruption")
    interrupt_payload = run.interrupts[0]
    workflow_id = interrupt_payload.get("workflow_id")
    step_id = interrupt_payload.get("step_id")
    if not isinstance(workflow_id, str) or not isinstance(step_id, str):
        raise LangGraphExecutionError("interrupt payload has no workflow or step identity")
    return ResumeDecision(
        thread_id=run.thread_id,
        workflow_id=workflow_id,
        step_id=step_id,
        approved=approved,
        evidence=evidence,
    )


def _demo(scenario: str) -> int:
    workflow, grant, clock = _demo_contracts(scenario)
    with tempfile.TemporaryDirectory(prefix="gar-langgraph-") as temporary:
        root = Path(temporary)
        with GovernedLangGraphExecutor(
            subject="governed-runtime",
            registry=_registry(),
            checkpoint_path=root / "checkpoints.sqlite",
            journal_path=root / "journal.sqlite",
            clock=clock,
        ) as executor:
            run = executor.start(workflow, grant, thread_id=f"thread-{scenario}")
            if scenario == "resume":
                run = executor.resume(
                    _resume_for_run(
                        run,
                        approved=True,
                        evidence=(EvidenceItem("human.approval", {"approved": True}),),
                    )
                )
            _print_run(run)
            return _exit_code(run)


def _required_text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return value.strip()


def _load_resume(path: Path) -> ResumeDecision:
    data = read_mapping(path)
    approved = data.get("approved")
    raw_evidence = data.get("evidence", [])
    if not isinstance(approved, bool):
        raise ContractError("approved must be a boolean")
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes, bytearray)):
        raise ContractError("evidence must be an array")
    evidence: list[EvidenceItem] = []
    for index, raw_item in enumerate(raw_evidence):
        if not isinstance(raw_item, dict):
            raise ContractError(f"evidence[{index}] must be an object")
        item = cast(Mapping[str, object], raw_item)
        kind = item.get("kind")
        payload = item.get("payload", {})
        if not isinstance(kind, str) or not kind.strip():
            raise ContractError(f"evidence[{index}].kind must be a non-empty string")
        if not isinstance(payload, dict):
            raise ContractError(f"evidence[{index}].payload must be an object")
        evidence.append(EvidenceItem(kind, cast(Mapping[str, object], payload)))
    try:
        return ResumeDecision(
            thread_id=_required_text(data, "thread_id"),
            workflow_id=_required_text(data, "workflow_id"),
            step_id=_required_text(data, "step_id"),
            approved=approved,
            evidence=tuple(evidence),
        )
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def _executor(args: argparse.Namespace) -> GovernedLangGraphExecutor:
    return GovernedLangGraphExecutor(
        subject=args.subject,
        registry=_registry(),
        checkpoint_path=args.checkpoint_db,
        journal_path=args.journal_db,
    )


def _start(args: argparse.Namespace) -> int:
    workflow = workflow_from_mapping(read_mapping(args.workflow))
    grant = authority_from_mapping(read_mapping(args.authority))
    with _executor(args) as executor:
        run = executor.start(workflow, grant, thread_id=args.thread_id)
    _print_run(run)
    return _exit_code(run)


def _resume(args: argparse.Namespace) -> int:
    decision = _load_resume(args.resume_contract)
    if decision.thread_id != args.thread_id:
        raise ContractError("resume contract thread_id must match --thread-id")
    with _executor(args) as executor:
        run = executor.resume(decision)
    _print_run(run)
    return _exit_code(run)


def _inspect(args: argparse.Namespace) -> int:
    with _executor(args) as executor:
        run = executor.inspect(thread_id=args.thread_id)
    _print_run(run)
    return _exit_code(run)


def _add_persistence_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--checkpoint-db", type=Path, required=True)
    parser.add_argument("--journal-db", type=Path, required=True)
    parser.add_argument("--subject", default="governed-runtime")


def build_parser() -> argparse.ArgumentParser:
    """Build the LangGraph adapter CLI parser."""

    parser = argparse.ArgumentParser(
        prog="gar-langgraph",
        description="Persistent LangGraph orchestration behind governed execution authority",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    demo = subcommands.add_parser("demo", help="run a deterministic governed walking slice")
    demo.add_argument("scenario", choices=("pass", "blocked", "resume", "fail"))

    start = subcommands.add_parser("start", help="start one persistent graph thread")
    _add_persistence_arguments(start)
    start.add_argument("--workflow", type=Path, required=True)
    start.add_argument("--authority", type=Path, required=True)

    resume = subcommands.add_parser("resume", help="resume an interrupted graph thread")
    _add_persistence_arguments(resume)
    resume.add_argument("--resume-contract", type=Path, required=True)

    inspect = subcommands.add_parser("inspect", help="inspect a thread without executing it")
    _add_persistence_arguments(inspect)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the LangGraph adapter CLI."""

    args = build_parser().parse_args(argv)
    try:
        if args.command == "demo":
            return _demo(args.scenario)
        if args.command == "start":
            return _start(args)
        if args.command == "resume":
            return _resume(args)
        return _inspect(args)
    except ContractError as exc:
        print(f"contract error: {exc}", file=sys.stderr)
        return 2
    except (JournalError, LangGraphExecutionError, ValueError) as exc:
        print(f"governed execution error: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        print(f"storage error: {type(exc).__name__}", file=sys.stderr)
        return 2


def entrypoint() -> None:
    """Console-script wrapper."""

    raise SystemExit(main())
