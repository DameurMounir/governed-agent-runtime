from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from governed_agent_runtime.clock import FrozenClock
from governed_agent_runtime.models import AgentDescriptor, AuthorityGrant
from governed_agent_runtime.registry import AgentRegistry


@pytest.fixture
def instant() -> datetime:
    return datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock(instant: datetime) -> FrozenClock:
    return FrozenClock(instant)


@pytest.fixture
def grant(instant: datetime) -> AuthorityGrant:
    return AuthorityGrant(
        authorization_id="auth-1",
        subject="runtime",
        workflow_id="workflow-1",
        capabilities=frozenset({"agent:echo", "effect:reversible"}),
        issued_at=instant - timedelta(minutes=1),
        expires_at=instant + timedelta(minutes=10),
    )


@pytest.fixture
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture
def echo_descriptor() -> AgentDescriptor:
    return AgentDescriptor("echo", "1.0.0", frozenset({"agent:echo"}))
