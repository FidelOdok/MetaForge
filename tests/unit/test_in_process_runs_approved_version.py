"""Both engines run exactly the approved flow version (FORGE-474).

The in-process executor looked the run's ``flow`` id up in the built-in
catalogue, so a run started on an approved, tailored version (``POST
/v1/runs`` with ``flow_version_id``, which is what ``flow.start_run`` sends)
walked the default template instead: a dropped phase came back, an added
deliverable was never required. Only the Temporal path ran the frozen version.

These drive the real ``/v1/runs`` route on the in-process engine, with the
phase brain and gate checks replaced by doubles that record what they were
asked to do, and approve every gate until the run completes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import api_gateway.runs.routes as routes
from orchestrator.design_flow.executor import DesignFlowExecutor, PhaseOutcome
from orchestrator.design_flow.generator import Operation, OperationKind, build_proposal
from orchestrator.design_flow.spec import get_flow
from orchestrator.design_flow.templates import load_templates
from orchestrator.design_flow.versions import (
    FlowVersion,
    get_version_store,
    reset_version_store,
)

ADDED = ("design", "test_plan")


class _RecordingBrain:
    """Does no work; remembers every phase it was asked to run, in order."""

    def __init__(self) -> None:
        self.phases: list[Any] = []

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> PhaseOutcome:
        self.phases.append(phase)
        return PhaseOutcome(summary=f"{phase.id} done")


class _RecordingLauncher:
    """Stands in for Temporal: keeps the frozen flow it was asked to start."""

    def __init__(self) -> None:
        self.flows: list[Any] = []

    async def require_worker(self) -> None:
        return None

    async def start(self, *, flow: Any, **_: Any) -> str:
        self.flows.append(flow)
        return "wf"

    async def state(self, run_id: str) -> dict[str, Any]:
        raise RuntimeError("no worker")


@pytest.fixture(autouse=True)
def _fresh_stores() -> Any:
    routes.reset_run_store()
    reset_version_store()
    yield
    routes.reset_run_store()
    reset_version_store()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")


@pytest.fixture
def brain(monkeypatch: pytest.MonkeyPatch) -> _RecordingBrain:
    """The in-process engine with doubles for the brain and gate checks."""
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
    recording = _RecordingBrain()

    async def _brain(run_id: str = "worker", flow_id: str | None = None) -> _RecordingBrain:
        return recording

    def _executor(brain: Any, project_backend: Any) -> DesignFlowExecutor:
        return DesignFlowExecutor(
            store=routes.get_run_store(),
            brain=brain,
            coordinator=routes.get_gate_coordinator(),
        )

    monkeypatch.setattr(routes, "build_phase_brain", _brain)
    monkeypatch.setattr(routes, "_build_in_process_executor", _executor)
    return recording


def _approved_tailored_version() -> FlowVersion:
    """hardware_v1 with firmware dropped and a deliverable added, approved."""
    proposal = build_proposal(
        get_flow("hardware_v1"),
        base_version=load_templates()["hardware_v1"].version,
        operations=[
            Operation(OperationKind.DROP_PHASE, "firmware", "no firmware here"),
            Operation(OperationKind.ADD_DELIVERABLE, ADDED[0], "plan the test", ADDED[1]),
        ],
        intent="a passive enclosure",
    )
    assert proposal.validation.ok, proposal.validation
    store = get_version_store()
    version = store.save(
        proposal.definition,
        base_template_id="hardware_v1",
        base_version=proposal.base_version,
        changes=[],
    )
    return store.decide(version.id, approved=True, decided_by="reviewer")


async def _start(gateway: httpx.AsyncClient, version_id: str) -> httpx.Response:
    return await gateway.post(
        "/v1/runs",
        json={
            "request": {
                "kind": "design_flow",
                "flow_version_id": version_id,
                "goal": "a passive enclosure",
                "project_id": "proj-474",
            },
            "start": True,
        },
    )


async def _approve_until_done(gateway: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    for _ in range(400):
        run: dict[str, Any] = (await gateway.get(f"/v1/runs/{run_id}")).json()
        if run["status"] in {"completed", "failed", "rejected", "canceled"}:
            return run
        if run["status"] == "awaiting_approval":
            answered = await gateway.post(
                f"/v1/runs/{run_id}/approval", json={"decision": "approve"}
            )
            assert answered.status_code == 200, answered.text
        await asyncio.sleep(0.005)
    raise AssertionError(f"run {run_id} never finished")


class TestTheInProcessEngineRunsTheApprovedVersion:
    async def test_it_walks_the_versions_phases_not_the_templates(
        self, gateway: httpx.AsyncClient, brain: _RecordingBrain
    ) -> None:
        version = _approved_tailored_version()
        approved_phases = [p.id for p in version.definition.phases]
        template_phases = [p.id for p in get_flow("hardware_v1").phases]
        assert "firmware" in template_phases and "firmware" not in approved_phases

        started = await _start(gateway, version.id)
        assert started.status_code == 201, started.text
        run = await _approve_until_done(gateway, started.json()["id"])

        assert run["status"] == "completed", run
        assert [p.id for p in brain.phases] == approved_phases
        assert [p["id"] for p in run["result"]["phases"]] == approved_phases

    async def test_the_added_deliverable_is_required(
        self, gateway: httpx.AsyncClient, brain: _RecordingBrain
    ) -> None:
        version = _approved_tailored_version()
        template_design = next(p for p in get_flow("hardware_v1").phases if p.id == ADDED[0])
        assert ADDED[1] not in template_design.required_deliverables

        started = await _start(gateway, version.id)
        await _approve_until_done(gateway, started.json()["id"])

        design = next(p for p in brain.phases if p.id == ADDED[0])
        assert ADDED[1] in design.required_deliverables

    async def test_the_run_records_the_version_hash_and_engine(
        self, gateway: httpx.AsyncClient, brain: _RecordingBrain
    ) -> None:
        version = _approved_tailored_version()

        body = (await _start(gateway, version.id)).json()

        assert body["engine"] == "in_process"
        assert body["flow_version_id"] == version.id
        assert body["flow_content_hash"] == version.frozen.content_hash
        assert body["request"]["flow_template_id"] == "hardware_v1"
        assert body["request"]["flow_version"] == version.frozen.version
        await _approve_until_done(gateway, body["id"])

    async def test_the_live_view_shows_the_versions_phases(
        self, gateway: httpx.AsyncClient, brain: _RecordingBrain
    ) -> None:
        version = _approved_tailored_version()
        body = (await _start(gateway, version.id)).json()

        state = (await gateway.get(f"/v1/runs/{body['id']}/flow-state")).json()

        assert [p["id"] for p in state["phases"]] == [p.id for p in version.definition.phases]
        await _approve_until_done(gateway, body["id"])

    async def test_a_version_it_cannot_run_exactly_is_refused_not_substituted(
        self, gateway: httpx.AsyncClient, brain: _RecordingBrain
    ) -> None:
        version = _approved_tailored_version()
        # Edited after approval: the frozen content no longer matches its hash.
        version.frozen.phases[0].objective = "something nobody approved"

        refused = await _start(gateway, version.id)

        assert refused.status_code == 409, refused.text
        assert "not substituted" in refused.text
        assert (await gateway.get("/v1/runs")).json()["runs"] == []
        assert brain.phases == []

    async def test_a_template_run_records_its_engine_and_hash(
        self, gateway: httpx.AsyncClient, brain: _RecordingBrain
    ) -> None:
        body = (
            await gateway.post(
                "/v1/runs",
                json={
                    "request": {"flow": "mech_v1", "goal": "a bracket", "project_id": "p"},
                    "start": True,
                },
            )
        ).json()

        assert body["engine"] == "in_process"
        assert body["flow_version_id"] is None
        assert body["flow_content_hash"]
        run = await _approve_until_done(gateway, body["id"])
        assert [p["id"] for p in run["result"]["phases"]] == [
            p.id for p in get_flow("mech_v1").phases
        ]


class TestTheTemporalPathIsUnchanged:
    async def test_it_hands_temporal_the_frozen_version(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("METAFORGE_FLOW_ENGINE", raising=False)
        launcher = _RecordingLauncher()

        async def _launcher() -> _RecordingLauncher:
            return launcher

        monkeypatch.setattr(routes, "get_flow_launcher", _launcher)
        version = _approved_tailored_version()

        body = (await _start(gateway, version.id)).json()

        assert launcher.flows == [version.frozen]
        assert body["engine"] == "temporal"
        assert body["flow_version_id"] == version.id
        assert body["flow_content_hash"] == version.frozen.content_hash
