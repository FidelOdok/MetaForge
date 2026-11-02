"""The run supervisor (FORGE-734..739).

It drives a design run to a verified result: watches it, lets gates be
reviewed and decided within the approval policy, restarts a failed or
unverified run with an amended brief, and stops for a person on a platform
issue, a used budget, or a run a person ended. The chair run on the sandbox is
the case these cases come from.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals.policy import PolicyStore, reset_policy_store
from api_gateway.runs import routes as run_routes
from api_gateway.runs import supervisor
from api_gateway.runs.supervisor_routes import router as supervisor_router
from mcp_core.guardrails import SERVICE_REFUSED_PREFIXES
from tool_registry.tools.design_flow.adapter import DesignFlowServer

PROJECT = "p1"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
    run_routes.reset_run_store()
    reset_policy_store(PolicyStore())
    created: list[dict[str, Any]] = []

    async def fake_create_run(body: Any) -> Any:
        store = run_routes.get_run_store()
        run = store.create(body.request)
        store.start(run.id)
        created.append(body.request)
        return SimpleNamespace(id=run.id)

    monkeypatch.setattr(run_routes, "create_run", fake_create_run)
    yield created
    run_routes.reset_run_store()
    reset_policy_store(None)


def _run(**request: Any) -> str:
    store = run_routes.get_run_store()
    run = store.create(
        {"kind": "design_flow", "flow": "mech_v1", "goal": "a chair", "project_id": PROJECT}
        | request
    )
    store.start(run.id)
    return run.id


def _tick(run_id: str) -> dict[str, Any]:
    return asyncio.run(supervisor.supervise_tick(run_id)) or {}


def _sup(run_id: str) -> dict[str, Any]:
    return supervisor.supervision_of(run_routes.get_run_store().get(run_id)) or {}


# ── diagnosis ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kw", "kind"),
    [
        ({"error": "Phase 'route_selection' stopped [failure class: design]"}, "design"),
        ({"error": "connection refused to freecad-adapter"}, "tooling"),
        (
            {
                "findings": ["records no material"],
                "gate_ready": False,
                "review": {"recommendation": "approve"},
            },
            "platform",
        ),
        (
            {
                "findings": ["moment_arm: 600 mm > 500 mm"],
                "finding_history": [
                    ["moment_arm: 600 mm > 500 mm"],
                    ["moment_arm: 600 mm > 500 mm"],
                ],
            },
            "platform",
        ),
        (
            {"findings": ["records no material"], "finding_history": [["records no material"]]},
            "design",
        ),
    ],
)
def test_diagnosis(kw: dict[str, Any], kind: str) -> None:
    assert supervisor.diagnose(**kw)["kind"] == kind


# ── the loop ────────────────────────────────────────────────────────────────


def test_a_running_run_is_watched() -> None:
    run_id = _run()
    supervisor.start_supervision(run_id)
    assert _tick(run_id)["status"] == "watching"
    assert _sup(run_id)["budgets"]["max_restarts"] == 3


def test_a_design_failure_restarts_with_an_amended_brief(_fresh: list[dict]) -> None:
    run_id = _run(decisions=[{"decision": "retry"}], gate_reviews=[{"reasons": "Add a material."}])
    supervisor.start_supervision(run_id)
    run_routes.get_run_store().fail(run_id, "Phase 'design' stopped [failure class: design]")
    _tick(run_id)
    old = _sup(run_id)
    assert old["status"] == "handed_over" and old["active"] is False
    new_request = _fresh[0]
    assert new_request["supervised_from"] == run_id
    assert "Add a material." in new_request["flow_context"]
    assert "decisions" not in new_request and "gate_reviews" not in new_request
    new = _sup(old["next_run"])
    assert new["active"] and new["chain"] == [run_id, old["next_run"]]
    assert new["counters"]["restarts"] == 1


def test_a_tooling_failure_pauses_before_restarting(
    _fresh: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _run()
    supervisor.start_supervision(run_id)
    run_routes.get_run_store().fail(run_id, "connection refused to freecad-adapter")
    _tick(run_id)
    assert _fresh == [] and _sup(run_id)["pause_until"] > 0
    monkeypatch.setattr(supervisor, "TOOLING_PAUSE_SECONDS", 0)
    sup = _sup(run_id)
    sup["pause_until"] = 0
    run_routes.get_run_store().annotate(run_id, "supervision", sup)
    _tick(run_id)
    assert len(_fresh) == 1


def test_the_restart_budget_stops_it() -> None:
    run_id = _run()
    supervisor.start_supervision(run_id, budgets={"max_restarts": 0})
    run_routes.get_run_store().fail(run_id, "[failure class: design] no material")
    _tick(run_id)
    sup = _sup(run_id)
    assert sup["status"] == "stopped" and "restart budget" in sup["reason"]


def test_a_verified_run_ends_the_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    async def lifecycle(run_id: str) -> Any:
        return SimpleNamespace(lifecycle={"completion": {"classification": "COMPLETED_VERIFIED"}})

    monkeypatch.setattr(run_routes, "get_run_lifecycle", lifecycle)
    run_id = _run()
    supervisor.start_supervision(run_id)
    run_routes.get_run_store().complete(run_id)
    _tick(run_id)
    assert _sup(run_id)["status"] == "verified"


def test_an_unverified_completion_restarts_with_the_gaps(
    _fresh: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def lifecycle(run_id: str) -> Any:
        completion = {
            "classification": "PARTIALLY_COMPLETED",
            "unmet_requirements": [{"id": "REQ-3", "status": "NOT_EVALUATED"}],
        }
        return SimpleNamespace(lifecycle={"completion": completion})

    monkeypatch.setattr(run_routes, "get_run_lifecycle", lifecycle)
    run_id = _run()
    supervisor.start_supervision(run_id)
    run_routes.get_run_store().complete(run_id)
    _tick(run_id)
    assert "REQ-3" in _fresh[0]["flow_context"]
    assert _sup(run_id)["status"] == "handed_over"


def test_a_run_a_person_ended_stops_it() -> None:
    run_id = _run()
    supervisor.start_supervision(run_id)
    run_routes.get_run_store().cancel(run_id)
    _tick(run_id)
    assert _sup(run_id)["status"] == "stopped"


def test_a_gate_whose_check_is_wrong_stops_for_a_person() -> None:
    store = run_routes.get_run_store()
    run_id = _run()
    run_routes.get_gate_coordinator().set_gate_state(
        run_id, ready=False, retries_left=2, phase="design", reworks_left=1
    )
    store.request_approval(
        run_id, reason="[G6] NOT READY (retry the phase or reject): records no material"
    )
    store.annotate(
        run_id,
        "gate_reviews",
        [{"phase": "design", "recommendation": "approve", "confidence": 0.9}],
    )
    supervisor.start_supervision(run_id)
    _tick(run_id)
    sup = _sup(run_id)
    assert sup["status"] == "stopped" and "platform" in sup["reason"]


def test_a_gate_left_to_a_person_is_reported_once() -> None:
    store = run_routes.get_run_store()
    run_id = _run()
    run_routes.get_gate_coordinator().set_gate_state(
        run_id, ready=True, retries_left=2, phase="design", reworks_left=1
    )
    store.request_approval(run_id, reason="")
    supervisor.start_supervision(run_id)
    _tick(run_id)
    _tick(run_id)
    sup = _sup(run_id)
    assert sup["status"] == "waiting_for_person"
    assert [e["action"] for e in sup["log"]].count("gate") == 1


# ── surfaces ────────────────────────────────────────────────────────────────


def test_the_routes_start_read_and_stop() -> None:
    app = FastAPI()
    app.include_router(supervisor_router)
    client = TestClient(app)
    run_id = _run()
    started = client.post(f"/v1/runs/{run_id}/supervise", json={"max_restarts": 1}).json()
    assert started["active"] and started["budgets"]["max_restarts"] == 1
    assert client.get(f"/v1/runs/{run_id}/supervision").json()["status"] == "watching"
    stopped = client.post(f"/v1/runs/{run_id}/supervise/stop", json={"reason": "enough"}).json()
    assert stopped["active"] is False and stopped["reason"] == "enough"
    assert client.get("/v1/runs/run_missing/supervision").status_code == 404


def test_flow_supervise_is_a_client_tool_the_worker_cannot_call() -> None:
    class _Sup:
        async def status(self, run_id: str) -> dict:
            return {"active": True}

    srv = DesignFlowServer(supervisor=_Sup())
    assert "flow.supervise" in srv.tool_ids
    assert any("flow.supervise".startswith(p) for p in SERVICE_REFUSED_PREFIXES)
