"""FORGE-485: a gate can be answered through the gateway after it restarts.

Real Temporal, real launcher, real routes. The gateway restart is simulated by
building a fresh run store from the ledger, which only remembers ``queued``,
while the workflow keeps waiting at its gate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("temporalio")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402

from api_gateway.runs.routes import (  # noqa: E402
    get_run_store,
    init_run_ledger,
    reset_run_store,
    router,
    set_flow_launcher,
)
from orchestrator.design_flow.frozen import FrozenFlow, FrozenGate, FrozenPhase  # noqa: E402
from orchestrator.design_flow.launcher import DesignFlowLauncher  # noqa: E402
from orchestrator.design_flow.temporal_activities import DesignFlowActivities  # noqa: E402
from orchestrator.design_flow.temporal_flow import (  # noqa: E402
    GateCheck,
    PhaseRequest,
    PhaseResult,
)
from orchestrator.design_flow.worker import build_design_flow_worker  # noqa: E402
from orchestrator.harness.ledger import SqliteRunLedger  # noqa: E402
from orchestrator.harness.runs import RunStatus  # noqa: E402

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _flow() -> FrozenFlow:
    phases = [
        FrozenPhase(
            id=f"phase{i}",
            title=f"Phase {i}",
            objective="x",
            enforce_deliverables=False,
            gate=FrozenGate(name="Intent sign-off") if i == 0 else None,
        )
        for i in range(2)
    ]
    flow = FrozenFlow(template_id="test_v1", name="Test", phases=phases)
    flow.content_hash = flow.compute_hash()
    return flow


async def _phase(request: PhaseRequest) -> PhaseResult:
    return PhaseResult(summary=f"did {request.phase.id}", artifacts=[])


async def _gate(payload: dict) -> GateCheck:
    return GateCheck(ready=True, checked=True, constraints_checked=True, reason="all good")


async def _announce(run_id: str, gate: str, reason: str) -> None:
    return None


@pytest.fixture
async def env():
    environment = await WorkflowEnvironment.start_local()
    try:
        yield environment
    finally:
        await environment.shutdown()


async def _wait_for_gate(launcher: DesignFlowLauncher, run_id: str) -> None:
    import asyncio

    for _ in range(100):
        if (await launcher.state(run_id)).get("awaiting_gate"):
            return
        await asyncio.sleep(0.2)
    raise AssertionError("workflow never reached its gate")


@pytest.mark.parametrize(("decision", "final"), [("approve", "completed"), ("reject", "rejected")])
async def test_gate_answerable_after_gateway_restart(
    env, monkeypatch: pytest.MonkeyPatch, decision: str, final: str
) -> None:
    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "temporal")
    acts = DesignFlowActivities(phase_runner=_phase, gate_checker=_gate, gate_announcer=_announce)
    launcher = DesignFlowLauncher(client=env.client)
    ledger = SqliteRunLedger(":memory:")

    reset_run_store()
    init_run_ledger(ledger)
    run_id = get_run_store().create({"flow": "test_v1", "kind": "design_flow"}).id

    async with build_design_flow_worker(env.client, acts):
        await launcher.start(run_id=run_id, goal="g", flow=_flow())
        await _wait_for_gate(launcher, run_id)

        # Gateway restart: a fresh store rebuilt from the ledger says `queued`.
        reset_run_store()
        init_run_ledger(ledger)
        assert get_run_store().get(run_id).status is RunStatus.QUEUED
        set_flow_launcher(launcher)

        app = FastAPI()
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            shown = (await http.get(f"/v1/runs/{run_id}")).json()
            assert shown["status"] == "awaiting_approval"
            assert "Intent sign-off" in shown["approval_reason"]
            assert "all good" in shown["approval_reason"]

            # A second restart before anyone answers: still answerable without a GET first.
            reset_run_store()
            init_run_ledger(ledger)
            answered = await http.post(f"/v1/runs/{run_id}/approval", json={"decision": decision})
            assert answered.status_code == 200, answered.text
            assert answered.json()["approved_by"]

        result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()

    assert result["status"] == final
    set_flow_launcher(None)
    reset_run_store()


async def test_record_follows_the_workflow_without_any_restart(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FORGE-485 scope update: `running` once started, `awaiting_approval` at the gate."""
    from api_gateway.runs import routes

    monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "temporal")
    acts = DesignFlowActivities(phase_runner=_phase, gate_checker=_gate, gate_announcer=_announce)
    launcher = DesignFlowLauncher(client=env.client)

    async def start(run_id: str) -> None:
        await launcher.start(run_id=run_id, goal="g", flow=_flow())

    monkeypatch.setattr(routes, "_start_on_temporal", start)
    reset_run_store()
    set_flow_launcher(launcher)

    async with build_design_flow_worker(env.client, acts):
        app = FastAPI()
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
            created = await http.post(
                "/v1/runs", json={"request": {"flow": "test_v1", "kind": "design_flow"}}
            )
            assert created.status_code == 201, created.text
            assert created.json()["status"] == "running"
            run_id = created.json()["id"]

            await _wait_for_gate(launcher, run_id)
            at_gate = (await http.get(f"/v1/runs/{run_id}")).json()
            assert at_gate["status"] == "awaiting_approval"
            assert "Intent sign-off" in at_gate["approval_reason"]

            await http.post(f"/v1/runs/{run_id}/approval", json={"decision": "approve"})
            result = await env.client.get_workflow_handle(f"design-flow-{run_id}").result()
            assert result["status"] == "completed"
            done = (await http.get(f"/v1/runs/{run_id}")).json()
            assert done["status"] == "completed"

    set_flow_launcher(None)
    reset_run_store()
