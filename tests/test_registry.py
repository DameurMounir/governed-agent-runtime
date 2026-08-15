from __future__ import annotations

import pytest

from governed_agent_runtime.decisions import Decision
from governed_agent_runtime.models import AgentDescriptor, AgentOutcome, AgentRequest
from governed_agent_runtime.registry import AgentRegistry


def handler(request: AgentRequest) -> AgentOutcome:
    return AgentOutcome(Decision.PASS, request.action)


def test_register_and_resolve() -> None:
    registry = AgentRegistry()
    descriptor = AgentDescriptor("agent", "1", frozenset({"cap"}))
    registry.register(descriptor, handler)
    resolved = registry.resolve("agent")
    assert resolved is not None
    assert resolved.descriptor == descriptor
    assert registry.agent_ids == ("agent",)


def test_duplicate_registration_is_rejected() -> None:
    registry = AgentRegistry()
    descriptor = AgentDescriptor("agent", "1", frozenset({"cap"}))
    registry.register(descriptor, handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(descriptor, handler)


def test_unknown_agent_returns_none() -> None:
    assert AgentRegistry().resolve("missing") is None
