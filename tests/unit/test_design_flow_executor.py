"""Design-flow executor: phase sequencing + gate pause/resume (MET-10).

Network- and LLM-free: the brain is a scripted double, the run store is the
in-memory state machine, and approvals are driven from the test to simulate the
``POST /v1/runs/{id}/approval`` route firing the store's transition observer.
Test flows are registered into the real ``FLOWS`` registry so the executor's
own ``get_flow`` lookup is exercised.
"""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Gate, Phase
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus


class ScriptedBrain:
    """Records the phases it was asked to run and returns canned outcomes."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.seen.append(phase.id)
        return PhaseOutcome(
            summary=f"did {phase.id} for '{goal}'",
            artifacts=[f"{phase.id}-artifact"],
            status="completed",
        )


def _register_flow(flow_id: str, *, gated: bool = True, auto: bool = False) -> str:
    def gate(name: str) -> Gate | None:
        return Gate(name=name, auto_approve=auto) if gated else None

    FLOWS[flow_id] = FlowDefinition(
        id=flow_id,
        name=flow_id,
        phases=(
            Phase(id="requirements", title="Requirements", objective="x", gate=gate("g1")),
            Phase(id="design", title="Design", objective="y", gate=gate("g2")),
            Phase(id="simulation", title="Simulation", objective="z", gate=gate("g3")),
        ),
    )
    return flow_id


@pytest.fixture(autouse=True)
def _clean_registry() -> object:
    before = dict(FLOWS)
    yield
    FLOWS.clear()
    FLOWS.update(before)


async def _wait_status(store: InMemoryRunStore, run_id: str, status: RunStatus) -> None:
    """Poll until the run reaches ``status`` (bounded, so a hang fails the test)."""
    for _ in range(200):
        if store.get(run_id).status is status:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(
        f"run '{run_id}' never reached {status}, stuck at {store.get(run_id).status}"
    )


@pytest.mark.asyncio
async def test_flow_pauses_at_each_gate_and_completes_on_approve() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_gated")
    run = store.create({"goal": "build a bracket", "flow": flow_id})

    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord)
    task = asyncio.create_task(executor.run(run.id))

    for _ in range(3):  # three gates
        await _wait_status(store, run.id, RunStatus.AWAITING_APPROVAL)
        store.submit_approval(run.id, ApprovalDecision.APPROVE)

    await asyncio.wait_for(task, timeout=2.0)

    assert brain.seen == ["requirements", "design", "simulation"]
    final = store.get(run.id)
    assert final.status is RunStatus.COMPLETED
    assert final.result is not None
    assert [p["id"] for p in final.result["phases"]] == [
        "requirements",
        "design",
        "simulation",
    ]


@pytest.mark.asyncio
async def test_reject_at_first_gate_ends_run_and_skips_later_phases() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_reject")
    run = store.create({"goal": "build a bracket", "flow": flow_id})

    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord)
    task = asyncio.create_task(executor.run(run.id))

    await _wait_status(store, run.id, RunStatus.AWAITING_APPROVAL)
    store.submit_approval(run.id, ApprovalDecision.REJECT)

    await asyncio.wait_for(task, timeout=2.0)

    assert brain.seen == ["requirements"]  # design/simulation never ran
    assert store.get(run.id).status is RunStatus.REJECTED


@pytest.mark.asyncio
async def test_auto_approve_gates_run_straight_through() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_auto", auto=True)
    run = store.create({"goal": "g", "flow": flow_id})

    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord)
    await asyncio.wait_for(executor.run(run.id), timeout=2.0)

    assert brain.seen == ["requirements", "design", "simulation"]
    assert store.get(run.id).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_ungated_flow_completes_without_pausing() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_ungated", gated=False)
    run = store.create({"goal": "g", "flow": flow_id})

    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord)
    await asyncio.wait_for(executor.run(run.id), timeout=2.0)

    assert store.get(run.id).status is RunStatus.COMPLETED
