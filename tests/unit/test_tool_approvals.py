"""Unit tests for the tool-approval REST surface (production-harness audit
follow-up — the third permission tier, "ask")."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.chat.tool_approvals import (
    get_approval_store,
    init_approval_ledger,
    reset_approval_store,
    router,
)
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import RunStatus


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


class TestApprovalLedgerDurability:
    """FORGE-89: transitions write through to a wired ledger; a restored
    AWAITING_APPROVAL row comes back FAILED/orphaned, not resumable — a
    restarted gateway has no coroutine left to resolve it."""

    def test_transitions_write_through_to_the_ledger(self, client: TestClient) -> None:
        ledger = SqliteRunLedger(":memory:")
        init_approval_ledger(ledger)
        try:
            _pending_run("run_1")
            persisted = ledger.get_run("run_1")
            assert persisted is not None
            assert persisted["status"] == "awaiting_approval"
        finally:
            reset_approval_store()

    def test_init_approval_ledger_none_keeps_process_local_behavior(
        self, client: TestClient
    ) -> None:
        _pending_run("run_1")
        assert get_approval_store().get("run_1") is not None  # no ledger involved

    def test_restores_completed_and_running_as_is(self, client: TestClient) -> None:
        ledger = SqliteRunLedger(":memory:")
        store = get_approval_store()
        running = store.create({"tool": "freecad.pad_sketch"}, run_id="was-running")
        store.start(running.id)
        ledger.record_run(store.get(running.id))
        done = store.create({}, run_id="already-done")
        store.start(done.id)
        store.complete(done.id, result={})
        ledger.record_run(store.get(done.id))

        reset_approval_store()
        init_approval_ledger(ledger)
        try:
            fresh_store = get_approval_store()
            assert fresh_store.get("was-running").status is RunStatus.RUNNING
            assert fresh_store.get("already-done").status is RunStatus.COMPLETED
        finally:
            reset_approval_store()

    def test_restores_awaiting_approval_as_orphaned_failed(self, client: TestClient) -> None:
        ledger = SqliteRunLedger(":memory:")
        store = get_approval_store()
        run = store.create({"tool": "twin.commit_geometry"}, run_id="was-pending")
        store.start(run.id)
        store.request_approval(run.id, reason="approval required")
        ledger.record_run(store.get(run.id))

        reset_approval_store()
        init_approval_ledger(ledger)
        try:
            fresh_store = get_approval_store()
            restored = fresh_store.get("was-pending")
            assert restored.status is RunStatus.FAILED
            assert restored.error is not None and "orphaned" in restored.error
        finally:
            reset_approval_store()

    def test_reset_approval_store_clears_the_ledger_too(self, client: TestClient) -> None:
        ledger = SqliteRunLedger(":memory:")
        init_approval_ledger(ledger)
        reset_approval_store()
        _pending_run("run_1")  # must not raise even though the old ledger is gone
        assert ledger.get_run("run_1") is None  # old ledger disconnected, never written
