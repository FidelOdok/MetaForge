"""Unit tests for api_gateway.chat.agent_router."""

from __future__ import annotations

from uuid import uuid4

import pytest

from api_gateway.chat.agent_router import AgentRouter, default_router
from domain_agents.mechanical.agent import MechanicalAgent
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI


@pytest.fixture()
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture()
def mcp() -> InMemoryMcpBridge:
    return InMemoryMcpBridge()


class TestAgentRouter:
    """Tests for the AgentRouter class."""

    def test_session_scope_returns_mechanical_agent(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        agent = default_router.get_agent("session", twin=twin, mcp_bridge=mcp)
        assert agent is not None
        assert isinstance(agent, MechanicalAgent)

    def test_bom_entry_scope_returns_mechanical_agent(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        agent = default_router.get_agent("bom-entry", twin=twin, mcp_bridge=mcp)
        assert agent is not None
        assert isinstance(agent, MechanicalAgent)

    def test_digital_twin_node_scope_returns_mechanical_agent(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        agent = default_router.get_agent("digital-twin-node", twin=twin, mcp_bridge=mcp)
        assert agent is not None
        assert isinstance(agent, MechanicalAgent)

    def test_unknown_scope_returns_none(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        agent = default_router.get_agent("nonexistent-scope", twin=twin, mcp_bridge=mcp)
        assert agent is None

    def test_approval_scope_returns_none(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        agent = default_router.get_agent("approval", twin=twin, mcp_bridge=mcp)
        assert agent is None

    def test_project_scope_returns_none(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        agent = default_router.get_agent("project", twin=twin, mcp_bridge=mcp)
        assert agent is None

    def test_register_custom_agent_factory(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        """Registering a custom factory makes it available via get_agent."""
        router = AgentRouter()

        class FakeAgent:
            def __init__(self, twin: object, mcp: object) -> None:
                self.twin = twin
                self.mcp = mcp

        router.register("custom-scope", FakeAgent)
        agent = router.get_agent("custom-scope", twin=twin, mcp_bridge=mcp)
        assert agent is not None
        assert isinstance(agent, FakeAgent)

    def test_agent_has_correct_twin_and_mcp(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        """The returned agent should hold the injected twin and mcp_bridge."""
        agent = default_router.get_agent("session", twin=twin, mcp_bridge=mcp)
        assert agent is not None
        assert agent.twin is twin
        assert agent.mcp is mcp

    def test_agent_receives_session_id(self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge) -> None:
        """When session_id is provided it should be forwarded to the agent."""
        sid = uuid4()
        agent = default_router.get_agent("session", twin=twin, mcp_bridge=mcp, session_id=sid)
        assert agent is not None
        assert agent.session_id == sid

    def test_register_overwrites_existing(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge
    ) -> None:
        """Registering the same scope_kind twice replaces the factory."""
        router = AgentRouter()

        class AgentA:
            def __init__(self, twin: object, mcp: object) -> None:
                pass

        class AgentB:
            def __init__(self, twin: object, mcp: object) -> None:
                pass

        router.register("scope", AgentA)
        router.register("scope", AgentB)

        agent = router.get_agent("scope", twin=twin, mcp_bridge=mcp)
        assert isinstance(agent, AgentB)
