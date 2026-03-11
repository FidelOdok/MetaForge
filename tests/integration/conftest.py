"""Integration test fixtures — composite, pre-wired component stacks.

These fixtures compose the root-level in-memory components into ready-to-use
integration stacks: agents wired to Twin + MCP, orchestrator stacks with
scheduler + workflow engine + event bus, and a Gateway HTTP test client.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.server import create_app
from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.mechanical.agent import MechanicalAgent
from orchestrator.event_bus.subscribers import EventBus
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import InMemoryWorkflowEngine
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# Realistic tool response constants
# ---------------------------------------------------------------------------

FEA_SUCCESS_RESPONSE: dict[str, Any] = {
    "solver_time": 1.23,
    "max_von_mises": {"bracket_body": 120.5, "mounting_hole": 89.3},
    "max_displacement_mm": 0.42,
    "convergence": True,
    "num_elements": 15420,
    "analysis_type": "static_stress",
}

FEA_HIGH_STRESS_RESPONSE: dict[str, Any] = {
    "solver_time": 2.1,
    "max_von_mises": {"bracket_body": 350.0, "mounting_hole": 280.0},
    "max_displacement_mm": 1.8,
    "convergence": True,
    "num_elements": 15420,
    "analysis_type": "static_stress",
}

ERC_SUCCESS_RESPONSE: dict[str, Any] = {
    "passed": True,
    "total_violations": 0,
    "errors": [],
    "warnings": [],
    "schematic_file": "eda/kicad/main.kicad_sch",
}

ERC_FAILURE_RESPONSE: dict[str, Any] = {
    "passed": False,
    "total_violations": 2,
    "errors": [
        {"type": "unconnected_pin", "component": "U1", "pin": "VCC"},
        {"type": "missing_power_flag", "net": "GND"},
    ],
    "warnings": [],
    "schematic_file": "eda/kicad/main.kicad_sch",
}

DRC_SUCCESS_RESPONSE: dict[str, Any] = {
    "passed": True,
    "total_violations": 0,
    "errors": [],
    "warnings": [],
    "pcb_file": "eda/kicad/main.kicad_pcb",
}

MESH_SUCCESS_RESPONSE: dict[str, Any] = {
    "mesh_file": "/tmp/bracket.inp",
    "num_nodes": 8234,
    "num_elements": 15420,
    "element_types": ["C3D10"],
    "quality_acceptable": True,
    "quality_issues": [],
    "algorithm_used": "netgen",
    "element_size_used": 1.0,
}


# ---------------------------------------------------------------------------
# MCP bridge with pre-registered tool responses
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_with_tools() -> InMemoryMcpBridge:
    """MCP bridge pre-configured with successful FEA, ERC, DRC responses."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool_response("calculix.run_fea", FEA_SUCCESS_RESPONSE)
    bridge.register_tool_response("kicad.run_erc", ERC_SUCCESS_RESPONSE)
    bridge.register_tool_response("kicad.run_drc", DRC_SUCCESS_RESPONSE)
    bridge.register_tool_response("freecad.generate_mesh", MESH_SUCCESS_RESPONSE)
    bridge.register_tool("calculix.run_fea", "fea", "CalculiX FEA")
    bridge.register_tool("kicad.run_erc", "erc", "KiCad ERC")
    bridge.register_tool("kicad.run_drc", "drc", "KiCad DRC")
    bridge.register_tool("freecad.generate_mesh", "mesh", "FreeCAD Mesh")
    return bridge


# ---------------------------------------------------------------------------
# Pre-wired agents
# ---------------------------------------------------------------------------


@pytest.fixture
def mech_agent(twin: InMemoryTwinAPI, mcp_with_tools: InMemoryMcpBridge) -> MechanicalAgent:
    """Mechanical agent wired to real Twin + MCP with tool responses."""
    return MechanicalAgent(twin=twin, mcp=mcp_with_tools)


@pytest.fixture
def ee_agent(twin: InMemoryTwinAPI, mcp_with_tools: InMemoryMcpBridge) -> ElectronicsAgent:
    """Electronics agent wired to real Twin + MCP with tool responses."""
    return ElectronicsAgent(twin=twin, mcp=mcp_with_tools)


# ---------------------------------------------------------------------------
# Mock agents for scheduler tests (implement AgentProtocol)
# ---------------------------------------------------------------------------


class MockAgent:
    """A mock agent that returns a configurable result or raises an error."""

    def __init__(
        self,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[Any] = []

    async def run_task(self, request: Any) -> Any:
        self.calls.append(request)
        if self._error:
            raise self._error
        return self._result or {"status": "ok", "task_type": "mock"}


class FlakeyAgent:
    """An agent that fails on the first N calls, then succeeds."""

    def __init__(self, fail_count: int = 1, result: Any = None) -> None:
        self._fail_count = fail_count
        self._result = result or {"status": "ok", "task_type": "flakey"}
        self.call_count = 0

    async def run_task(self, request: Any) -> Any:
        self.call_count += 1
        if self.call_count <= self._fail_count:
            raise RuntimeError(f"Flakey failure #{self.call_count}")
        return self._result


# ---------------------------------------------------------------------------
# Orchestrator stack (scheduler + workflow engine + event bus + dependency)
# ---------------------------------------------------------------------------


@pytest.fixture
def scheduler(
    workflow_engine: InMemoryWorkflowEngine,
    event_bus: EventBus,
) -> InMemoryScheduler:
    """Scheduler wired to workflow engine and event bus, no dependency graph."""
    return InMemoryScheduler(
        workflow_engine=workflow_engine,
        event_bus=event_bus,
        max_concurrency=4,
    )


@pytest.fixture
def scheduler_with_deps(
    workflow_engine: InMemoryWorkflowEngine,
    event_bus: EventBus,
) -> InMemoryScheduler:
    """Scheduler factory — caller must set dependency_graph after registering workflow."""
    return InMemoryScheduler(
        workflow_engine=workflow_engine,
        event_bus=event_bus,
        max_concurrency=4,
    )


# ---------------------------------------------------------------------------
# Gateway HTTP test client
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """Create a fresh FastAPI app instance."""
    return create_app()


@pytest.fixture
async def http_client(app) -> AsyncClient:
    """Async HTTP client wired to the Gateway app via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
