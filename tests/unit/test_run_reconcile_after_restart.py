"""A run parked at a gate can be answered after a gateway restart (FORGE-485).

The ledger only remembers ``queued`` for a restored run, so the record said
``queued`` while the workflow waited at its gate, and answering it returned
409. The workflow is the authority; the record must follow it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.runs import routes
from api_gateway.runs.routes import (
    get_run_store,
    init_run_ledger,
    reconcile_live_runs,
    reset_run_store,
    router,
    set_flow_launcher,
)
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import RunStatus

GATE_STATE = {
    "status": "awaiting_approval",
    "current_phase": "intent",
    "awaiting_gate": "Intent sign-off",
    "gate_reason": "3 deliverable type(s) recorded",
    "completed": [],
    "error": None,
}


class FakeLauncher:
    def __init__(self, state: dict[str, Any] | None = None, *, down: bool = False) -> None:
        self.current = dict(state or GATE_STATE)
        self.down = down
        self.answers: list[tuple[str, bool, str]] = []
        self.retries: list[tuple[bool, str]] = []

    async def state(self, run_id: str) -> dict[str, Any]:
        if self.down:
            raise RuntimeError("no worker")
        return dict(self.current)

    async def answer_gate(
        self,
        run_id: str,
        *,
        approved: bool,
        decided_by: str,
        comment: str = "",
        retry: bool = False,
    ) -> None:
        self.answers.append((run_id, approved, decided_by))
        self.retries.append((retry, comment))
        self.current = {
            **self.current,
            "status": "running" if (approved or retry) else "rejected",
        }
        self.current["awaiting_gate"] = None


@pytest.fixture
def restarted(monkeypatch: pytest.MonkeyPatch):
    """A run created, ledgered as queued, then a fresh store restored from it."""
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "temporal")
    ledger = SqliteRunLedger(":memory:")
    reset_run_store()
    init_run_ledger(ledger)
    run = get_run_store().create({"flow": "mech_v1", "kind": "design_flow", "goal": "g"})
    run_id = run.id
    # The gateway restarts: a brand new store, rebuilt from the ledger.
    reset_run_store()
    init_run_ledger(ledger)
    assert get_run_store().get(run_id).status is RunStatus.QUEUED
    app = FastAPI()
    app.include_router(router)
    yield run_id, TestClient(app)
    set_flow_launcher(None)
    reset_run_store()


def test_get_reports_the_gate_the_workflow_is_waiting_at(restarted) -> None:
    run_id, client = restarted
    set_flow_launcher(FakeLauncher())  # type: ignore[arg-type]
    body = client.get(f"/v1/runs/{run_id}").json()
    assert body["status"] == "awaiting_approval"
    assert "Intent sign-off" in body["approval_reason"]
    assert "3 deliverable type(s) recorded" in body["approval_reason"]


@pytest.mark.parametrize(("decision", "approved"), [("approve", True), ("reject", False)])
def test_a_gate_can_be_answered_after_restart(restarted, decision: str, approved: bool) -> None:
    run_id, client = restarted
    launcher = FakeLauncher()
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": decision})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == ("running" if approved else "rejected")
    assert resp.json()["approved_by"]
    assert [(r, a) for r, a, _ in launcher.answers] == [(run_id, approved)]
    assert launcher.answers[0][2], "the approver identity is relayed to the workflow"


def test_startup_reconcile_updates_live_runs(restarted) -> None:
    import asyncio

    run_id, _ = restarted
    set_flow_launcher(FakeLauncher())  # type: ignore[arg-type]
    assert asyncio.run(reconcile_live_runs()) == 1
    assert get_run_store().get(run_id).status is RunStatus.AWAITING_APPROVAL


def test_unreachable_workflow_leaves_the_record_alone(restarted) -> None:
    run_id, client = restarted
    set_flow_launcher(FakeLauncher(down=True))  # type: ignore[arg-type]
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "queued"
    # And the approval still refuses rather than signalling a gate nobody saw.
    assert (
        client.post(f"/v1/runs/{run_id}/approval", json={"decision": "approve"}).status_code == 409
    )


def test_reconcile_never_reopens_a_terminal_run(restarted) -> None:
    run_id, client = restarted
    store = get_run_store()
    store.restore(
        store.get(run_id).__class__(**{**vars(store.get(run_id)), "status": RunStatus.COMPLETED})
    )
    set_flow_launcher(FakeLauncher())  # type: ignore[arg-type]
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "completed"


def test_in_process_runs_are_not_reconciled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "temporal")
    reset_run_store()
    run = get_run_store().create({"flow": "mech_v1", "flow_engine": "in_process"})
    set_flow_launcher(FakeLauncher())  # type: ignore[arg-type]
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get(f"/v1/runs/{run.id}").json()["status"] == "queued"
    set_flow_launcher(None)
    reset_run_store()
    assert routes._launcher is None


# ── FORGE-495: retry decision ────────────────────────────────────────────


def test_retry_is_signalled_with_the_reviewers_reason(restarted) -> None:
    run_id, client = restarted
    launcher = FakeLauncher({**GATE_STATE, "gate_ready": True, "retries_left": 3})
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(
        f"/v1/runs/{run_id}/approval", json={"decision": "retry", "reason": "add the cad model"}
    )
    assert resp.status_code == 200, resp.text
    assert launcher.retries == [(True, "add the cad model")]


def test_approving_a_gate_that_is_not_ready_is_refused(restarted) -> None:
    run_id, client = restarted
    launcher = FakeLauncher({**GATE_STATE, "gate_ready": False, "retries_left": 3})
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": "approve"})
    assert resp.status_code == 409
    assert "not ready" in resp.json()["detail"]
    assert launcher.answers == []
    # The run is still parked and answerable: retry goes through.
    assert client.post(f"/v1/runs/{run_id}/approval", json={"decision": "retry"}).status_code == 200


def test_retry_past_the_cap_is_refused(restarted) -> None:
    run_id, client = restarted
    launcher = FakeLauncher({**GATE_STATE, "gate_ready": True, "retries_left": 0})
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": "retry"})
    assert resp.status_code == 409
    assert launcher.answers == []


def test_retry_on_a_plain_run_is_refused() -> None:
    reset_run_store()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    run = get_run_store().create({"goal": "g"})
    resp = client.post(f"/v1/runs/{run.id}/approval", json={"decision": "retry"})
    assert resp.status_code == 422
    reset_run_store()


# ── FORGE-500: rework decision ───────────────────────────────────────────

REWORK_STATE = {
    **GATE_STATE,
    "current_phase": "simulation",
    "gate_ready": False,
    "retries_left": 3,
    "reworks_left": 3,
}


class ReworkLauncher(FakeLauncher):
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        super().__init__(state)
        self.reworks: list[tuple[str, str]] = []

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        return []

    async def answer_gate(  # type: ignore[override]
        self,
        run_id: str,
        *,
        approved: bool,
        decided_by: str,
        comment: str = "",
        retry: bool = False,
        rework_to: str = "",
    ) -> None:
        await super().answer_gate(
            run_id, approved=approved, decided_by=decided_by, comment=comment, retry=retry
        )
        self.reworks.append((rework_to, comment))
        self.current["status"] = "running"


def test_rework_is_signalled_with_target_and_reason_even_at_a_not_ready_gate(restarted) -> None:
    run_id, client = restarted
    launcher = ReworkLauncher(REWORK_STATE)
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(
        f"/v1/runs/{run_id}/approval",
        json={"decision": "rework", "to_phase": "design", "reason": "thicken the arm"},
    )
    assert resp.status_code == 200, resp.text
    assert launcher.reworks == [("design", "thicken the arm")]


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({"decision": "rework"}, "needs 'to_phase'"),
        ({"decision": "rework", "to_phase": "nope"}, "not part of this run's flow"),
        ({"decision": "rework", "to_phase": "simulation"}, "not earlier"),
    ],
)
def test_rework_to_an_invalid_phase_is_422_with_a_named_reason(
    restarted, body: dict[str, str], needle: str
) -> None:
    run_id, client = restarted
    launcher = ReworkLauncher(REWORK_STATE)
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(f"/v1/runs/{run_id}/approval", json=body)
    assert resp.status_code == 422
    assert needle in resp.json()["detail"]
    assert launcher.reworks == []


def test_rework_past_the_cap_is_refused(restarted) -> None:
    run_id, client = restarted
    launcher = ReworkLauncher({**REWORK_STATE, "reworks_left": 0})
    set_flow_launcher(launcher)  # type: ignore[arg-type]
    resp = client.post(
        f"/v1/runs/{run_id}/approval", json={"decision": "rework", "to_phase": "design"}
    )
    assert resp.status_code == 409
    assert "rework cycles" in resp.json()["detail"]
    assert launcher.reworks == []


def test_rework_on_a_plain_run_is_refused() -> None:
    reset_run_store()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    run = get_run_store().create({"goal": "g"})
    resp = client.post(
        f"/v1/runs/{run.id}/approval", json={"decision": "rework", "to_phase": "design"}
    )
    assert resp.status_code == 422
    reset_run_store()


def test_flow_state_exposes_rework_cycles(restarted) -> None:
    run_id, client = restarted
    set_flow_launcher(ReworkLauncher({**REWORK_STATE, "rework_cycles": 1, "max_rework_cycles": 3}))  # type: ignore[arg-type]
    state = client.get(f"/v1/runs/{run_id}/flow-state").json()
    assert state["reworkCycles"] == 1
    assert state["maxReworkCycles"] == 3
    assert state["reworksLeft"] == 3
