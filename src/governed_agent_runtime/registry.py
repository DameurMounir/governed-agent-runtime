"""Agent registration with explicit descriptors and no dynamic imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from governed_agent_runtime.models import AgentDescriptor, AgentOutcome, AgentRequest


class AgentHandler(Protocol):
    """Callable contract implemented by an agent adapter."""

    def __call__(self, request: AgentRequest, /) -> AgentOutcome:
        """Execute one already-authorized request."""


@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    """Descriptor and handler stored together."""

    descriptor: AgentDescriptor
    handler: AgentHandler


class AgentRegistry:
    """In-process registry that rejects duplicate identities."""

    def __init__(self) -> None:
        self._agents: dict[str, RegisteredAgent] = {}

    def register(self, descriptor: AgentDescriptor, handler: AgentHandler) -> None:
        """Register exactly one handler for an agent identity."""

        if descriptor.agent_id in self._agents:
            raise ValueError(f"agent already registered: {descriptor.agent_id}")
        self._agents[descriptor.agent_id] = RegisteredAgent(descriptor, handler)

    def resolve(self, agent_id: str) -> RegisteredAgent | None:
        """Resolve an agent without triggering imports or external lookup."""

        return self._agents.get(agent_id)

    @property
    def agent_ids(self) -> tuple[str, ...]:
        """Return registered IDs in deterministic order."""

        return tuple(sorted(self._agents))
