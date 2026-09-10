"""Unit tests for the Runs API endpoints (MET-547, Phase 1)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.runs.routes import get_run_store, init_run_ledger, reset_run_store, router
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import RunNotFoundError, RunStatus


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


class TestRunLedgerDurability:
    """Production-harness audit follow-up: transitions write through to a
    wired ledger, and non-terminal runs rehydrate into a fresh store."""

    def test_transitions_write_through_to_the_ledger(self, client: TestClient) -> None:
        ledger = SqliteRunLedger(":memory:")
        init_run_ledger(ledger)
        try:
            run_id = client.post("/v1/runs", json={"request": {"goal": "widget"}}).json()["id"]
            persisted = ledger.get_run(run_id)
            assert persisted is not None
            assert persisted["status"] == "running"
            assert persisted["request"] == {"goal": "widget"}
        finally:
            reset_run_store()

    def test_init_run_ledger_none_keeps_process_local_behavior(self, client: TestClient) -> None:
        """The default (no ledger) — every other test in this file — must be
        completely unaffected by the durability feature existing."""
        run_id = client.post("/v1/runs", json={}).json()["id"]
        assert get_run_store().get(run_id) is not None  # no ledger involved at all

    def test_rehydrates_non_terminal_runs_on_init(self, client: TestClient) -> None:
        ledger = SqliteRunLedger(":memory:")
        store = get_run_store()
        running = store.create({"goal": "in flight"}, run_id="was-running")
        store.start(running.id)
        ledger.record_run(store.get(running.id))
        done = store.create({}, run_id="already-done")
        store.start(done.id)
        store.complete(done.id)
        ledger.record_run(store.get(done.id))

        reset_run_store()  # simulates a fresh process: brand-new, empty store
        init_run_ledger(ledger)
        try:
            fresh_store = get_run_store()
            assert fresh_store.get("was-running").status is RunStatus.RUNNING
            with pytest.raises(RunNotFoundError):  # terminal runs don't rehydrate
                fresh_store.get("already-done")
        finally:
            reset_run_store()
