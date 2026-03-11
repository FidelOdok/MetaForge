"""End-to-end tests for the API Gateway.

Exercises the full HTTP round-trip:
  HTTP request → FastAPI → Orchestrator → Agent → MCP → Twin → HTTP response.

Uses httpx AsyncClient with ASGI transport (no real network, but exercises
the complete FastAPI stack including middleware, routing, and lifecycle).
"""

from __future__ import annotations

import asyncio
import copy
from uuid import uuid4

import httpx
import pytest

from api_gateway.server import ACTION_WORKFLOWS, create_app
from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.mechanical.agent import MechanicalAgent
from orchestrator.dependency_engine import DependencyGraph
from orchestrator.event_bus.subscribers import create_default_bus
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.models.artifact import Artifact
from twin_core.models.enums import ArtifactType

# ---------------------------------------------------------------------------
# Realistic tool mock data
# ---------------------------------------------------------------------------

STRESS_PASS_RESULT = {
    "max_von_mises": {"bracket_body": 85.3, "fillet_region": 120.7},
    "solver_time": 14.2,
    "mesh_elements": 52000,
    "node_count": 18500,
}

ERC_CLEAN_RESULT = {
    "schematic_file": "eda/kicad/main.kicad_sch",
    "total_violations": 0,
    "errors": 0,
    "warnings": 0,
    "violations": [],
    "passed": True,
}

DRC_CLEAN_RESULT = {
    "pcb_file": "eda/kicad/main.kicad_pcb",
    "total_violations": 0,
    "errors": 0,
    "warnings": 0,
    "violations": [],
    "passed": True,
}

CAD_GENERATE_RESULT = {
    "cad_file": "output/bracket_generated.step",
    "volume_mm3": 12500.0,
    "surface_area_mm2": 8400.0,
    "bounding_box": {
        "min_x": 0.0,
        "min_y": 0.0,
        "min_z": 0.0,
        "max_x": 50.0,
        "max_y": 30.0,
        "max_z": 5.0,
    },
    "parameters_used": {"width": 50.0, "height": 30.0, "thickness": 5.0},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mcp() -> InMemoryMcpBridge:
    mcp = InMemoryMcpBridge()
    mcp.register_tool("calculix.run_fea", capability="stress_analysis", name="Run FEA")
    mcp.register_tool_response("calculix.run_fea", STRESS_PASS_RESULT)
    mcp.register_tool("kicad.run_erc", capability="erc_validation", name="Run ERC")
    mcp.register_tool_response("kicad.run_erc", ERC_CLEAN_RESULT)
    mcp.register_tool("kicad.run_drc", capability="drc_validation", name="Run DRC")
    mcp.register_tool_response("kicad.run_drc", DRC_CLEAN_RESULT)
    mcp.register_tool(
        "freecad.create_parametric", capability="cad_generation", name="Create Parametric"
    )
    mcp.register_tool_response("freecad.create_parametric", CAD_GENERATE_RESULT)
    return mcp


@pytest.fixture
async def gateway_stack():
    """Build a fully-wired gateway app with orchestrator, agents, and Twin."""
    twin = InMemoryTwinAPI.create()
    mcp = _make_mcp()
    engine = InMemoryWorkflowEngine.create()
    event_bus = create_default_bus(engine)

    # Deep-copy workflows so in-place step mutation doesn't leak between tests
    workflows = copy.deepcopy(ACTION_WORKFLOWS)

    for defn in workflows.values():
        await engine.register_workflow(defn)

    mech_agent = MechanicalAgent(twin=twin, mcp=mcp)
    ee_agent = ElectronicsAgent(twin=twin, mcp=mcp)

    dep_graph = DependencyGraph(workflows["full_validation"])
    dep_graph.validate()

    scheduler = InMemoryScheduler(
        workflow_engine=engine,
        event_bus=event_bus,
        dependency_graph=dep_graph,
        max_concurrency=4,
    )
    scheduler.register_agent("MECH", mech_agent)
    scheduler.register_agent("EE", ee_agent)
    await scheduler.start()

    app = create_app(
        workflow_engine=engine,
        scheduler=scheduler,
    )
    # Inject action_workflows — httpx ASGI transport doesn't trigger lifespan
    app.state.action_workflows = workflows
    app.state.twin = twin
    app.state.mcp = mcp
    app.state.event_bus = event_bus

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    # Create a test artifact in the Twin
    artifact = await twin.create_artifact(
        Artifact(
            name="drone-fc-assembly",
            type=ArtifactType.CAD_MODEL,
            domain="mechanical",
            file_path="models/drone_fc.step",
            content_hash="sha256:gw1234",
            format="step",
            created_by="human",
            metadata={"project": "drone-fc"},
        )
    )

    yield {
        "client": client,
        "app": app,
        "twin": twin,
        "mcp": mcp,
        "engine": engine,
        "scheduler": scheduler,
        "artifact": artifact,
    }

    await scheduler.stop()
    await client.aclose()


# ---------------------------------------------------------------------------
# Test class: Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpointE2E:
    """Verify the /health endpoint returns correct gateway status."""

    async def test_health_returns_200(self, gateway_stack):
        s = gateway_stack
        resp = await s["client"].get("/health")
        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] in {"healthy", "degraded"}
        assert "version" in body
        assert body["version"] == "0.1.0"
        assert "uptime_seconds" in body

    async def test_health_has_timestamp(self, gateway_stack):
        s = gateway_stack
        resp = await s["client"].get("/health")
        body = resp.json()
        assert "timestamp" in body


# ---------------------------------------------------------------------------
# Test class: Assistant request submission
# ---------------------------------------------------------------------------


class TestAssistantRequestE2E:
    """Verify POST /v1/assistant/request triggers agent execution."""

    async def test_submit_validate_stress(self, gateway_stack):
        """Submit validate_stress action and get a run_id back."""
        s = gateway_stack
        resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "validate_stress",
                "target_id": str(s["artifact"].id),
                "parameters": {
                    "mesh_file_path": "models/bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {"max_von_mises_mpa": 276.0, "safety_factor": 1.5, "material": "Al6061-T6"}
                    ],
                },
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert "run_id" in body["result"]

    async def test_submit_run_erc(self, gateway_stack):
        """Submit run_erc action."""
        s = gateway_stack
        resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "run_erc",
                "target_id": str(s["artifact"].id),
                "parameters": {
                    "schematic_file": "eda/kicad/main.kicad_sch",
                },
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"

    async def test_submit_unknown_action(self, gateway_stack):
        """Unknown action returns 400."""
        s = gateway_stack
        resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "analyze_vibration",
                "target_id": str(s["artifact"].id),
            },
        )

        assert resp.status_code == 400
        assert "Unknown action" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test class: Run status polling
# ---------------------------------------------------------------------------


class TestRunStatusPollingE2E:
    """Verify GET /v1/assistant/request/{run_id} returns step statuses."""

    async def test_poll_run_status(self, gateway_stack):
        """Submit a request, then poll until steps complete."""
        s = gateway_stack

        # Submit
        resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "run_erc",
                "target_id": str(s["artifact"].id),
                "parameters": {"schematic_file": "eda/kicad/main.kicad_sch"},
            },
        )
        assert resp.status_code == 200
        run_id = resp.json()["result"]["run_id"]

        # Poll until complete
        for _ in range(50):
            await asyncio.sleep(0.1)
            status_resp = await s["client"].get(f"/v1/assistant/request/{run_id}")
            if status_resp.status_code != 200:
                continue
            status_body = status_resp.json()
            steps = status_body.get("steps", {})
            if any(s_data.get("status") in {"completed", "failed"} for s_data in steps.values()):
                break

        assert status_resp.status_code == 200
        assert status_body["run_id"] == run_id

    async def test_poll_nonexistent_run(self, gateway_stack):
        """Polling a non-existent run returns 404."""
        s = gateway_stack
        resp = await s["client"].get("/v1/assistant/request/nonexistent-run-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test class: Proposal endpoints
# ---------------------------------------------------------------------------


class TestProposalEndpointsE2E:
    """Verify design-change proposal endpoints."""

    async def test_list_proposals_empty(self, gateway_stack):
        """Empty proposal list returns 200 with empty array."""
        s = gateway_stack
        resp = await s["client"].get("/v1/assistant/proposals")
        assert resp.status_code == 200

        body = resp.json()
        assert body["proposals"] == []
        assert body["total"] == 0

    async def test_get_nonexistent_proposal(self, gateway_stack):
        """Getting a non-existent proposal returns 404."""
        s = gateway_stack
        resp = await s["client"].get(f"/v1/assistant/proposals/{uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test class: Full round-trip (submit → poll → verify)
# ---------------------------------------------------------------------------


class TestFullRoundTripE2E:
    """Verify a complete request lifecycle through the gateway."""

    async def test_validate_stress_round_trip(self, gateway_stack):
        """Submit validate_stress → poll status → verify agent ran."""
        s = gateway_stack

        # 1. Submit
        submit_resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "validate_stress",
                "target_id": str(s["artifact"].id),
                "parameters": {
                    "mesh_file_path": "models/bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {"max_von_mises_mpa": 276.0, "safety_factor": 1.5, "material": "Al6061-T6"}
                    ],
                },
            },
        )
        assert submit_resp.status_code == 200
        run_id = submit_resp.json()["result"]["run_id"]

        # 2. Poll until complete
        final_body = None
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await s["client"].get(f"/v1/assistant/request/{run_id}")
            if resp.status_code == 200:
                body = resp.json()
                steps = body.get("steps", {})
                stress_step = steps.get("stress")
                if stress_step and stress_step.get("status") in {"completed", "failed"}:
                    final_body = body
                    break

        assert final_body is not None
        assert final_body["steps"]["stress"]["status"] == "completed"

    async def test_full_validation_round_trip(self, gateway_stack):
        """Submit full_validation (MECH + EE) → poll → verify both steps."""
        s = gateway_stack

        submit_resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "full_validation",
                "target_id": str(s["artifact"].id),
                "parameters": {
                    "mesh_file_path": "models/bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [
                        {"max_von_mises_mpa": 276.0, "safety_factor": 1.5, "material": "Al6061-T6"}
                    ],
                    "schematic_file": "eda/kicad/main.kicad_sch",
                },
            },
        )
        assert submit_resp.status_code == 200
        run_id = submit_resp.json()["result"]["run_id"]

        # Poll until both steps complete
        final_body = None
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await s["client"].get(f"/v1/assistant/request/{run_id}")
            if resp.status_code != 200:
                continue
            body = resp.json()
            steps = body.get("steps", {})
            stress = steps.get("stress", {})
            erc = steps.get("erc", {})
            if stress.get("status") in {"completed", "failed"} and erc.get("status") in {
                "completed",
                "failed",
            }:
                final_body = body
                break

        assert final_body is not None
        assert "stress" in final_body["steps"]
        assert "erc" in final_body["steps"]

    async def test_generate_cad_round_trip(self, gateway_stack):
        """Submit generate_cad → poll status → verify agent ran."""
        s = gateway_stack

        # 1. Submit
        submit_resp = await s["client"].post(
            "/v1/assistant/request",
            json={
                "action": "generate_cad",
                "target_id": str(s["artifact"].id),
                "parameters": {
                    "shape_type": "bracket",
                    "dimensions": {"width": 50.0, "height": 30.0, "thickness": 5.0},
                    "material": "aluminum_6061",
                },
            },
        )
        assert submit_resp.status_code == 200
        run_id = submit_resp.json()["result"]["run_id"]

        # 2. Poll until complete
        final_body = None
        for _ in range(50):
            await asyncio.sleep(0.1)
            resp = await s["client"].get(f"/v1/assistant/request/{run_id}")
            if resp.status_code == 200:
                body = resp.json()
                steps = body.get("steps", {})
                cad_step = steps.get("cad")
                if cad_step and cad_step.get("status") in {"completed", "failed"}:
                    final_body = body
                    break

        assert final_body is not None
        assert final_body["steps"]["cad"]["status"] == "completed"
