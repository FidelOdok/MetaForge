"""Gate decisions given in the client's chat, recorded by the gateway (FORGE-582)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api_gateway.approvals.routes import router
from api_gateway.auth.principal import Principal
from api_gateway.design_flows.mcp_bindings import make_gate_decider, make_gate_reader
from api_gateway.runs import routes as run_routes


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
    run_routes.reset_run_store()
    app = FastAPI()

    @app.middleware("http")
    async def _principal(request: Request, call_next):  # type: ignore[no-untyped-def]
        who = request.headers.get("x-test-principal")
        if who:
            request.state.principal = Principal(subject=who, email=f"{who}@example.com")
        return await call_next(request)

    app.include_router(router)
    yield TestClient(app)
    run_routes.reset_run_store()


def _gate(*, ready: bool = True) -> str:
    store = run_routes.get_run_store()
    run = store.create({"flow": "design_v1", "flow_engine": "in_process", "project_id": "p1"})
    store.start(run.id)
    run_routes.get_gate_coordinator().set_gate_state(
        run.id, ready=ready, retries_left=2, phase="design", reworks_left=1
    )
    store.request_approval(
        run.id,
        reason="" if ready else "[design gate] NOT READY (retry the phase or reject): missing",
    )
    return run.id


def test_an_inline_approval_is_recorded_as_chat(client: TestClient) -> None:
    run_id = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{run_id}/inline-decision",
        json={"decision": "approve", "approver": "user:fidel", "approver_verified": True},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["decision"]
    assert record["surface"] == "chat"
    # An unauthenticated request cannot vouch for the approver it names.
    assert record["approver"] == "user:fidel"
    assert record["approver_verified"] is False


def test_an_authenticated_sidecar_records_a_verified_approver(client: TestClient) -> None:
    run_id = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{run_id}/inline-decision",
        json={"decision": "approve", "approver": "user:fidel", "approver_verified": True},
        headers={"x-test-principal": "sidecar"},
    )
    assert resp.json()["decision"]["approver_verified"] is True


def test_no_named_approver_is_the_unverified_stand_in(client: TestClient) -> None:
    run_id = _gate()
    resp = client.post(f"/v1/approvals/gate:{run_id}/inline-decision", json={"decision": "approve"})
    assert resp.json()["decision"]["approver"] == "local:elicitation"


def test_a_gate_that_is_not_ready_cannot_be_approved_inline(client: TestClient) -> None:
    run_id = _gate(ready=False)
    resp = client.post(f"/v1/approvals/gate:{run_id}/inline-decision", json={"decision": "approve"})
    assert resp.status_code in (409, 422)
    assert run_routes.get_run_store().get(run_id).status.value == "awaiting_approval"


def test_reject_needs_a_reason_inline_too(client: TestClient) -> None:
    run_id = _gate()
    resp = client.post(f"/v1/approvals/gate:{run_id}/inline-decision", json={"decision": "reject"})
    assert resp.status_code == 422


def test_only_gates_are_decided_here(client: TestClient) -> None:
    resp = client.post("/v1/approvals/tool:abc/inline-decision", json={"decision": "approve"})
    assert resp.status_code == 422


def test_in_process_bindings_read_and_decide_a_gate(client: TestClient) -> None:
    run_id = _gate()
    state = asyncio.run(make_gate_reader()(run_id))
    assert state["run_status"] == "awaiting_approval"
    assert state["gate"]["status"] == "pending"
    assert "approve" in state["gate"]["allowed_decisions"]
    item = asyncio.run(make_gate_decider()(f"gate:{run_id}", "approve", "", "", None, True))
    assert item["decision"]["surface"] == "chat"
    # No approver named: the stand-in, and never verified on its own say-so.
    assert item["decision"]["approver_verified"] is False


def test_in_process_reader_reports_a_run_without_a_gate(client: TestClient) -> None:
    store = run_routes.get_run_store()
    run = store.create({"flow": "design_v1"})
    store.start(run.id)
    state = asyncio.run(make_gate_reader()(run.id))
    assert state == {"run_status": "running", "gate": None}
