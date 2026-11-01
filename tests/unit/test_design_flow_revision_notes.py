"""Retried and reworked phases are told what their gate turned down (FORGE-530).

The in-process executor over a real ``InMemoryTwinAPI``, with the gateway's
own wiring: ``run_change_sets.phase_scope`` makes the phase's writes drafts in
the run's change set, ``close_for_decision`` closes them the way
``decide_run_gate`` does, and ``run_change_sets.revision_notes`` reads them
back for the feedback. Plus the Temporal activity and the notes' wire shape.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

from api_gateway.runs import change_sets as run_change_sets
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.rework_context import (
    RevisionNote,
    closed_phase_revisions,
    notes_from_dicts,
    notes_to_dicts,
)
from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Gate, Phase
from orchestrator.design_flow.temporal_activities import DesignFlowActivities
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus
from twin_core.api import InMemoryTwinAPI

PROJECT = "77777777-7777-7777-7777-777777777777"
THICKNESS = {1: 4.0, 2: 6.0, 3: 7.0}


def _step(body: str) -> str:
    return base64.b64encode(f"ISO-10303-21;\n{body}\nENDSEC;\n".encode()).decode("ascii")


@pytest.fixture(autouse=True)
def _blob_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    before = dict(FLOWS)
    yield
    FLOWS.clear()
    FLOWS.update(before)
    run_change_sets.reset()


@pytest.fixture
def twin(monkeypatch: pytest.MonkeyPatch) -> InMemoryTwinAPI:
    t = InMemoryTwinAPI.create()
    monkeypatch.setattr(run_change_sets, "_twin", lambda: t)
    return t


async def _bracket(twin: InMemoryTwinAPI, thickness: float) -> dict[str, Any]:
    record = make_geometry_recorder(twin, None)
    return await record(
        step_base64=_step(f"bracket-{thickness}"),
        name="Bracket",
        project_id=PROJECT,
        properties={"volume_mm3": 1000.0 * thickness, "bounding_box": [120, 40, thickness]},
    )


class DraftingBrain:
    """Phase ``two`` commits a Bracket revision each attempt (a draft, via the phase scope)."""

    def __init__(self, twin: InMemoryTwinAPI) -> None:
        self.twin = twin
        self.calls: list[tuple[str, int, str]] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.attempt, context.retry_feedback))
        if phase.id == "two":
            await _bracket(self.twin, THICKNESS[min(context.attempt, 3)])
        return PhaseOutcome(summary=f"did {phase.id} #{context.attempt}", status="completed")


class NotReadyOnce:
    """The gate of phase two reports cad_model missing on its first check only."""

    def __init__(self) -> None:
        self.calls = 0

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]:
        self.calls += 1
        return set() if self.calls == 1 else {"cad_model"}


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
        ),
    )
    return flow_id


async def _wait_awaiting(store: InMemoryRunStore, run_id: str, *, after: int = 0) -> None:
    for _ in range(600):
        run = store.get(run_id)
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > after:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"never awaiting approval: {store.get(run_id).status}")


async def _decide(
    coord: GateCoordinator,
    store: InMemoryRunStore,
    run_id: str,
    decision: ApprovalDecision,
    reason: str = "",
    to_phase: str = "",
) -> None:
    """What ``decide_run_gate`` does: close the drafts first, then move the run."""
    run = store.get(run_id)
    if decision is not ApprovalDecision.APPROVE:
        await run_change_sets.close_for_decision(run, decision, reason)
    if decision is ApprovalDecision.RETRY:
        coord.note_retry(run_id, reason)
    elif decision is ApprovalDecision.REWORK:
        coord.note_rework(run_id, to_phase, reason)
    store.submit_approval(run_id, decision)


def _setup(twin: InMemoryTwinAPI, flow_id: str, evaluator: Any = None):
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = DraftingBrain(twin)
    run = store.create({"goal": "g", "flow": flow_id, "project_id": PROJECT})
    executor = DesignFlowExecutor(
        store=store,
        brain=brain,
        coordinator=coord,
        gate_evaluator=evaluator,
        phase_scope=run_change_sets.phase_scope,
        revision_notes=run_change_sets.revision_notes,
    )
    return coord, store, brain, run, executor


async def test_retried_phase_brief_names_the_revision_reason_and_diff(twin) -> None:
    await _bracket(twin, 8.0)  # CAD-BRACKET@1, the approved baseline
    coord, store, brain, run, executor = _setup(twin, _flow("notes_retry"), NotReadyOnce())
    task = asyncio.create_task(executor.run(run.id))

    await _wait_awaiting(store, run.id)  # gate one
    await _decide(coord, store, run.id, ApprovalDecision.APPROVE)
    await _wait_awaiting(store, run.id, after=1)  # gate two, not ready
    await _decide(coord, store, run.id, ApprovalDecision.RETRY, "too thin")

    await _wait_awaiting(store, run.id, after=2)  # gate two, second attempt
    feedback = brain.calls[2][2]
    assert brain.calls[2][:2] == ("two", 2)
    assert feedback.startswith("RETRY (attempt 2)")
    assert "  - CAD-BRACKET@2 cad_model 'Bracket' (abandoned)" in feedback
    assert "    Gate's reason: phase 'two' did not record required deliverables" in feedback
    assert "    Changed from CAD-BRACKET@1:" in feedback
    assert "      bbox 120x40x8 mm -> 120x40x4 mm" in feedback
    assert "      volume 8000 -> 4000 mm3 (-4000, -50.0%)" in feedback
    assert feedback.endswith("Fix exactly these problems before you reply.")

    # A second retry is told about the second attempt only, not both.
    await _decide(coord, store, run.id, ApprovalDecision.RETRY, "still too thin")
    await _wait_awaiting(store, run.id, after=3)
    third = brain.calls[3][2]
    assert "CAD-BRACKET@3 cad_model 'Bracket' (abandoned)" in third
    assert "CAD-BRACKET@2 cad_model" not in third
    assert "Gate's reason: still too thin" in third  # ready gate: the reviewer's reason
    assert "volume 4000 -> 6000 mm3" in third

    await _decide(coord, store, run.id, ApprovalDecision.REJECT, "stop")
    await asyncio.wait_for(task, timeout=3.0)


async def test_reworked_phase_brief_names_the_turned_down_revision(twin) -> None:
    await _bracket(twin, 8.0)
    coord, store, brain, run, executor = _setup(twin, _flow("notes_rework"))
    task = asyncio.create_task(executor.run(run.id))

    await _wait_awaiting(store, run.id)
    await _decide(coord, store, run.id, ApprovalDecision.APPROVE)
    await _wait_awaiting(store, run.id, after=1)
    await _decide(coord, store, run.id, ApprovalDecision.REWORK, "SF 0.8 < 1.5", to_phase="one")

    await _wait_awaiting(store, run.id, after=2)  # back at gate one
    phase, _attempt, feedback = brain.calls[2]
    assert phase == "one"
    assert feedback.startswith("REWORK (cycle 1)")
    assert "CAD-BRACKET@2 cad_model 'Bracket' (abandoned)" in feedback
    assert "Changed from CAD-BRACKET@1:" in feedback
    assert "volume 8000 -> 4000 mm3" in feedback

    await _decide(coord, store, run.id, ApprovalDecision.REJECT, "stop")
    await asyncio.wait_for(task, timeout=3.0)


async def test_no_provider_keeps_the_feedback_unchanged(twin) -> None:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = DraftingBrain(twin)
    run = store.create({"goal": "g", "flow": _flow("notes_none"), "project_id": PROJECT})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, phase_scope=run_change_sets.phase_scope
    )
    task = asyncio.create_task(executor.run(run.id))
    await _wait_awaiting(store, run.id)
    await _decide(coord, store, run.id, ApprovalDecision.APPROVE)
    await _wait_awaiting(store, run.id, after=1)
    await _decide(coord, store, run.id, ApprovalDecision.RETRY, "again")
    await _wait_awaiting(store, run.id, after=2)
    assert "Revisions this gate turned down" not in brain.calls[2][2]
    await _decide(coord, store, run.id, ApprovalDecision.REJECT, "stop")
    await asyncio.wait_for(task, timeout=3.0)


async def test_closed_phase_revisions_is_scoped_to_run_and_phase(twin) -> None:
    from mcp_core.context import McpCallContext, with_context
    from twin_core.items import close_change_set

    await _bracket(twin, 8.0)
    with with_context(McpCallContext(actor_id="svc", run_id="run-a", phase="two")):
        mine = await _bracket(twin, 4.0)
    with with_context(McpCallContext(actor_id="svc", run_id="run-b", phase="two")):
        await _bracket(twin, 5.0)
    await close_change_set(twin, "run-a", status="abandoned", reason="retried")
    await close_change_set(twin, "run-b", status="rejected", reason="no")

    found = await closed_phase_revisions(twin, run_id="run-a", phase_id="two", project_id=PROJECT)
    assert [str(n) for n in found] == [mine["node_id"]]
    assert await closed_phase_revisions(twin, run_id="run-a", phase_id="one") == []


async def test_closed_drafts_of_a_new_item_are_found(twin) -> None:
    # Live (shelf run, needs phase): the phase wrote two NEW need items, the
    # retry abandoned them, and the retried phase was told nothing, because a
    # new item has no head and list_items left unheaded items out.
    from mcp_core.context import McpCallContext, with_context
    from twin_core.items import close_change_set

    record = make_geometry_recorder(twin, None)
    with with_context(McpCallContext(actor_id="svc", run_id="run-n", phase="two")):
        new = await record(
            step_base64=_step("spacer"),
            name="Spacer",
            project_id=PROJECT,
            properties={"volume_mm3": 500.0, "bounding_box": [20, 20, 5]},
        )
    await close_change_set(twin, "run-n", status="abandoned", reason="retried")

    found = await closed_phase_revisions(twin, run_id="run-n", phase_id="two", project_id=PROJECT)
    assert [str(n) for n in found] == [new["node_id"]]


async def test_activity_returns_plain_notes() -> None:
    seen: list[tuple[Any, ...]] = []

    async def provider(run_id: str, phase_id: str, project_id: Any, reason: str):
        seen.append((run_id, phase_id, project_id, reason))
        return [RevisionNote(ref="CAD-X@2", item_type="cad_model", reason=reason)]

    async def runner(_: Any) -> Any:
        raise AssertionError("not called")

    acts = DesignFlowActivities(phase_runner=runner, revision_notes=provider)
    rows = await acts.collect_revision_notes(
        {"run_id": "r", "phase_id": "two", "project_id": PROJECT, "reason": "why"}
    )
    assert seen == [("r", "two", PROJECT, "why")]
    assert rows[0]["ref"] == "CAD-X@2" and isinstance(rows[0]["changes"], list)
    assert acts.collect_revision_notes in acts.all()

    async def broken(*_: Any) -> list[RevisionNote]:
        raise RuntimeError("twin down")

    assert (
        await DesignFlowActivities(
            phase_runner=runner, revision_notes=broken
        ).collect_revision_notes({})
        == []
    )
    assert await DesignFlowActivities(phase_runner=runner).collect_revision_notes({}) == []


def test_notes_round_trip() -> None:
    note = RevisionNote(
        ref="CS-REQS@3",
        item_type="constraint_set",
        name="Reqs",
        status="rejected",
        reason="load too low",
        previous_ref="CS-REQS@2",
        changes=("load_kg >= 20 kg -> load_kg >= 25 kg",),
    )
    assert notes_from_dicts(notes_to_dicts([note])) == [note]
    assert notes_from_dicts([{"no": "ref"}, "junk"]) == []
