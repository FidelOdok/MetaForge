"""Unified approvals API (FORGE-507): list, detail and decide across every kind."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api_gateway.approvals.findings import findings_from_reason
from api_gateway.approvals.routes import router, set_metrics
from api_gateway.assistant import routes as assistant_routes
from api_gateway.auth.principal import Principal
from api_gateway.chat import tool_approvals
from api_gateway.design_loop import routes as design_loop_routes
from api_gateway.runs import routes as run_routes
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.design_sketch_recorder import make_design_sketch_approver
from api_gateway.twin.technical_drawing_viewer import make_technical_drawing_approver
from observability.metrics import MetricsCollector
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import RunStatus
from twin_core.api import InMemoryTwinAPI
from twin_core.models.design_loop_iteration import DesignLoopIteration
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

NOT_READY = (
    "[design gate] NOT READY (retry the phase or reject): phase 'design' did not record "
    "required deliverables ['cad_model'] (present: none); 2 constraint violation(s): a; b"
)


class _Recorder(MetricsCollector):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, str, str]] = []

    def record_approval_decision(
        self, kind: str, decision: str, surface: str, outcome: str
    ) -> None:
        self.calls.append((kind, decision, surface, outcome))


@pytest.fixture
def metrics() -> Iterator[_Recorder]:
    rec = _Recorder()
    set_metrics(rec)
    yield rec
    set_metrics(None)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def client(
    twin: InMemoryTwinAPI, metrics: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
    run_routes.reset_run_store()
    tool_approvals.reset_approval_store()
    assistant_routes.workflow.reset()
    twin_routes.init_twin(twin)
    design_loop_routes.init_twin(twin)
    twin_routes.init_design_sketch_approver(make_design_sketch_approver(twin))
    twin_routes.init_technical_drawing_approver(make_technical_drawing_approver(twin))
    app = FastAPI()
    app.state.twin = twin
    app.state.proposal_apply = None

    @app.middleware("http")
    async def _principal(request: Request, call_next):  # type: ignore[no-untyped-def]
        who = request.headers.get("x-test-principal")
        if who:
            request.state.principal = Principal(subject=who, email=f"{who}@example.com")
        return await call_next(request)

    app.include_router(router)
    yield TestClient(app)
    twin_routes.init_design_sketch_approver(None)
    twin_routes.init_technical_drawing_approver(None)


def _gate(*, ready: bool = True, retries_left: int = 2, flow_engine: str = "in_process") -> str:
    store = run_routes.get_run_store()
    run = store.create({"flow": "design_v1", "flow_engine": flow_engine, "project_id": "p1"})
    store.start(run.id)
    run_routes.get_gate_coordinator().set_gate_state(
        run.id, ready=ready, retries_left=retries_left, phase="design", reworks_left=1
    )
    store.request_approval(run.id, reason="" if ready else NOT_READY)
    return run.id


def _tool(route: str = "dashboard", **extra: object) -> str:
    store = tool_approvals.get_approval_store()
    run = store.create(
        {"tool": "twin.commit_geometry", "arguments": {"x": 1}, "route": route, **extra}
    )
    store.start(run.id)
    store.request_approval(run.id, reason="approval required")
    return run.id


def _change() -> UUID:
    proposal = asyncio.run(
        assistant_routes.workflow.propose_change(
            "mech", "thicken the wall", {"wall": 3}, [uuid4()], project_id="p1"
        )
    )
    return proposal.change_id


def _wp(twin: InMemoryTwinAPI, wp_type: WorkProductType) -> UUID:
    wp = WorkProduct(
        name="x",
        type=wp_type,
        domain="mechanical",
        file_path="x.html",
        content_hash="h",
        format="html",
        created_by="agent",
    )
    return asyncio.run(twin.create_work_product(wp)).id


def _loop(twin: InMemoryTwinAPI) -> UUID:
    loop_id = uuid4()
    asyncio.run(
        twin.create_design_loop_iteration(
            DesignLoopIteration(
                loop_id=loop_id,
                iteration_number=1,
                work_product_id=uuid4(),
                parameter_name="wall",
                parameter_value=2.0,
                metric="mass_kg",
                objective_value=0.4,
                feasible=True,
                is_winner=True,
            )
        )
    )
    return loop_id


def _all_kinds(twin: InMemoryTwinAPI) -> dict[str, str]:
    return {
        "gate": f"gate:{_gate()}",
        "tool": f"tool:{_tool()}",
        "change": f"change:{_change()}",
        "design_loop": f"design_loop:{_loop(twin)}",
        "sketch": f"sketch:{_wp(twin, WorkProductType.DESIGN_SKETCH)}",
        "drawing": f"drawing:{_wp(twin, WorkProductType.TECHNICAL_DRAWING)}",
    }


# ── list and detail ──────────────────────────────────────────────────────


def test_list_includes_one_of_each_kind(client: TestClient, twin: InMemoryTwinAPI) -> None:
    ids = _all_kinds(twin)
    body = client.get("/v1/approvals").json()
    assert {i["id"] for i in body["items"]} == set(ids.values())
    kinds = {i["id"].split(":")[0]: i["kind"] for i in body["items"]}
    assert kinds == {
        "gate": "gate",
        "tool": "tool_call",
        "change": "design_change",
        "design_loop": "design_loop",
        "sketch": "sketch",
        "drawing": "drawing",
    }
    assert all(i["status"] == "pending" and i["decidable"] for i in body["items"])
    assert body["unscoped_count"] == 0


def test_list_filters(client: TestClient, twin: InMemoryTwinAPI) -> None:
    _all_kinds(twin)
    only = client.get("/v1/approvals", params={"kind": "gate"}).json()["items"]
    assert [i["kind"] for i in only] == ["gate"]
    scoped = client.get("/v1/approvals", params={"project_id": "p1"}).json()
    assert {i["kind"] for i in scoped["items"]} == {"gate", "design_change"}
    assert scoped["unscoped_count"] == 4
    assert client.get("/v1/approvals", params={"status": "decided"}).json()["items"] == []


def test_gate_detail_and_allowed_decisions(client: TestClient) -> None:
    gate_id = _gate()
    item = client.get(f"/v1/approvals/gate:{gate_id}").json()
    assert item["allowed_decisions"] == ["approve", "reject", "retry", "rework"]
    assert item["rework_targets"] == ["intent", "needs", "requirements", "feasibility"]
    assert item["reason_required_for"] == ["reject", "retry", "rework"]
    assert item["detail"]["run_id"] == gate_id
    assert item["detail"]["phase"] == "design"
    assert item["detail"]["retries_left"] == 2
    assert item["project_id"] == "p1"


def test_not_ready_gate_has_structured_findings_and_no_approve(client: TestClient) -> None:
    item = client.get(f"/v1/approvals/gate:{_gate(ready=False)}").json()
    assert "approve" not in item["allowed_decisions"]
    kinds = [f["kind"] for f in item["findings"]]
    assert kinds == ["missing_deliverable", "constraint_violation"]
    assert all(f["severity"] == "error" for f in item["findings"])


def test_exhausted_retries_drop_retry(client: TestClient) -> None:
    item = client.get(f"/v1/approvals/gate:{_gate(retries_left=0)}").json()
    assert "retry" not in item["allowed_decisions"]


def test_findings_parse_from_reason() -> None:
    assert [f.kind for f in findings_from_reason(NOT_READY)] == [
        "missing_deliverable",
        "constraint_violation",
    ]
    assert findings_from_reason("Gate 'x': fine") == []


def test_tool_detail_and_flow_proposal_kind(client: TestClient) -> None:
    tool = client.get(f"/v1/approvals/tool:{_tool()}").json()
    assert tool["detail"] == {"tool": "twin.commit_geometry", "arguments": {"x": 1}}
    proposal = client.get(
        f"/v1/approvals/tool:{_tool(kind='design_flow_proposal', flow_version_id='fv1')}"
    ).json()
    assert proposal["kind"] == "flow_proposal"
    assert proposal["detail"]["flow_version_id"] == "fv1"


def test_change_detail(client: TestClient) -> None:
    item = client.get(f"/v1/approvals/change:{_change()}").json()
    assert item["detail"]["diff"] == {"wall": 3}
    assert len(item["detail"]["affected"]) == 1
    assert item["allowed_decisions"] == ["approve", "reject"]


def test_unknown_ids_404(client: TestClient) -> None:
    for bad in ("nope", "gate:run_missing", "tool:run_missing", "change:not-a-uuid", "sketch:x"):
        assert client.get(f"/v1/approvals/{bad}").status_code == 404
    assert (
        client.post("/v1/approvals/gate:run_missing/decision", json={"decision": "approve"})
    ).status_code == 404


# ── decide, per kind ─────────────────────────────────────────────────────


def test_decide_gate_approve_and_reject(client: TestClient) -> None:
    gate_id = _gate()
    resp = client.post(f"/v1/approvals/gate:{gate_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["decision"]["approver"] == "local:dashboard"
    second = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{second}/decision", json={"decision": "reject", "reason": "no"}
    )
    assert resp.json()["status"] == "rejected"
    assert resp.json()["decision"]["reason"] == "no"


def test_decide_gate_retry_and_rework(client: TestClient) -> None:
    retry_id = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{retry_id}/decision", json={"decision": "retry", "reason": "redo"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "retried"
    rework_id = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{rework_id}/decision",
        json={"decision": "rework", "reason": "back", "to_phase": "needs"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reworked"


def test_gate_refusals(client: TestClient) -> None:
    not_ready = _gate(ready=False)
    assert (
        client.post(f"/v1/approvals/gate:{not_ready}/decision", json={"decision": "approve"})
    ).status_code == 409
    no_retries = _gate(retries_left=0)
    assert (
        client.post(
            f"/v1/approvals/gate:{no_retries}/decision",
            json={"decision": "retry", "reason": "again"},
        )
    ).status_code == 409
    bad_target = _gate()
    assert (
        client.post(
            f"/v1/approvals/gate:{bad_target}/decision",
            json={"decision": "rework", "reason": "x", "to_phase": "simulation"},
        )
    ).status_code == 422
    no_reason = _gate()
    assert (
        client.post(f"/v1/approvals/gate:{no_reason}/decision", json={"decision": "reject"})
    ).status_code == 422


def test_already_decided_is_409(client: TestClient) -> None:
    gate_id = _gate()
    url = f"/v1/approvals/gate:{gate_id}/decision"
    assert client.post(url, json={"decision": "approve"}).status_code == 200
    assert client.post(url, json={"decision": "approve"}).status_code == 409


def test_decide_tool(client: TestClient) -> None:
    run_id = _tool()
    resp = client.post(f"/v1/approvals/tool:{run_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert tool_approvals.get_approval_store().get(run_id).approved_by == "local:dashboard"


def test_tool_retry_is_422(client: TestClient) -> None:
    resp = client.post(
        f"/v1/approvals/tool:{_tool()}/decision", json={"decision": "retry", "reason": "x"}
    )
    assert resp.status_code == 422


def test_elicitation_hold_listed_but_not_decidable(client: TestClient) -> None:
    run_id = _tool(route="elicitation")
    item = client.get(f"/v1/approvals/tool:{run_id}").json()
    assert item["decidable"] is False
    assert item["allowed_decisions"] == []
    assert "client's own approval prompt" in item["not_decidable_reason"]
    resp = client.post(f"/v1/approvals/tool:{run_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 409
    assert tool_approvals.get_approval_store().get(run_id).status is RunStatus.AWAITING_APPROVAL


def test_expired_tool_hold_is_not_decidable(client: TestClient) -> None:
    run_id = _tool()
    tool_approvals.get_approval_store().time_out(run_id, reason="late")
    item = client.get(f"/v1/approvals/tool:{run_id}").json()
    assert item["status"] == "expired"
    resp = client.post(f"/v1/approvals/tool:{run_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 409


def test_decide_change_records_reviewer_from_principal(client: TestClient) -> None:
    change_id = _change()
    resp = client.post(
        f"/v1/approvals/change:{change_id}/decision",
        json={"decision": "reject", "reason": "too thick", "reviewer": "mallory"},
        headers={"x-test-principal": "alice"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    proposal = assistant_routes.workflow.get_proposal(change_id)
    assert proposal is not None
    assert proposal.reviewer == "alice@example.com"


def test_change_retry_is_422(client: TestClient) -> None:
    resp = client.post(
        f"/v1/approvals/change:{_change()}/decision", json={"decision": "retry", "reason": "x"}
    )
    assert resp.status_code == 422


def test_decide_design_loop_sketch_drawing(client: TestClient, twin: InMemoryTwinAPI) -> None:
    ids = _all_kinds(twin)
    for kind in ("design_loop", "sketch", "drawing"):
        resp = client.post(
            f"/v1/approvals/{ids[kind]}/decision",
            json={"decision": "approve", "approved_by": "mallory"},
            headers={"x-test-principal": "bob"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"
        assert resp.json()["decision"]["approver"] == "bob@example.com"
    sketch = asyncio.run(twin.get_work_product(UUID(ids["sketch"].split(":")[1])))
    assert sketch is not None and sketch.metadata["approved_by"] == "bob@example.com"
    again = client.post(f"/v1/approvals/{ids['sketch']}/decision", json={"decision": "approve"})
    assert again.status_code == 409
    reject = client.post(
        f"/v1/approvals/{ids['drawing']}/decision", json={"decision": "reject", "reason": "x"}
    )
    assert reject.status_code == 409  # already approved; closed items are never decidable


def test_sketch_reject_is_422(client: TestClient, twin: InMemoryTwinAPI) -> None:
    resp = client.post(
        f"/v1/approvals/sketch:{_wp(twin, WorkProductType.DESIGN_SKETCH)}/decision",
        json={"decision": "reject", "reason": "x"},
    )
    assert resp.status_code == 422


# ── identity, surface, metrics ───────────────────────────────────────────


def test_surface_and_on_behalf_of_recorded(client: TestClient, metrics: _Recorder) -> None:
    gate_id = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{gate_id}/decision",
        json={"decision": "approve"},
        headers={
            "x-test-principal": "agent-7",
            "X-MetaForge-Surface": "agent",
            "X-MetaForge-On-Behalf-Of": "carol",
        },
    )
    record = resp.json()["decision"]
    assert record["surface"] == "agent"
    assert record["on_behalf_of"] == "carol"
    assert record["approver"] == "agent-7@example.com"
    assert record["approver_verified"] is True
    assert ("gate", "approve", "agent", "ok") in metrics.calls
    detail = client.get(f"/v1/approvals/gate:{gate_id}").json()
    assert detail["decision"]["surface"] == "agent"


def test_surface_defaults_to_unknown_and_bad_values_422(client: TestClient) -> None:
    ok = client.post(f"/v1/approvals/gate:{_gate()}/decision", json={"decision": "approve"})
    assert ok.json()["decision"]["surface"] == "unknown"
    bad = client.post(
        f"/v1/approvals/gate:{_gate()}/decision",
        json={"decision": "approve"},
        headers={"X-MetaForge-Surface": "toaster"},
    )
    assert bad.status_code == 422
    stray = client.post(
        f"/v1/approvals/gate:{_gate()}/decision",
        json={"decision": "approve"},
        headers={"X-MetaForge-Surface": "cli", "X-MetaForge-On-Behalf-Of": "carol"},
    )
    assert stray.status_code == 422


def test_refusals_are_counted(client: TestClient, metrics: _Recorder) -> None:
    client.post(
        f"/v1/approvals/gate:{_gate(ready=False)}/decision",
        json={"decision": "approve"},
        headers={"X-MetaForge-Surface": "cli"},
    )
    assert ("gate", "approve", "cli", "refused") in metrics.calls


def test_old_routes_still_work(client: TestClient) -> None:
    gate_id = _gate()
    legacy = client.app  # type: ignore[attr-defined]
    legacy.include_router(run_routes.router)
    legacy.include_router(tool_approvals.router)
    resp = client.post(f"/v1/runs/{gate_id}/approval", json={"decision": "approve"})
    assert resp.status_code == 200
    tool_id = _tool()
    resp = client.post(f"/v1/chat/tool_approvals/{tool_id}", json={"decision": "reject"})
    assert resp.status_code == 200
    items = {i["id"]: i for i in client.get("/v1/approvals?status=decided").json()["items"]}
    assert items[f"gate:{gate_id}"]["status"] == "approved"
    assert items[f"tool:{tool_id}"]["status"] == "rejected"


# ── persistence of surface and on-behalf-of (FORGE-507 review) ────────────

AGENT_HEADERS = {
    "x-test-principal": "agent-7",
    "X-MetaForge-Surface": "agent",
    "X-MetaForge-On-Behalf-Of": "carol",
}


def test_tool_decision_survives_a_ledger_reload(client: TestClient, tmp_path: Path) -> None:
    tool_approvals.init_approval_ledger(SqliteRunLedger(str(tmp_path / "tools.db")))
    run_id = _tool()
    resp = client.post(
        f"/v1/approvals/tool:{run_id}/decision", json={"decision": "approve"}, headers=AGENT_HEADERS
    )
    assert resp.status_code == 200
    # A restart: a fresh store rehydrated from the same ledger file.
    tool_approvals.reset_approval_store()
    tool_approvals.init_approval_ledger(SqliteRunLedger(str(tmp_path / "tools.db")))
    record = client.get(f"/v1/approvals/tool:{run_id}").json()["decision"]
    assert record["surface"] == "agent"
    assert record["on_behalf_of"] == "carol"
    assert record["approver"] == "agent-7@example.com"
    assert record["approver_verified"] is True


def test_gate_decision_survives_a_ledger_reload(client: TestClient, tmp_path: Path) -> None:
    run_routes.init_run_ledger(SqliteRunLedger(str(tmp_path / "runs.db")))
    gate_id = _gate()
    resp = client.post(
        f"/v1/approvals/gate:{gate_id}/decision",
        json={"decision": "approve", "reason": "fine"},
        headers=AGENT_HEADERS,
    )
    assert resp.status_code == 200
    run_routes.reset_run_store()
    run_routes.init_run_ledger(SqliteRunLedger(str(tmp_path / "runs.db")))
    run = run_routes.get_run_store().get(gate_id)
    logged = run.request["decisions"][-1]
    assert (logged["surface"], logged["on_behalf_of"], logged["reason"]) == (
        "agent",
        "carol",
        "fine",
    )


def test_refused_decision_leaves_no_decision_entry(client: TestClient) -> None:
    gate_id = _gate(ready=False)
    client.post(f"/v1/approvals/gate:{gate_id}/decision", json={"decision": "approve"})
    assert "decisions" not in run_routes.get_run_store().get(gate_id).request


def test_twin_and_proposal_records_carry_surface(client: TestClient, twin: InMemoryTwinAPI) -> None:
    ids = _all_kinds(twin)
    for kind in ("change", "design_loop", "sketch", "drawing"):
        body = {"decision": "approve"}
        resp = client.post(f"/v1/approvals/{ids[kind]}/decision", json=body, headers=AGENT_HEADERS)
        assert resp.status_code == 200, resp.text
    change = assistant_routes.workflow.get_proposal(UUID(ids["change"].split(":")[1]))
    assert change is not None
    assert (change.decision_surface, change.decision_on_behalf_of) == ("agent", "carol")
    sketch = asyncio.run(twin.get_work_product(UUID(ids["sketch"].split(":")[1])))
    assert sketch is not None
    assert sketch.metadata["approval_surface"] == "agent"
    assert sketch.metadata["approval_on_behalf_of"] == "carol"
    iterations = asyncio.run(
        twin.list_design_loop_iterations(UUID(ids["design_loop"].split(":")[1]))
    )
    assert iterations[0].approval_surface == "agent"
    # A fresh read, with no state kept by the API itself, reports the same.
    for kind in ("change", "design_loop", "sketch", "drawing"):
        record = client.get(f"/v1/approvals/{ids[kind]}").json()["decision"]
        assert (record["surface"], record["on_behalf_of"]) == ("agent", "carol"), kind
        assert record["approver_verified"] is True
