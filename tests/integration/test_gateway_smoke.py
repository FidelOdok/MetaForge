"""Integration tests: Gateway HTTP smoke tests.

Tests the FastAPI Gateway endpoints end-to-end via ASGI transport,
verifying health checks, assistant request handling, chat CRUD,
CORS configuration, and validation error responses.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_returns_200(self, http_client: AsyncClient):
        resp = await http_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "uptime_seconds" in data

    async def test_health_includes_version(self, http_client: AsyncClient):
        resp = await http_client.get("/health")
        data = resp.json()
        assert data["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# Assistant endpoints
# ---------------------------------------------------------------------------


class TestAssistantEndpoints:
    async def test_submit_request_accepted(self, http_client: AsyncClient):
        resp = await http_client.post(
            "/v1/assistant/request",
            json={
                "action": "validate_stress",
                "target_id": str(uuid4()),
                "parameters": {"mesh_file_path": "cad/bracket.inp"},
                "session_id": str(uuid4()),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "request_id" in data

    async def test_submit_request_validation_error(self, http_client: AsyncClient):
        # Missing required fields
        resp = await http_client.post(
            "/v1/assistant/request",
            json={"action": "validate_stress"},
        )
        assert resp.status_code == 422

    async def test_list_proposals_empty(self, http_client: AsyncClient):
        resp = await http_client.get("/v1/assistant/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["proposals"] == []
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


class TestChatEndpoints:
    async def test_list_channels(self, http_client: AsyncClient):
        resp = await http_client.get("/v1/chat/channels")
        assert resp.status_code == 200
        data = resp.json()
        channels = data["channels"]
        assert len(channels) >= 1
        # Default channels include "Session Chat"
        names = {ch["name"] for ch in channels}
        assert "Session Chat" in names

    async def test_create_thread_and_send_message(self, http_client: AsyncClient):
        # Create a thread
        create_resp = await http_client.post(
            "/v1/chat/threads",
            json={
                "scope_kind": "session",
                "scope_entity_id": str(uuid4()),
                "title": "Test Thread",
                "initial_message": "Hello, world!",
            },
        )
        assert create_resp.status_code == 201
        thread = create_resp.json()
        thread_id = thread["id"]
        assert thread["title"] == "Test Thread"
        assert len(thread["messages"]) == 1
        assert thread["messages"][0]["content"] == "Hello, world!"

        # Send a follow-up message
        msg_resp = await http_client.post(
            f"/v1/chat/threads/{thread_id}/messages",
            json={
                "actor_id": "user-1",
                "actor_kind": "human",
                "content": "Follow-up message",
            },
        )
        assert msg_resp.status_code == 201
        msg = msg_resp.json()
        assert msg["content"] == "Follow-up message"
        assert msg["actor_kind"] == "human"

        # Retrieve the thread with all messages
        get_resp = await http_client.get(f"/v1/chat/threads/{thread_id}")
        assert get_resp.status_code == 200
        full_thread = get_resp.json()
        assert len(full_thread["messages"]) == 2

    async def test_thread_not_found(self, http_client: AsyncClient):
        resp = await http_client.get("/v1/chat/threads/nonexistent-id")
        assert resp.status_code == 404

    async def test_invalid_scope_kind(self, http_client: AsyncClient):
        resp = await http_client.post(
            "/v1/chat/threads",
            json={
                "scope_kind": "nonexistent-scope",
                "scope_entity_id": str(uuid4()),
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCors:
    async def test_cors_preflight_headers(self, http_client: AsyncClient):
        resp = await http_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS middleware should respond
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
