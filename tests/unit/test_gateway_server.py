"""Tests for the API Gateway server."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.server import ACTION_WORKFLOWS, create_app
from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.mechanical.agent import MechanicalAgent
from orchestrator.dependency_engine import DependencyGraph
from orchestrator.event_bus.subscribers import create_default_bus
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import InMemoryWorkflowEngine
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def wired_app():
    """App with orchestrator components manually wired (no lifespan needed)."""
    app = create_app()

    engine = InMemoryWorkflowEngine.create()
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    event_bus = create_default_bus(engine)

    for defn in ACTION_WORKFLOWS.values():
        await engine.register_workflow(defn)

    dep_graph = DependencyGraph(ACTION_WORKFLOWS["full_validation"])
    dep_graph.validate()

    scheduler = InMemoryScheduler(
        workflow_engine=engine,
        event_bus=event_bus,
        dependency_graph=dep_graph,
    )
    scheduler.register_agent("MECH", MechanicalAgent(twin=twin, mcp=mcp))
    scheduler.register_agent("EE", ElectronicsAgent(twin=twin, mcp=mcp))
    await scheduler.start()

    app.state.workflow_engine = engine
    app.state.scheduler = scheduler
    app.state.twin = twin
    app.state.mcp = mcp
    app.state.event_bus = event_bus
    app.state.action_workflows = ACTION_WORKFLOWS

    yield app

    await scheduler.stop()


@pytest.fixture
async def client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def wired_client(wired_app) -> AsyncClient:
    transport = ASGITransport(app=wired_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- App factory ---


class TestCreateApp:
    """Tests for the create_app factory."""

    def test_create_app_returns_fastapi(self):
        app = create_app()
        assert app.title == "MetaForge Gateway"
        assert app.version == "0.1.0"

    def test_create_app_custom_cors(self):
        app = create_app(cors_origins=["http://localhost:3000"])
        assert app is not None

    def test_create_app_default_cors(self):
        app = create_app()
        assert app is not None


# --- Router mounting ---


class TestRouterMounting:
    """Tests that all routers are properly included."""

    def test_health_route_exists(self, app):
        paths = [r.path for r in app.routes]
        assert "/health" in paths

    def test_assistant_routes_exist(self, app):
        paths = [r.path for r in app.routes]
        assert "/v1/assistant/request" in paths
        assert "/v1/assistant/proposals" in paths

    def test_chat_routes_exist(self, app):
        paths = [r.path for r in app.routes]
        assert "/v1/chat/channels" in paths
        assert "/v1/chat/threads" in paths


# --- Smoke endpoints ---


class TestHealthEndpoint:
    """Smoke test for the health endpoint through the full app."""

    async def test_health_returns_200(self, client: AsyncClient):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert "uptime_seconds" in data
        assert data["version"] == "0.1.0"


class TestChatEndpoints:
    """Smoke tests for the chat endpoints through the full app."""

    async def test_list_channels(self, client: AsyncClient):
        response = await client.get("/v1/chat/channels")
        assert response.status_code == 200
        data = response.json()
        assert "channels" in data
        assert len(data["channels"]) > 0

    async def test_list_threads_empty(self, client: AsyncClient):
        response = await client.get("/v1/chat/threads")
        assert response.status_code == 200
        data = response.json()
        assert "threads" in data


class TestAssistantEndpoints:
    """Smoke tests for the assistant endpoints with orchestrator wired."""

    async def test_submit_request_known_action(self, wired_client: AsyncClient):
        response = await wired_client.post(
            "/v1/assistant/request",
            json={
                "action": "validate_stress",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "project_id": "proj-001",
                "session_id": "00000000-0000-0000-0000-000000000002",
                "parameters": {"mesh_file_path": "mesh/bracket.inp"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "run_id" in data["result"]

    async def test_submit_request_unknown_action(self, wired_client: AsyncClient):
        response = await wired_client.post(
            "/v1/assistant/request",
            json={
                "action": "nonexistent_action",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "project_id": "proj-001",
            },
        )
        assert response.status_code == 400
        assert "Unknown action" in response.json()["detail"]

    async def test_run_status_not_found(self, wired_client: AsyncClient):
        response = await wired_client.get("/v1/assistant/request/nonexistent-run-id")
        assert response.status_code == 404

    async def test_run_status_after_submit(self, wired_client: AsyncClient):
        submit_resp = await wired_client.post(
            "/v1/assistant/request",
            json={
                "action": "run_erc",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "project_id": "proj-001",
                "parameters": {"schematic_file": "eda/main.kicad_sch"},
            },
        )
        assert submit_resp.status_code == 200
        run_id = submit_resp.json()["result"]["run_id"]

        status_resp = await wired_client.get(f"/v1/assistant/request/{run_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["run_id"] == run_id
        assert data["status"] in ("running", "completed", "failed")

    async def test_list_proposals(self, wired_client: AsyncClient):
        response = await wired_client.get("/v1/assistant/proposals")
        assert response.status_code == 200
        data = response.json()
        assert "proposals" in data

    async def test_submit_request_fallback_no_orchestrator(self, client: AsyncClient):
        """Without orchestrator, route falls back to 'accepted' placeholder."""
        response = await client.post(
            "/v1/assistant/request",
            json={
                "action": "validate_stress",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "project_id": "proj-001",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"


# --- CORS middleware ---


class TestCorsMiddleware:
    """Verify CORS headers are present."""

    async def test_cors_headers_on_preflight(self, client: AsyncClient):
        response = await client.options(
            "/health",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "GET",
            },
        )
        # CORS middleware returns 200 for preflight
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


# --- Module-level app ---


class TestModuleLevelApp:
    """Verify the module-level app object."""

    def test_module_app_exists(self):
        from api_gateway.server import app

        assert app is not None
        assert app.title == "MetaForge Gateway"

    def test_main_function_exists(self):
        from api_gateway.server import main

        assert callable(main)
