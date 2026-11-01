"""Graph flows on the in-process engine (FORGE-539).

Conditions skip phases, phases run in dependency order, and a rework re-runs
only the target and what depends on it. Same harness as
``test_design_flow_rework``: a scripted brain, the in-memory run store, and
decisions driven the way the approval route drives them.
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
from orchestrator.design_flow.spec import FlowDefinition, Gate, Phase
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus


class RecordingBrain:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.retry_feedback))
        return PhaseOutcome(summary=f"did {phase.id}", status="completed")


def _gated(pid: str, **kw: object) -> Phase:
    return Phase(id=pid, title=pid, objective=pid, gate=Gate(name=f"{pid} gate"), **kw)  # type: ignore[arg-type]


def _diamond(**verify_kw: object) -> FlowDefinition:
    return FlowDefinition(
        id="diamond",
        name="diamond",
        phases=(
            _gated("req"),
            _gated("mech", depends_on=("req",)),
            _gated("elec", depends_on=("req",)),
            _gated("verify", depends_on=("mech", "elec"), **verify_kw),
        ),
    )


async def _awaiting(store: InMemoryRunStore, run_id: str, *, after: int) -> None:
    for _ in range(400):
        run = store.get(run_id)
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > after:
            return
        if run.is_terminal:
            raise AssertionError(f"run ended: {run.status} {run.error}")
        await asyncio.sleep(0.005)
    raise AssertionError(f"never awaiting approval: {store.get(run_id).status}")


async def _approve_all(store: InMemoryRunStore, run_id: str, gates: int, *, start: int = 0) -> None:
    for n in range(start, start + gates):
        await _awaiting(store, run_id, after=n)
        store.submit_approval(run_id, ApprovalDecision.APPROVE)


def _setup(facts: dict[str, str] | None = None):
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = RecordingBrain()
    run = store.create({"goal": "g", "flow": "diamond", "project_id": "p1"})
    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord)
    return coord, store, brain, run, executor


@pytest.mark.asyncio
async def test_phases_run_in_dependency_order() -> None:
    coord, store, brain, run, executor = _setup()
    task = asyncio.create_task(executor.run(run.id, _diamond()))
    await _approve_all(store, run.id, 4)
    await asyncio.wait_for(task, 2)
    assert [c[0] for c in brain.calls] == ["req", "mech", "elec", "verify"]
    assert store.get(run.id).status is RunStatus.COMPLETED
    assert store.get(run.id).result["skipped"] == []  # type: ignore[index]


@pytest.mark.asyncio
async def test_a_false_condition_skips_the_phase_and_releases_dependents() -> None:
    flow = FlowDefinition(
        id="diamond",
        name="diamond",
        phases=(
            _gated("req"),
            _gated("route_selection", depends_on=("req",), condition="route == undecided"),
            _gated("design", depends_on=("req", "route_selection")),
        ),
    )
    coord, store, brain, run, executor = _setup()
    task = asyncio.create_task(executor.run(run.id, flow, facts={"route": "in_house"}))
    await _approve_all(store, run.id, 2)
    await asyncio.wait_for(task, 2)
    assert [c[0] for c in brain.calls] == ["req", "design"]
    result = store.get(run.id).result
    assert result["skipped"] == ["route_selection"]  # type: ignore[index]
    assert [p["id"] for p in result["phases"]] == ["req", "design"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_a_true_condition_runs_the_phase() -> None:
    flow = FlowDefinition(
        id="diamond",
        name="diamond",
        phases=(
            _gated("req"),
            _gated("route_selection", condition="route == undecided"),
        ),
    )
    coord, store, brain, run, executor = _setup()
    task = asyncio.create_task(executor.run(run.id, flow, facts={"route": "undecided"}))
    await _approve_all(store, run.id, 2)
    await asyncio.wait_for(task, 2)
    assert [c[0] for c in brain.calls] == ["req", "route_selection"]


@pytest.mark.asyncio
async def test_rework_reruns_only_the_target_and_its_dependents() -> None:
    # verify's gate sends the run back to mech. mech and verify run again;
    # req and elec keep their results and approvals.
    coord, store, brain, run, executor = _setup()
    task = asyncio.create_task(executor.run(run.id, _diamond()))
    await _approve_all(store, run.id, 3)  # req, mech, elec
    await _awaiting(store, run.id, after=3)  # verify
    coord.note_rework(run.id, "mech", "safety factor too low")
    store.submit_approval(run.id, ApprovalDecision.REWORK)
    await _approve_all(store, run.id, 2, start=4)  # mech, verify again
    await asyncio.wait_for(task, 2)

    assert [c[0] for c in brain.calls] == ["req", "mech", "elec", "verify", "mech", "verify"]
    reworked = brain.calls[4]
    assert reworked[0] == "mech" and "safety factor too low" in reworked[1]
    result = store.get(run.id).result
    assert sorted(p["id"] for p in result["phases"]) == ["elec", "mech", "req", "verify"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_rework_to_a_phase_this_one_does_not_depend_on_is_refused() -> None:
    # elec does not depend on mech, so elec's gate cannot send the run to mech.
    coord, store, brain, run, executor = _setup()
    task = asyncio.create_task(executor.run(run.id, _diamond()))
    await _approve_all(store, run.id, 2)  # req, mech
    await _awaiting(store, run.id, after=2)  # elec
    coord.note_rework(run.id, "mech", "")
    store.submit_approval(run.id, ApprovalDecision.REWORK)
    await asyncio.wait_for(task, 2)
    run_after = store.get(run.id)
    assert run_after.status is RunStatus.FAILED
    assert "not earlier" in (run_after.error or "") or "not part" in (run_after.error or "")


@pytest.mark.asyncio
async def test_a_linear_flow_still_reruns_every_later_phase() -> None:
    # The old behaviour, untouched: a straight line has no independent phases.
    flow = FlowDefinition(
        id="diamond", name="diamond", phases=(_gated("a"), _gated("b"), _gated("c"))
    )
    coord, store, brain, run, executor = _setup()
    task = asyncio.create_task(executor.run(run.id, flow))
    await _approve_all(store, run.id, 2)
    await _awaiting(store, run.id, after=2)
    coord.note_rework(run.id, "a", "")
    store.submit_approval(run.id, ApprovalDecision.REWORK)
    await _approve_all(store, run.id, 3, start=3)
    await asyncio.wait_for(task, 2)
    assert [c[0] for c in brain.calls] == ["a", "b", "c", "a", "b", "c"]
