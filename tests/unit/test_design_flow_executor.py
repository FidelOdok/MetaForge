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


class FakeEvaluator:
    """A `GateEvaluator` double returning a fixed set of present types."""

    def __init__(self, present: set[str]) -> None:
        self._present = present

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]:
        return set(self._present)


def _register_flow(
    flow_id: str,
    *,
    gated: bool = True,
    auto: bool = False,
    required: tuple[str, ...] = (),
    enforce: bool = True,
) -> str:
    def gate(name: str) -> Gate | None:
        return Gate(name=name, auto_approve=auto) if gated else None

    def phase(pid: str, title: str, obj: str, gname: str) -> Phase:
        return Phase(
            id=pid,
            title=title,
            objective=obj,
            required_deliverables=required,
            enforce_deliverables=enforce,
            gate=gate(gname),
        )

    FLOWS[flow_id] = FlowDefinition(
        id=flow_id,
        name=flow_id,
        phases=(
            phase("requirements", "Requirements", "x", "g1"),
            phase("design", "Design", "y", "g2"),
            phase("simulation", "Simulation", "z", "g3"),
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


@pytest.mark.asyncio
async def test_gate_proceeds_when_required_deliverable_present() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_ready", required=("cad_model",))
    run = store.create({"goal": "g", "flow": flow_id, "project_id": "p1"})

    executor = DesignFlowExecutor(
        store=store,
        brain=brain,
        coordinator=coord,
        gate_evaluator=FakeEvaluator({"cad_model", "design_decision"}),
    )
    task = asyncio.create_task(executor.run(run.id))

    for _ in range(3):  # deliverable present -> each gate pauses for the human
        await _wait_status(store, run.id, RunStatus.AWAITING_APPROVAL)
        store.submit_approval(run.id, ApprovalDecision.APPROVE)

    await asyncio.wait_for(task, timeout=2.0)
    assert store.get(run.id).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_gate_fails_when_required_deliverable_missing() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_missing", required=("cad_model",))
    run = store.create({"goal": "g", "flow": flow_id, "project_id": "p1"})

    # Evaluator reports the required cad_model is absent -> gate must not pass.
    executor = DesignFlowExecutor(
        store=store,
        brain=brain,
        coordinator=coord,
        gate_evaluator=FakeEvaluator({"design_decision"}),
    )
    await asyncio.wait_for(executor.run(run.id), timeout=2.0)

    run_ = store.get(run.id)
    assert run_.status is RunStatus.FAILED
    assert "cad_model" in (run_.error or "")
    assert brain.seen == ["requirements"]  # failed at the first gate


@pytest.mark.asyncio
async def test_missing_deliverable_not_enforced_still_pauses() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = ScriptedBrain()
    flow_id = _register_flow("test_soft", required=("cad_model",), enforce=False)
    run = store.create({"goal": "g", "flow": flow_id, "project_id": "p1"})

    executor = DesignFlowExecutor(
        store=store,
        brain=brain,
        coordinator=coord,
        gate_evaluator=FakeEvaluator(set()),  # nothing present
    )
    task = asyncio.create_task(executor.run(run.id))

    # Not enforced -> the gate still pauses for the human despite the gap.
    await _wait_status(store, run.id, RunStatus.AWAITING_APPROVAL)
    store.submit_approval(run.id, ApprovalDecision.REJECT)
    await asyncio.wait_for(task, timeout=2.0)
    assert store.get(run.id).status is RunStatus.REJECTED
