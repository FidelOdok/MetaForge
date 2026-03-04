"""Unit tests for chat REST endpoints (MET-82).

Uses FastAPI's TestClient to exercise every endpoint under
``/api/v1/chat``.  The in-memory ``ChatStore`` is reset between tests
so they remain independent.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.chat.routes import ChatStore, router
from api_gateway.chat.routes import store as _module_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Reset the module-level store before every test so tests are isolated."""
    fresh = ChatStore.create()
    _module_store.channels.clear()
    _module_store.channels.update(fresh.channels)
    _module_store.threads.clear()
    _module_store.messages.clear()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_thread(
    client: TestClient,
    *,
    scope_kind: str = "session",
    scope_entity_id: str = "entity-1",
    title: str | None = None,
    initial_message: str | None = None,
) -> dict:
    payload: dict = {
        "scope_kind": scope_kind,
        "scope_entity_id": scope_entity_id,
    }
    if title is not None:
        payload["title"] = title
    if initial_message is not None:
        payload["initial_message"] = initial_message
    resp = client.post("/api/v1/chat/threads", json=payload)
    assert resp.status_code == 201
    return resp.json()


def _send_message(
    client: TestClient,
    thread_id: str,
    *,
    content: str = "hello",
    actor_id: str = "user-1",
    actor_kind: str = "user",
) -> dict:
    resp = client.post(
        f"/api/v1/chat/threads/{thread_id}/messages",
        json={
            "content": content,
            "actor_id": actor_id,
            "actor_kind": actor_kind,
        },
    )
    assert resp.status_code == 201
    return resp.json()


# ===================================================================
# GET /channels
# ===================================================================


class TestListChannels:
    def test_returns_default_channels(self, client: TestClient) -> None:
        resp = client.get("/api/v1/chat/channels")
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert len(data["channels"]) == 5

    def test_channel_fields(self, client: TestClient) -> None:
        resp = client.get("/api/v1/chat/channels")
        ch = resp.json()["channels"][0]
        assert "id" in ch
        assert "name" in ch
        assert "scope_kind" in ch
        assert "created_at" in ch

    def test_channel_scope_kinds(self, client: TestClient) -> None:
        resp = client.get("/api/v1/chat/channels")
        scope_kinds = {ch["scope_kind"] for ch in resp.json()["channels"]}
        expected = {"session", "approval", "bom-entry", "digital-twin-node", "project"}
        assert scope_kinds == expected


# ===================================================================
# POST /threads
# ===================================================================


class TestCreateThread:
    def test_create_minimal_thread(self, client: TestClient) -> None:
        data = _create_thread(client)
        assert "id" in data
        assert data["scope_kind"] == "session"
        assert data["scope_entity_id"] == "entity-1"
        assert data["archived"] is False
        assert data["messages"] == []

    def test_create_thread_with_title(self, client: TestClient) -> None:
        data = _create_thread(client, title="My Thread")
        assert data["title"] == "My Thread"

    def test_create_thread_auto_title(self, client: TestClient) -> None:
        data = _create_thread(client)
        assert data["title"].startswith("Thread ")

    def test_create_thread_with_initial_message(self, client: TestClient) -> None:
        data = _create_thread(client, initial_message="Hello, world!")
        assert len(data["messages"]) == 1
        msg = data["messages"][0]
        assert msg["content"] == "Hello, world!"
        assert msg["actor_kind"] == "system"
        assert msg["actor_id"] == "system"

    def test_create_thread_assigns_channel(self, client: TestClient) -> None:
        data = _create_thread(client, scope_kind="approval")
        assert data["channel_id"]  # non-empty
        # Verify channel exists
        channels_resp = client.get("/api/v1/chat/channels")
        channel_ids = {ch["id"] for ch in channels_resp.json()["channels"]}
        assert data["channel_id"] in channel_ids

    def test_create_thread_invalid_scope(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/chat/threads",
            json={
                "scope_kind": "nonexistent-scope",
                "scope_entity_id": "entity-1",
            },
        )
        assert resp.status_code == 400
        assert "No channel found" in resp.json()["detail"]

    def test_create_thread_missing_required_field(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/chat/threads",
            json={"scope_kind": "session"},
        )
        assert resp.status_code == 422  # Pydantic validation error


# ===================================================================
# GET /threads/{thread_id}
# ===================================================================


class TestGetThread:
    def test_get_existing_thread(self, client: TestClient) -> None:
        created = _create_thread(client)
        resp = client.get(f"/api/v1/chat/threads/{created['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == created["id"]
        assert data["scope_kind"] == "session"

    def test_get_thread_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/chat/threads/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Thread not found"

    def test_get_thread_includes_messages(self, client: TestClient) -> None:
        created = _create_thread(client, initial_message="msg-1")
        _send_message(client, created["id"], content="msg-2")
        resp = client.get(f"/api/v1/chat/threads/{created['id']}")
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "msg-1"
        assert data["messages"][1]["content"] == "msg-2"


# ===================================================================
# GET /threads  (list + filtering + pagination)
# ===================================================================


class TestListThreads:
    def test_empty_list(self, client: TestClient) -> None:
        resp = client.get("/api/v1/chat/threads")
        assert resp.status_code == 200
        data = resp.json()
        assert data["threads"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    def test_list_returns_created_threads(self, client: TestClient) -> None:
        _create_thread(client, scope_entity_id="e1")
        _create_thread(client, scope_entity_id="e2")
        resp = client.get("/api/v1/chat/threads")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["threads"]) == 2

    def test_filter_by_scope_kind(self, client: TestClient) -> None:
        _create_thread(client, scope_kind="session", scope_entity_id="e1")
        _create_thread(client, scope_kind="approval", scope_entity_id="e2")
        resp = client.get("/api/v1/chat/threads?scope_kind=approval")
        data = resp.json()
        assert data["total"] == 1
        assert data["threads"][0]["scope_kind"] == "approval"

    def test_filter_by_entity_id(self, client: TestClient) -> None:
        _create_thread(client, scope_entity_id="e1")
        _create_thread(client, scope_entity_id="e2")
        resp = client.get("/api/v1/chat/threads?entity_id=e2")
        data = resp.json()
        assert data["total"] == 1
        assert data["threads"][0]["scope_entity_id"] == "e2"

    def test_filter_by_channel_id(self, client: TestClient) -> None:
        t1 = _create_thread(client, scope_kind="session", scope_entity_id="e1")
        _create_thread(client, scope_kind="approval", scope_entity_id="e2")
        channel_id = t1["channel_id"]
        resp = client.get(f"/api/v1/chat/threads?channel_id={channel_id}")
        data = resp.json()
        assert data["total"] == 1
        assert data["threads"][0]["channel_id"] == channel_id

    def test_archived_excluded_by_default(self, client: TestClient) -> None:
        created = _create_thread(client)
        # Manually archive the thread via the store
        _module_store.threads[created["id"]].archived = True
        resp = client.get("/api/v1/chat/threads")
        assert resp.json()["total"] == 0

    def test_include_archived(self, client: TestClient) -> None:
        created = _create_thread(client)
        _module_store.threads[created["id"]].archived = True
        resp = client.get("/api/v1/chat/threads?include_archived=true")
        assert resp.json()["total"] == 1

    def test_pagination(self, client: TestClient) -> None:
        for i in range(5):
            _create_thread(client, scope_entity_id=f"e{i}")
        resp = client.get("/api/v1/chat/threads?page=1&per_page=2")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["threads"]) == 2
        assert data["page"] == 1
        assert data["per_page"] == 2

    def test_pagination_page_2(self, client: TestClient) -> None:
        for i in range(5):
            _create_thread(client, scope_entity_id=f"e{i}")
        resp = client.get("/api/v1/chat/threads?page=2&per_page=2")
        data = resp.json()
        assert len(data["threads"]) == 2

    def test_pagination_last_page(self, client: TestClient) -> None:
        for i in range(5):
            _create_thread(client, scope_entity_id=f"e{i}")
        resp = client.get("/api/v1/chat/threads?page=3&per_page=2")
        data = resp.json()
        assert len(data["threads"]) == 1

    def test_thread_summary_has_message_count(self, client: TestClient) -> None:
        created = _create_thread(client, initial_message="hi")
        _send_message(client, created["id"], content="reply")
        resp = client.get("/api/v1/chat/threads")
        summary = resp.json()["threads"][0]
        assert summary["message_count"] == 2


# ===================================================================
# POST /threads/{thread_id}/messages
# ===================================================================


class TestSendMessage:
    def test_send_message(self, client: TestClient) -> None:
        created = _create_thread(client)
        msg = _send_message(client, created["id"])
        assert msg["content"] == "hello"
        assert msg["actor_id"] == "user-1"
        assert msg["actor_kind"] == "user"
        assert msg["status"] == "sent"

    def test_send_message_thread_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/chat/threads/nonexistent/messages",
            json={
                "content": "hello",
                "actor_id": "user-1",
                "actor_kind": "user",
            },
        )
        assert resp.status_code == 404

    def test_send_message_with_graph_ref(self, client: TestClient) -> None:
        created = _create_thread(client)
        resp = client.post(
            f"/api/v1/chat/threads/{created['id']}/messages",
            json={
                "content": "linked message",
                "actor_id": "agent-1",
                "actor_kind": "agent",
                "graph_ref_node": "node-123",
                "graph_ref_type": "CAD_MODEL",
                "graph_ref_label": "bracket.step",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["graph_ref_node"] == "node-123"
        assert data["graph_ref_type"] == "CAD_MODEL"
        assert data["graph_ref_label"] == "bracket.step"

    def test_send_message_empty_content_rejected(self, client: TestClient) -> None:
        created = _create_thread(client)
        resp = client.post(
            f"/api/v1/chat/threads/{created['id']}/messages",
            json={
                "content": "",
                "actor_id": "user-1",
                "actor_kind": "user",
            },
        )
        assert resp.status_code == 422  # Pydantic min_length=1

    def test_send_message_updates_last_message_at(self, client: TestClient) -> None:
        created = _create_thread(client)
        original_ts = created["last_message_at"]
        _send_message(client, created["id"], content="new msg")
        resp = client.get(f"/api/v1/chat/threads/{created['id']}")
        updated_ts = resp.json()["last_message_at"]
        assert updated_ts >= original_ts
