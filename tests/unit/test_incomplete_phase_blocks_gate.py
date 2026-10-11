"""FORGE-590: a phase that reports its objective was not met blocks its gate."""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.frozen import FrozenGate, FrozenPhase
from orchestrator.design_flow.grounding import INCOMPLETE_STATUS
from orchestrator.design_flow.spec import FLOWS, Phase
from orchestrator.design_flow.temporal_flow import DesignFlowWorkflow, GateCheck
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus
from tests.unit.test_design_flow_executor import (
    FakeEvaluator,
    ScriptedBrain,
    _register_flow,
    _wait_status,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> object:
    # The in-process test registers a flow; leave the catalogue as found.
    before = dict(FLOWS)
    yield
    FLOWS.clear()
    FLOWS.update(before)


def _phase(enforce: bool = True) -> FrozenPhase:
    return FrozenPhase(
        id="route",
        title="Route selection",
        objective="pick a route",
        required_deliverables=["design_decision"],
        enforce_deliverables=enforce,
        gate=FrozenGate(name="route_gate"),
    )


@pytest.mark.parametrize("enforce", [True, False])
def test_temporal_gate_reads_not_ready_for_an_incomplete_phase(enforce: bool) -> None:
    phase = _phase(enforce)
    check = GateCheck(ready=True, checked=True, present=["design_decision"])
    entry = {"phase": "route", "summary": "objective NOT met", "status": INCOMPLETE_STATUS}
    findings, blocking = DesignFlowWorkflow._gate_findings_for(phase, phase.gate, check, entry)
    assert blocking
    assert any("objective was not met" in f for f in findings)


def test_temporal_gate_is_unchanged_for_a_completed_phase() -> None:
    phase = _phase()
    check = GateCheck(ready=True, checked=True, present=["design_decision"])
    entry = {"phase": "route", "summary": "done", "status": "completed"}
    findings, blocking = DesignFlowWorkflow._gate_findings_for(phase, phase.gate, check, entry)
    assert not blocking
    assert findings == []


class _IncompleteBrain(ScriptedBrain):
    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        out = await super().run_phase(goal=goal, phase=phase, context=context)
        out.status = INCOMPLETE_STATUS
        return out


@pytest.mark.asyncio
async def test_in_process_gate_reads_not_ready_for_an_incomplete_phase() -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    flow_id = _register_flow("test_incomplete", required=("cad_model",))
    run = store.create({"goal": "g", "flow": flow_id, "project_id": "p1"})
    executor = DesignFlowExecutor(
        store=store,
        brain=_IncompleteBrain(),
        coordinator=coord,
        gate_evaluator=FakeEvaluator({"cad_model"}),
    )
    task = asyncio.create_task(executor.run(run.id))
    await _wait_status(store, run.id, RunStatus.AWAITING_APPROVAL)
    reason = store.get(run.id).approval_reason or ""
    assert "NOT READY" in reason
    assert "objective was not met" in reason
    store.submit_approval(run.id, ApprovalDecision.REJECT)
    await asyncio.wait_for(task, timeout=2.0)
