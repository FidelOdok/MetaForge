"""Unit tests for the tool-approval REST surface (production-harness audit
follow-up — the third permission tier, "ask")."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.chat.tool_approvals import get_approval_store, reset_approval_store, router


@pytest.fixture
def client() -> TestClient:
    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _pending_run(run_id: str = "run_1") -> None:
    store = get_approval_store()
    store.create({"tool": "twin.commit_geometry", "arguments": {"x": 1}}, run_id=run_id)
    store.start(run_id)
    store.request_approval(run_id, reason="approval required for tool 'twin.commit_geometry'")


def test_list_pending_approvals_empty(client: TestClient) -> None:
    resp = client.get("/v1/chat/tool_approvals")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


def test_list_pending_approvals_returns_awaiting_only(client: TestClient) -> None:
    _pending_run("run_1")
    store = get_approval_store()
    store.create({"tool": "freecad.pad_sketch"}, run_id="run_2")  # stays queued, not pending
    resp = client.get("/v1/chat/tool_approvals")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["runs"]]
    assert ids == ["run_1"]


def test_get_approval_roundtrip(client: TestClient) -> None:
    _pending_run("run_1")
    resp = client.get("/v1/chat/tool_approvals/run_1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_approval"
    assert body["request"] == {"tool": "twin.commit_geometry", "arguments": {"x": 1}}


def test_get_unknown_approval_404(client: TestClient) -> None:
    resp = client.get("/v1/chat/tool_approvals/nope")
    assert resp.status_code == 404


def test_submit_approve_resumes_running(client: TestClient) -> None:
    _pending_run("run_1")
    resp = client.post("/v1/chat/tool_approvals/run_1", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_submit_reject_is_terminal(client: TestClient) -> None:
    _pending_run("run_1")
    resp = client.post("/v1/chat/tool_approvals/run_1", json={"decision": "reject"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_submit_unknown_run_404(client: TestClient) -> None:
    resp = client.post("/v1/chat/tool_approvals/nope", json={"decision": "approve"})
    assert resp.status_code == 404


def test_submit_on_non_awaiting_run_conflicts_409(client: TestClient) -> None:
    store = get_approval_store()
    store.create({}, run_id="run_1")
    store.start("run_1")  # running, not awaiting_approval
    resp = client.post("/v1/chat/tool_approvals/run_1", json={"decision": "approve"})
    assert resp.status_code == 409


def test_submit_bad_decision_422(client: TestClient) -> None:
    _pending_run("run_1")
    resp = client.post("/v1/chat/tool_approvals/run_1", json={"decision": "maybe"})
    assert resp.status_code == 422


def test_reset_approval_store_gives_a_fresh_store(client: TestClient) -> None:
    _pending_run("run_1")
    reset_approval_store()
    assert get_approval_store().list() == []
