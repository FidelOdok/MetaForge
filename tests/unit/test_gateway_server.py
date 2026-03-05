"""Tests for the API Gateway server."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api_gateway.server import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
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
        assert "/api/v1/assistant/request" in paths
        assert "/api/v1/assistant/proposals" in paths

    def test_chat_routes_exist(self, app):
        paths = [r.path for r in app.routes]
        assert "/api/v1/chat/channels" in paths
        assert "/api/v1/chat/threads" in paths


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
        response = await client.get("/api/v1/chat/channels")
        assert response.status_code == 200
        data = response.json()
        assert "channels" in data
        assert len(data["channels"]) > 0

    async def test_list_threads_empty(self, client: AsyncClient):
        response = await client.get("/api/v1/chat/threads")
        assert response.status_code == 200
        data = response.json()
        assert "threads" in data


class TestAssistantEndpoints:
    """Smoke tests for the assistant endpoints through the full app."""

    async def test_submit_request(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/assistant/request",
            json={
                "action": "validate",
                "target_id": "00000000-0000-0000-0000-000000000001",
                "session_id": "00000000-0000-0000-0000-000000000002",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"

    async def test_list_proposals(self, client: AsyncClient):
        response = await client.get("/api/v1/assistant/proposals")
        assert response.status_code == 200
        data = response.json()
        assert "proposals" in data


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
