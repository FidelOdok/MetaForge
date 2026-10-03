"""Sending a design-flow run back to an earlier phase, in-process engine (FORGE-500).

Same shape as ``test_design_flow_retry``: scripted brain, in-memory run store,
decisions driven from the test the way the approval route drives them.
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
from orchestrator.design_flow.rework import (
    DEFAULT_MAX_REWORK_CYCLES,
    MAX_REWORK_CYCLES_ENV,
    build_rework_feedback,
    max_rework_cycles,
    rework_target_error,
)
from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Gate, Phase
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus


class RecordingBrain:
    """Records (phase, feedback) for every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.retry_feedback))
        return PhaseOutcome(summary=f"did {phase.id} #{len(self.calls)}", status="completed")


class ViolatingChecker:
    """A constraint checker that reports one violation at every gate."""

    async def check(self, project_id: str | None, since_ts: float = 0.0):  # type: ignore[no-untyped-def]
        from orchestrator.design_flow.executor import ConstraintReport

        return ConstraintReport(
            checked=True,
            passed=False,
            evaluated_count=1,
            violations=["safety factor 1.56 < 2"],
        )


@pytest.fixture(autouse=True)
def _clean_registry() -> object:
    before = dict(FLOWS)
    yield
    FLOWS.clear()
    FLOWS.update(before)


def _flow(flow_id: str) -> str:
    FLOWS[flow_id] = FlowDefinition(
        id=flow_id,
        name=flow_id,
        phases=(
            Phase(id="intent", title="Intent", objective="a", gate=Gate(name="g1")),
            Phase(id="design", title="Design", objective="b", gate=Gate(name="g2")),
            Phase(
                id="verify",
                title="Verify",
                objective="c",
                gate=Gate(name="g3", enforce_constraints=True),
            ),
        ),
    )
    return flow_id


async def _wait_awaiting(store: InMemoryRunStore, run_id: str, *, after: int = 0) -> None:
    for _ in range(400):
        run = store.get(run_id)
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > after:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never awaiting approval: {store.get(run_id).status}")


def _approve(coord: GateCoordinator, store: InMemoryRunStore, run_id: str) -> None:
    store.submit_approval(run_id, ApprovalDecision.APPROVE)


def _rework(
    coord: GateCoordinator, store: InMemoryRunStore, run_id: str, to_phase: str, reason: str = ""
) -> None:
    coord.note_rework(run_id, to_phase, reason)
    store.submit_approval(run_id, ApprovalDecision.REWORK)


def _setup(flow_id: str, *, checker: object | None = None):
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = RecordingBrain()
    run = store.create({"goal": "g", "flow": flow_id, "project_id": "p1"})
    executor = DesignFlowExecutor(
        store=store,
        brain=brain,
        coordinator=coord,
        constraint_checker=checker,  # type: ignore[arg-type]
    )
    return coord, store, brain, run, executor


@pytest.mark.asyncio
async def test_rework_from_the_last_gate_reruns_target_and_later_phases() -> None:
    coord, store, brain, run, executor = _setup(_flow("rw_a"), checker=ViolatingChecker())
    task = asyncio.create_task(executor.run(run.id))

    await _wait_awaiting(store, run.id)  # gate 1
    _approve(coord, store, run.id)
    await _wait_awaiting(store, run.id, after=1)  # gate 2
    _approve(coord, store, run.id)
    await _wait_awaiting(store, run.id, after=2)  # gate 3 (verify), NOT ready
    assert "NOT READY" in (store.get(run.id).approval_reason or "")
    state = coord.gate_state(run.id)
    assert state is not None and state["phase"] == "verify" and state["reworks_left"] == 3
    _rework(coord, store, run.id, "design", "thicken the arm")

    # Design runs again, and its gate re-opens; so does verify's.
    await _wait_awaiting(store, run.id, after=3)
    _approve(coord, store, run.id)
    await _wait_awaiting(store, run.id, after=4)
    _approve(coord, store, run.id)  # the engine-level approve; the route refuses it when not ready
    await asyncio.wait_for(task, timeout=3.0)

    # intent ran once (kept); design, verify ran twice.
    assert [c[0] for c in brain.calls] == ["intent", "design", "verify", "design", "verify"]
    feedback = brain.calls[3][1]
    assert feedback.startswith("REWORK")
    assert "safety factor 1.56 < 2" in feedback  # the gate's findings
    assert "did verify #3" in feedback  # the failing phase's summary
    assert "thicken the arm" in feedback  # the reviewer's reason
    assert brain.calls[4][1] == ""  # feedback goes to the target phase only
    assert brain.calls[0][1] == "" and brain.calls[1][1] == "" and brain.calls[2][1] == ""


@pytest.mark.asyncio
async def test_rework_keeps_earlier_outcomes_and_drops_the_rest() -> None:
    coord, store, brain, run, executor = _setup(_flow("rw_b"))
    task = asyncio.create_task(executor.run(run.id))
    await _wait_awaiting(store, run.id)
    _approve(coord, store, run.id)
    await _wait_awaiting(store, run.id, after=1)
    _rework(coord, store, run.id, "intent", "scope changed")
    # Intent is the target, so its gate is asked again.
    await _wait_awaiting(store, run.id, after=2)
    for n in (2, 3):
        _approve(coord, store, run.id)
        await _wait_awaiting(store, run.id, after=n)
    _approve(coord, store, run.id)
    await asyncio.wait_for(task, timeout=3.0)

    assert [c[0] for c in brain.calls] == ["intent", "design", "intent", "design", "verify"]
    phases = store.get(run.id).result["phases"]  # type: ignore[index]
    assert [p["id"] for p in phases] == ["intent", "design", "verify"]
    assert store.get(run.id).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_rework_cap_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_REWORK_CYCLES_ENV, "1")
    coord, store, brain, run, executor = _setup(_flow("rw_cap"))
    task = asyncio.create_task(executor.run(run.id))
    await _wait_awaiting(store, run.id)
    _approve(coord, store, run.id)
    await _wait_awaiting(store, run.id, after=1)
    _rework(coord, store, run.id, "intent", "first")
    await _wait_awaiting(store, run.id, after=2)  # intent's gate again
    _approve(coord, store, run.id)
    await _wait_awaiting(store, run.id, after=3)  # design's gate again
    state = coord.gate_state(run.id)
    assert state is not None and state["reworks_left"] == 0
    _rework(coord, store, run.id, "intent", "second")
    await asyncio.wait_for(task, timeout=3.0)

    run_ = store.get(run.id)
    assert run_.status is RunStatus.FAILED
    assert "rework cycle" in (run_.error or "")


@pytest.mark.asyncio
async def test_an_invalid_target_fails_closed_in_the_engine() -> None:
    coord, store, brain, run, executor = _setup(_flow("rw_bad"))
    task = asyncio.create_task(executor.run(run.id))
    await _wait_awaiting(store, run.id)
    _rework(coord, store, run.id, "verify", "forward is not rework")
    await asyncio.wait_for(task, timeout=3.0)
    assert store.get(run.id).status is RunStatus.FAILED
    assert "not earlier" in (store.get(run.id).error or "")


def test_target_validation_names_the_reason() -> None:
    ids = ["intent", "design", "verify"]
    assert rework_target_error(ids, "verify", "design") is None
    assert rework_target_error(ids, None, "design") is None
    assert "needs 'to_phase'" in (rework_target_error(ids, "verify", " ") or "")
    assert "not part of this run's flow" in (rework_target_error(ids, "verify", "nope") or "")
    assert "not earlier" in (rework_target_error(ids, "design", "verify") or "")
    same = rework_target_error(ids, "design", "design") or ""
    assert "not earlier" in same and "retry" in same
    first = rework_target_error(ids, "intent", "intent") or ""
    assert "this is the first phase" in first


def test_rework_cap_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MAX_REWORK_CYCLES_ENV, raising=False)
    assert max_rework_cycles() == DEFAULT_MAX_REWORK_CYCLES == 3
    monkeypatch.setenv(MAX_REWORK_CYCLES_ENV, "5")
    assert max_rework_cycles() == 5
    monkeypatch.setenv(MAX_REWORK_CYCLES_ENV, "junk")
    assert max_rework_cycles() == 3


def test_rework_feedback_leads_with_the_instruction_then_findings_summary_reason() -> None:
    text = build_rework_feedback(
        from_phase="verify",
        to_phase="design",
        findings=["sf 1.56 < 2"],
        reason="use a thicker wall",
        from_summary="ran FEA",
        cycle=2,
    )
    assert text.startswith("REWORK (cycle 2)")
    assert text.index("sf 1.56 < 2") < text.index("ran FEA") < text.index("use a thicker wall")
