"""Unit tests for the Runs API endpoints (MET-547, Phase 1)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.runs.routes import get_run_store, reset_run_store, router


@pytest.fixture
def client() -> TestClient:
    reset_run_store()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_create_run_starts_running_by_default(client: TestClient) -> None:
    resp = client.post("/v1/runs", json={"request": {"goal": "widget"}})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "running"
    assert body["request"] == {"goal": "widget"}
    assert body["history"] == ["queued", "running"]


def test_create_run_without_start_stays_queued(client: TestClient) -> None:
    resp = client.post("/v1/runs", json={"request": {}, "start": False})
    assert resp.status_code == 201
    assert resp.json()["status"] == "queued"


def test_get_run_roundtrip(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={}).json()["id"]
    resp = client.get(f"/v1/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


def test_get_unknown_run_404(client: TestClient) -> None:
    resp = client.get("/v1/runs/run_nope")
    assert resp.status_code == 404


def test_list_runs(client: TestClient) -> None:
    client.post("/v1/runs", json={})
    client.post("/v1/runs", json={})
    resp = client.get("/v1/runs")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 2


def test_approval_approve_resumes_running(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={}).json()["id"]
    # Drive the run to awaiting_approval directly (harness-internal step).
    get_run_store().request_approval(run_id, reason="destructive op")
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_approval_reject_is_terminal(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={}).json()["id"]
    get_run_store().request_approval(run_id)
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": "reject"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_approval_on_running_run_conflicts_409(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={}).json()["id"]  # running, not awaiting
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": "approve"})
    assert resp.status_code == 409


def test_approval_unknown_run_404(client: TestClient) -> None:
    resp = client.post("/v1/runs/run_nope/approval", json={"decision": "approve"})
    assert resp.status_code == 404


def test_approval_bad_decision_422(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={}).json()["id"]
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": "maybe"})
    assert resp.status_code == 422  # schema validation rejects it
