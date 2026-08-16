"""Dependency-aware console entry point for the optional LangGraph adapter."""

from __future__ import annotations

import sys


def entrypoint() -> None:
    """Run the optional CLI or explain how to install its dependencies."""

    try:
        from governed_agent_runtime.langgraph_cli import main
    except ModuleNotFoundError as exc:
        missing = exc.name or "optional dependency"
        if missing == "langgraph" or missing.startswith("langgraph."):
            print(
                "LangGraph support is not installed; install governed-agent-runtime[langgraph]",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        raise
    raise SystemExit(main())
