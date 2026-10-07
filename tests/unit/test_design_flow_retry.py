"""Retrying a design-flow phase from its gate, in-process engine (FORGE-495).

Same shape as ``test_design_flow_executor``: scripted brain, in-memory run
store, decisions driven from the test the way the approval route drives them.
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
from orchestrator.design_flow.retry import (
    DEFAULT_MAX_PHASE_RETRIES,
    MAX_PHASE_RETRIES_ENV,
    build_retry_feedback,
    max_phase_retries,
)
from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Gate, Phase
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus


class RecordingBrain:
    """Records (phase, attempt, feedback) for every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.attempt, context.retry_feedback))
        return PhaseOutcome(summary=f"did {phase.id} #{context.attempt}", status="completed")


class ScriptedEvaluator:
    """Reports ``present`` from a per-call script (last entry repeats)."""

    def __init__(self, script: list[set[str]]) -> None:
        self._script = script
        self.calls = 0

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]:
        out = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return set(out)


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
            Phase(id="one", title="One", objective="a", gate=Gate(name="g1")),
            Phase(
                id="two",
                title="Two",
                objective="b",
                required_deliverables=("cad_model",),
                enforce_deliverables=True,
                gate=Gate(name="g2"),
            ),
            Phase(id="three", title="Three", objective="c", gate=Gate(name="g3")),
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


def _decide(
    coord: GateCoordinator,
    store: InMemoryRunStore,
    run_id: str,
    decision: ApprovalDecision,
    reason: str = "",
) -> None:
    if decision is ApprovalDecision.RETRY:
        coord.note_retry(run_id, reason)
    store.submit_approval(run_id, decision)


def _setup(flow_id: str, evaluator: ScriptedEvaluator | None = None):
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = RecordingBrain()
    run = store.create({"goal": "g", "flow": flow_id, "project_id": "p1"})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, gate_evaluator=evaluator
    )
    return coord, store, brain, run, executor


@pytest.mark.asyncio
async def test_retry_reruns_only_that_phase_with_findings_and_keeps_earlier_approvals() -> None:
    evaluator = ScriptedEvaluator([set(), {"cad_model"}])  # missing, then present
    coord, store, brain, run, executor = _setup(_flow("retry_a"), evaluator)
    task = asyncio.create_task(executor.run(run.id))

    await _wait_awaiting(store, run.id)  # gate one (ready)
    _decide(coord, store, run.id, ApprovalDecision.APPROVE)

    await _wait_awaiting(store, run.id, after=1)  # gate two, NOT ready
    assert "NOT READY" in (store.get(run.id).approval_reason or "")
    state = coord.gate_state(run.id)
    assert state is not None and state["ready"] is False and state["retries_left"] == 3
    _decide(coord, store, run.id, ApprovalDecision.RETRY, "record the cad model")

    await _wait_awaiting(store, run.id, after=2)  # gate two again, now ready
    assert "NOT READY" not in (store.get(run.id).approval_reason or "")
    _decide(coord, store, run.id, ApprovalDecision.APPROVE)

    await _wait_awaiting(store, run.id, after=3)  # gate three
    _decide(coord, store, run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=3.0)

    assert store.get(run.id).status is RunStatus.COMPLETED
    # Phase one ran once (kept), phase two twice, phase three once.
    assert [c[0] for c in brain.calls] == ["one", "two", "two", "three"]
    assert brain.calls[0][2] == "" and brain.calls[1][2] == ""
    attempt, feedback = brain.calls[2][1], brain.calls[2][2]
    assert attempt == 2
    assert "cad_model" in feedback  # the gate's finding
    assert "record the cad model" in feedback  # the reviewer's reason
    assert feedback.startswith("RETRY")
    assert brain.calls[3][2] == ""  # feedback does not leak into the next phase
    phases = store.get(run.id).result["phases"]  # type: ignore[index]
    assert [p["id"] for p in phases] == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_retry_cap_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_PHASE_RETRIES_ENV, "2")
    # The findings never change here, so FORGE-573's stall stop would end the run
    # first; lift it so this test checks the retry cap on its own.
    from orchestrator.design_flow.rework import STALL_STOP_ENV

    monkeypatch.setenv(STALL_STOP_ENV, "10")
    evaluator = ScriptedEvaluator([set()])  # never satisfies the gate
    coord, store, brain, run, executor = _setup(_flow("retry_cap"), evaluator)
    task = asyncio.create_task(executor.run(run.id))

    await _wait_awaiting(store, run.id)
    _decide(coord, store, run.id, ApprovalDecision.APPROVE)
    for n in (1, 2):
        await _wait_awaiting(store, run.id, after=n)
        _decide(coord, store, run.id, ApprovalDecision.RETRY, f"try {n}")
    await _wait_awaiting(store, run.id, after=3)
    state = coord.gate_state(run.id)
    assert state is not None and state["ready"] is False and state["retries_left"] == 0
    _decide(coord, store, run.id, ApprovalDecision.RETRY, "one more")
    await asyncio.wait_for(task, timeout=3.0)

    run_ = store.get(run.id)
    assert run_.status is RunStatus.FAILED
    assert "after 2 retries" in (run_.error or "")
    assert [c[0] for c in brain.calls].count("two") == 3  # first attempt + 2 retries


@pytest.mark.asyncio
async def test_reject_still_ends_the_run_and_a_not_ready_gate_parks() -> None:
    evaluator = ScriptedEvaluator([set()])
    coord, store, brain, run, executor = _setup(_flow("retry_reject"), evaluator)
    task = asyncio.create_task(executor.run(run.id))
    await _wait_awaiting(store, run.id)
    _decide(coord, store, run.id, ApprovalDecision.APPROVE)
    await _wait_awaiting(store, run.id, after=1)
    # Parked with findings, not failed.
    assert store.get(run.id).status is RunStatus.AWAITING_APPROVAL
    _decide(coord, store, run.id, ApprovalDecision.REJECT)
    await asyncio.wait_for(task, timeout=3.0)
    assert store.get(run.id).status is RunStatus.REJECTED
    assert "three" not in [c[0] for c in brain.calls]


def test_retry_feedback_leads_with_findings_then_reason() -> None:
    text = build_retry_feedback(findings=["missing X"], reason="add X", attempt=2)
    assert text.index("missing X") < text.index("add X")
    assert "attempt 2" in text


def test_max_phase_retries_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MAX_PHASE_RETRIES_ENV, raising=False)
    assert max_phase_retries() == DEFAULT_MAX_PHASE_RETRIES == 3
    monkeypatch.setenv(MAX_PHASE_RETRIES_ENV, "5")
    assert max_phase_retries() == 5
    monkeypatch.setenv(MAX_PHASE_RETRIES_ENV, "junk")
    assert max_phase_retries() == 3


def test_retry_feedback_is_the_first_thing_in_the_phase_prompt() -> None:
    from api_gateway.runs.flow_brain import ReActPhaseBrain

    brain = ReActPhaseBrain(mcp_bridge=None)
    phase = Phase(id="p", title="P", objective="obj")
    feedback = build_retry_feedback(findings=["missing X"], reason="add X", attempt=2)
    first = brain._prompt("goal", phase, FlowContext(goal="goal", flow_context="ctx"))
    retried = brain._prompt(
        "goal", phase, FlowContext(goal="goal", flow_context="ctx", retry_feedback=feedback)
    )
    assert "RETRY" not in first
    assert retried.startswith(feedback)
    assert retried.index("missing X") < retried.index("Flow context")
