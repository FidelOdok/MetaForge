"""Repairs that stop improving are flagged, then stopped (FORGE-573)."""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.design_flow.executor import (
    ConstraintReport,
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.rework import (
    DEFAULT_STALL_STOP,
    STALL_STOP_ENV,
    findings_streak,
    stall_note,
    stall_stop,
)
from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Gate, Phase
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus


class TestStreak:
    def test_counts_identical_trailing_verdicts_order_free(self) -> None:
        assert findings_streak([]) == 0
        assert findings_streak([("a",)]) == 1
        assert findings_streak([("a", "b"), ("b", "a")]) == 2
        assert findings_streak([("a",), ("b",), ("b",)]) == 2
        assert findings_streak([("a",), ("a",), ("b",)]) == 1

    def test_stop_threshold_is_configurable_but_at_least_two(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert stall_stop() == DEFAULT_STALL_STOP == 3
        monkeypatch.setenv(STALL_STOP_ENV, "1")
        assert stall_stop() == 2
        monkeypatch.setenv(STALL_STOP_ENV, "x")
        assert stall_stop() == DEFAULT_STALL_STOP

    def test_notes(self) -> None:
        assert stall_note("sim", 2, 3).startswith("NO IMPROVEMENT")
        assert "stopped" in stall_note("sim", 3, 3)


class SameViolation:
    """The constraint engine finds the same violation however often it is asked."""

    async def check(self, project_id: str | None, since_ts: float = 0.0) -> ConstraintReport:
        return ConstraintReport(
            checked=True, passed=False, evaluated_count=1, violations=["FoS 1.6 < 2"]
        )


class Brain:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.ran.append(phase.id)
        return PhaseOutcome(summary=f"{phase.id} #{len(self.ran)}", status="completed")


@pytest.fixture(autouse=True)
def _clean_flows() -> object:
    before = dict(FLOWS)
    yield
    FLOWS.clear()
    FLOWS.update(before)


async def _wait_gate(store: InMemoryRunStore, run_id: str, seen: int) -> bool:
    for _ in range(800):
        run = store.get(run_id)
        if run.is_terminal:
            return False
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > seen:
            return True
        await asyncio.sleep(0.005)
    raise AssertionError(store.get(run_id).status)


@pytest.mark.asyncio
async def test_identical_findings_are_flagged_then_stop_the_run_before_the_cap() -> None:
    FLOWS["stall"] = FlowDefinition(
        id="stall",
        name="stall",
        phases=(
            Phase(id="vv", title="V&V", objective="o", gate=Gate("g", enforce_constraints=True)),
        ),
    )
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = Brain()
    run = store.create({"goal": "g", "flow": "stall"})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, constraint_checker=SameViolation()
    )
    task = asyncio.create_task(executor.run(run.id))

    stalled: list[bool] = []
    seen = 0
    while await _wait_gate(store, run.id, seen):
        seen += 1
        state = coord.gate_state(run.id) or {}
        stalled.append(bool(state.get("stalled")))
        if state.get("stalled"):
            assert "NO IMPROVEMENT" in (store.get(run.id).approval_reason or "")
        coord.note_retry(run.id, "try again")
        store.submit_approval(run.id, ApprovalDecision.RETRY)
    await asyncio.wait_for(task, timeout=5.0)

    final = store.get(run.id)
    assert final.status is RunStatus.FAILED
    assert "not converging" in (final.error or "")
    assert stalled == [False, True]  # flagged on the 2nd identical verdict
    assert brain.ran == ["vv", "vv", "vv"]  # stopped at the 3rd, under the 3-retry cap
