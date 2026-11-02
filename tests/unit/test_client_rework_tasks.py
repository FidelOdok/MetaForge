"""A reworked client-mode phase gets a task of its own (FORGE-742).

A rework restarts a phase's attempts at 1, and the client task id was
``run:phase:attempt``. The re-run therefore asked for the id of a task that
was submitted before the rework; ``open_task`` returns a submitted task as it
is (that keeps a submission across an activity retry), so the phase finished
in 50 ms on the old work, no task was opened for the client, and the gate
reopened unchanged on every answer. The chair run deadlocked this way.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.client_tasks import routes as task_routes
from api_gateway.runs import routes as run_routes
from api_gateway.runs.client_phase import (
    ClientPhaseBrain,
    LocalTaskChannel,
    run_client_phase_request,
    task_from_request,
)
from orchestrator.design_flow.client_tasks import PhaseTask, SqliteClientTaskStore, task_id_for
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.frozen import FrozenGate, FrozenPhase
from orchestrator.design_flow.spec import Phase
from orchestrator.design_flow.temporal_flow import PhaseRequest


def _design() -> FrozenPhase:
    return FrozenPhase(
        id="design",
        title="Design",
        objective="Model the chair",
        required_deliverables=["cad_model"],
        gate=FrozenGate(name="design_review"),
    )


def _request(attempt: int, rework_cycle: int) -> PhaseRequest:
    return PhaseRequest(
        run_id="run-1",
        goal="a chair",
        phase=_design(),
        project_id="p-chair",
        attempt=attempt,
        rework_cycle=rework_cycle,
        intelligence="client",
    )


class _SubmitsWhenAsked(LocalTaskChannel):
    """The client: claims and submits whatever task is open, on the first poll."""

    def __init__(self, store: SqliteClientTaskStore, summary: str) -> None:
        super().__init__(store)
        self.store = store
        self.summary = summary
        self.opened: list[str] = []

    async def open(self, task: PhaseTask) -> PhaseTask:
        self.opened.append(task.id)
        return await super().open(task)

    async def get(self, task_id: str) -> PhaseTask:
        if self.store.get(task_id).status == "open":
            self.store.submit(task_id, "claude-ai", self.summary, ["cad-2"])
        return await super().get(task_id)


async def _no_sleep(_: float) -> None:
    return None


def test_the_id_is_unchanged_until_the_run_is_reworked() -> None:
    assert task_id_for("run-1", "design", 2) == "run-1:design:2"
    assert task_id_for("run-1", "design", 2, 0) == "run-1:design:2"
    assert task_id_for("run-1", "design", 2, 1) == "run-1:design:2.r1"
    assert task_id_for("run-1", "design", 1, 2) != task_id_for("run-1", "design", 1, 1)


def test_a_reworked_phase_opens_a_new_task_rather_than_reusing_the_submitted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METAFORGE_CLIENT_TASK_POLL_SECONDS", "0.05")
    store = SqliteClientTaskStore()
    # Before the rework: attempt 2 of the design phase was done and submitted.
    before = task_from_request(_request(attempt=2, rework_cycle=0))
    store.open_task(before)
    store.submit(before.id, "claude-ai", "the first design", ["cad-1"])

    # The rework runs the phase again from attempt 1, then a retry makes it 2.
    channel = _SubmitsWhenAsked(store, "the reworked design")
    result = asyncio.run(run_client_phase_request(channel, _request(attempt=2, rework_cycle=1)))

    assert channel.opened == ["run-1:design:2.r1"]
    assert result.summary == "the reworked design"
    assert result.artifacts == ["cad-2"]
    # The work from before the rework is still on record, untouched.
    assert store.get("run-1:design:2").summary == "the first design"


def test_an_activity_retry_still_keeps_its_submission() -> None:
    # The reason open_task returns a submitted task: same request, same task.
    store = SqliteClientTaskStore()
    task = task_from_request(_request(attempt=1, rework_cycle=1))
    store.open_task(task)
    store.submit(task.id, "claude-ai", "done before the worker restarted", ["cad-3"])
    result = asyncio.run(
        run_client_phase_request(LocalTaskChannel(store), _request(attempt=1, rework_cycle=1))
    )
    assert result.summary == "done before the worker restarted"


def test_the_brief_tells_the_client_which_rework_this_is() -> None:
    assert task_from_request(_request(attempt=1, rework_cycle=2)).brief["rework_cycle"] == 2


def test_the_in_process_brain_uses_the_rework_cycle() -> None:
    store = SqliteClientTaskStore()
    channel = _SubmitsWhenAsked(store, "reworked in process")
    brain = ClientPhaseBrain("run-1", channel)
    context = FlowContext(goal="a chair", attempt=1, rework_cycle=1)
    outcome = asyncio.run(
        brain.run_phase(
            goal="a chair",
            phase=Phase(id="design", title="Design", objective="Model it"),
            context=context,
        )
    )
    assert outcome.summary == "reworked in process"
    assert channel.opened == ["run-1:design:1.r1"]


# ── the route the Temporal worker posts to ──────────────────────────────────


@pytest.fixture
def api() -> Any:
    run_routes.reset_run_store()
    task_routes.init_client_task_store(SqliteClientTaskStore())
    app = FastAPI()
    app.include_router(task_routes.router)
    yield TestClient(app)
    task_routes.init_client_task_store(None)
    run_routes.reset_run_store()


def test_the_route_opens_a_separate_task_for_a_reworked_attempt(api: TestClient) -> None:
    run = run_routes.get_run_store().create({"kind": "design_flow", "intelligence": "client"})
    body = {"runId": run.id, "phaseId": "design", "attempt": 1, "brief": {}}
    first = api.post("/v1/client-tasks", json=body).json()
    api.post(f"/v1/client-tasks/{first['id']}/claim", json={"client": "claude-ai"})
    api.post(
        f"/v1/client-tasks/{first['id']}/submit",
        json={"summary": "first", "artifacts": [], "client": "claude-ai"},
    )

    again = api.post("/v1/client-tasks", json={**body, "reworkCycle": 1}).json()

    assert again["id"] == f"{run.id}:design:1.r1"
    assert again["status"] == "open"
    assert api.get(f"/v1/client-tasks/{first['id']}").json()["status"] == "submitted"
