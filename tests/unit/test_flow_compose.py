"""Composing fragments into flows (composable flows, stories 4 and 5).

Story 4: a flow is the spine plus the fragments its intent names, composed
server-side by a validated ``add_fragment`` operation.

Story 5: dependencies come from the data, so staleness and rework follow
it -- Planner §21's own example: a change to how a part looks must not re-run
its structural analysis.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from orchestrator.design_flow.capabilities import GapSeverity, assess_capabilities
from orchestrator.design_flow.compose import CompositionError, compose, fragment_ids
from orchestrator.design_flow.contract import ref_key
from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.fragments import fragment_from_mapping, load_fragments
from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.generator import (
    Operation,
    OperationKind,
    TailoringError,
    build_proposal,
    parse_caller_operations,
    parse_operations,
)
from orchestrator.design_flow.graph import build_graph
from orchestrator.design_flow.intent import compile_intent, suggest_fragments
from orchestrator.design_flow.invariants import validate_flow
from orchestrator.design_flow.lifecycle import Validity, lifecycle_view
from orchestrator.design_flow.spec import (
    FlowDefinition,
    Gate,
    Phase,
    definition_from_frozen,
    get_flow,
)
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus

THERMAL = "thermal_v1"
ELEC = "electronics_v1"


def _both(base: str = "design_v1") -> FlowDefinition:
    fr = load_fragments()
    return compose(get_flow(base), [fr[ELEC], fr[THERMAL]]).definition


# -- story 4: composition ---------------------------------------------------


@pytest.mark.parametrize("base", ["design_v1", "mech_v1", "hardware_v1"])
def test_composing_both_fragments_into_every_template_is_valid(base: str) -> None:
    flow = _both(base)
    assert validate_flow(flow).ok
    build_graph(flow.phases)  # acyclic
    assert fragment_ids(flow) == (ELEC, THERMAL)
    # Stage phases keep their gates; tasks have none.
    assert all((p.gate is None) == p.is_task for p in flow.phases)


def test_a_task_waits_for_the_stage_before_its_own_and_its_producers() -> None:
    flow = {p.id: p for p in _both().phases}
    assert flow["electronics.component_selection"].depends_on == ("feasibility",)
    assert set(flow["thermal.heat_load"].depends_on or ()) == {
        "feasibility",
        "electronics.power_budget",
    }
    # V&V tasks start after the design gate, and read what design wrote.
    assert set(flow["thermal.heat_transfer"].depends_on or ()) == {
        "design",
        "thermal.heat_load",
        "thermal.cooling_architecture",
    }


def test_a_stage_gate_waits_for_every_task_it_reviews() -> None:
    flow = {p.id: p for p in _both().phases}
    assert set(flow["design"].depends_on or ()) == {
        "feasibility",
        "electronics.component_selection",
        "electronics.power_budget",
        "thermal.heat_load",
        "thermal.cooling_architecture",
    }


def test_inputs_no_task_writes_are_reported_including_check_limits() -> None:
    fr = load_fragments()
    composed = compose(get_flow("design_v1"), [fr[ELEC], fr[THERMAL]])
    external = {e.ref: e.consumers for e in composed.external_inputs}
    assert set(external) == {
        "design_value:max_ambient_temp_c",
        "design_value:max_allowed_junction_temp_c",
        "design_value:max_supply_ripple_mv",
    }
    assert "thermal.heat_transfer" in external["design_value:max_allowed_junction_temp_c"]


def test_thermal_alone_needs_its_dissipation_from_outside() -> None:
    composed = compose(get_flow("design_v1"), [load_fragments()[THERMAL]])
    assert "design_value:power_dissipation_w" in {e.ref for e in composed.external_inputs}


def test_composing_the_same_fragment_twice_is_refused() -> None:
    fr = load_fragments()
    once = compose(get_flow("design_v1"), [fr[THERMAL]]).definition
    with pytest.raises(CompositionError, match="composed twice"):
        compose(once, [fr[THERMAL]])


def test_composition_is_incremental() -> None:
    fr = load_fragments()
    step = compose(get_flow("design_v1"), [fr[ELEC]]).definition
    step = compose(step, [fr[THERMAL]]).definition
    assert [p.id for p in step.phases] == [p.id for p in _both().phases]
    assert {p.id: p.depends_on for p in step.phases} == {p.id: p.depends_on for p in _both().phases}


def test_a_stage_the_flow_lacks_is_refused() -> None:
    base = get_flow("design_v1")
    no_sim = FlowDefinition(
        id=base.id, name=base.name, phases=tuple(p for p in base.phases if p.id != "simulation")
    )
    with pytest.raises(CompositionError, match="stage 'simulation'"):
        compose(no_sim, [load_fragments()[THERMAL]])


def test_a_missing_required_fragment_is_refused() -> None:
    raw = {
        "id": "needy_v1",
        "version": "1.0.0",
        "domain": "needy",
        "requires": [THERMAL],
        "tasks": [
            {
                "id": "t",
                "stage": "design",
                "objective": "o",
                "outputs": ["design_value:needy_x"],
            }
        ],
    }
    with pytest.raises(CompositionError, match="requires thermal_v1"):
        compose(get_flow("design_v1"), [fragment_from_mapping(raw)])


def test_two_fragments_writing_one_item_are_refused() -> None:
    raw = {
        "id": "rival_v1",
        "version": "1.0.0",
        "domain": "rival",
        "tasks": [
            {
                "id": "t",
                "stage": "design",
                "objective": "o",
                "outputs": ["design_value:heat_load_w"],
            }
        ],
    }
    with pytest.raises(CompositionError, match="an item has one writer"):
        compose(get_flow("design_v1"), [load_fragments()[THERMAL], fragment_from_mapping(raw)])


def test_data_flowing_backwards_across_stages_is_refused() -> None:
    raw = {
        "id": "backwards_v1",
        "version": "1.0.0",
        "domain": "backwards",
        "tasks": [
            {
                "id": "early",
                "stage": "design",
                "objective": "o",
                "inputs": ["design_value:late_x"],
                "outputs": ["design_value:early_x"],
            },
            {
                "id": "late",
                "stage": "simulation",
                "objective": "o",
                "outputs": ["design_value:late_x"],
            },
        ],
    }
    with pytest.raises(CompositionError, match="later stage"):
        compose(get_flow("design_v1"), [fragment_from_mapping(raw)])


def test_a_composed_flow_freezes_verifies_and_round_trips() -> None:
    flow = _both()
    frozen = freeze_flow(flow, version="1.0.0")
    frozen.verify()
    assert definition_from_frozen(frozen) == flow


# -- story 4: the operation -------------------------------------------------


def test_add_fragment_composes_and_records_the_change() -> None:
    ops = parse_operations(
        [{"op": "add_fragment", "value": THERMAL, "rationale": "the board runs hot"}]
    )
    proposal = build_proposal(get_flow("design_v1"), base_version="1.0.0", operations=ops)
    assert proposal.valid
    assert fragment_ids(proposal.definition) == (THERMAL,)
    assert proposal.diff() == ["compose fragment 'thermal_v1' into the flow — the board runs hot"]
    assert proposal.composition is not None
    assert any("power_dissipation_w" in a for a in proposal.assumptions)


def test_a_model_asking_for_an_unknown_fragment_loses_only_that_change() -> None:
    ops = parse_operations(
        [
            {"op": "add_fragment", "value": "warp_drive_v1", "rationale": "x"},
            {"op": "add_fragment", "value": THERMAL, "rationale": "hot"},
        ]
    )
    proposal = build_proposal(get_flow("design_v1"), base_version="1.0.0", operations=ops)
    assert [op.value for op in proposal.operations] == [THERMAL]


def test_a_fragment_listed_before_the_one_it_requires_still_composes() -> None:
    # Two passes: order of the operations does not decide whether it fits.
    tailored = build_proposal(
        get_flow("design_v1"),
        base_version="1.0.0",
        operations=[
            Operation(OperationKind.ADD_FRAGMENT, "", "hot", value=THERMAL),
            Operation(OperationKind.ADD_FRAGMENT, "", "board", value=ELEC),
        ],
    )
    assert set(fragment_ids(tailored.definition)) == {THERMAL, ELEC}


def test_a_caller_naming_an_unknown_fragment_is_refused() -> None:
    with pytest.raises(TailoringError, match="is not a fragment"):
        parse_caller_operations(
            [{"op": "add_fragment", "value": "nope", "rationale": "r"}], get_flow("design_v1")
        )


def test_a_caller_fragment_without_a_rationale_is_refused() -> None:
    with pytest.raises(TailoringError, match="rationale is required"):
        parse_caller_operations([{"op": "add_fragment", "value": THERMAL}], get_flow("design_v1"))


def test_a_caller_fragment_that_cannot_compose_is_refused_with_the_reason() -> None:
    with pytest.raises(TailoringError, match="stage 'simulation'"):
        parse_caller_operations(
            [
                {"op": "drop_phase", "phase": "simulation", "rationale": "no V&V"},
                {"op": "add_fragment", "value": THERMAL, "rationale": "hot"},
            ],
            get_flow("design_v1"),
        )


def test_intent_suggests_fragments_by_whole_word() -> None:
    assert suggest_fragments("a sensor on a small pcb") == (ELEC,)
    assert THERMAL in suggest_fragments("passive cooling for a 20 W heat load")
    assert suggest_fragments("a theatre stage prop") == ()
    model = compile_intent("design a PCB with a regulator and heatsink cooling")
    assert set(model.suggested_fragments) == {ELEC, THERMAL}
    assert model.as_dict()["suggested_fragments"] == list(model.suggested_fragments)


def test_task_capabilities_are_assessed() -> None:
    flow = _both()
    report = assess_capabilities(
        flow.phases,
        producers={},
        registered={"twin.record_engineering_entity", "twin.record_decision", "power.check_budget"},
    )
    gaps = {g.phase_id: g for g in report.gaps if g.capability.startswith("tool:")}
    assert gaps["thermal.heat_transfer"].capability == "tool:calculix.run_thermal"
    assert gaps["thermal.heat_transfer"].severity is GapSeverity.REQUIRES_NEW_CAPABILITY
    assert gaps["thermal.heat_transfer"].blocking
    assert "thermal.heat_load" not in gaps
    assert report.status == "BLOCKED"


def test_an_unreachable_task_tool_needs_the_user() -> None:
    task = load_fragments()[THERMAL].task("heat_transfer")
    report = assess_capabilities(
        [task], producers={}, registered={"calculix.run_thermal"}, reachable=set()
    )
    (gap,) = [g for g in report.gaps if g.capability.startswith("tool:")]
    assert gap.severity is GapSeverity.REQUIRES_USER_ACTION
    assert gap.unreachable_tools == ("calculix.run_thermal",)


# -- story 5: data-level edges ----------------------------------------------


def test_impact_follows_data_not_ordering() -> None:
    graph = build_graph(_both().phases)
    # A changed power budget reaches the thermal chain and the gates that
    # reviewed it -- not the ERC or the ripple simulation, which never read it.
    affected = set(graph.affected("electronics.power_budget"))
    assert {"thermal.heat_load", "thermal.heat_transfer", "design", "simulation"} <= affected
    assert "electronics.schematic_check" not in affected
    assert "electronics.circuit_simulation" not in affected
    # Ordering still holds: the ERC waits for the design gate.
    assert "design" in graph.upstream("electronics.schematic_check")
    assert graph.impacted_by("electronics.schematic_check") == ["electronics.component_selection"]


def test_a_flow_without_tasks_keeps_the_old_semantics() -> None:
    graph = build_graph(get_flow("mech_v1").phases)
    assert graph.impact is None
    for pid in graph.order:
        assert graph.affected(pid) == graph.downstream(pid)


def _appearance_flow() -> FlowDefinition:
    """Planner §21's example: a colour change must not re-run structural analysis."""
    raw = {
        "id": "housing_v1",
        "version": "1.0.0",
        "domain": "housing",
        "tasks": [
            {
                "id": "finish",
                "stage": "design",
                "objective": "Pick the finish.",
                "outputs": ["design_value:housing_colour_code"],
            },
            {
                "id": "render",
                "stage": "simulation",
                "objective": "Render it.",
                "inputs": ["design_value:housing_colour_code"],
                "outputs": ["design_value:render_count"],
            },
            {
                "id": "wall",
                "stage": "design",
                "objective": "Size the wall.",
                "outputs": ["design_value:wall_thickness_mm"],
            },
            {
                "id": "structural",
                "stage": "simulation",
                "objective": "Check the wall.",
                "inputs": ["design_value:wall_thickness_mm"],
                "outputs": ["design_value:wall_safety_factor"],
            },
        ],
    }
    return compose(get_flow("design_v1"), [fragment_from_mapping(raw)]).definition


def test_planner_s21_a_colour_change_does_not_touch_structural_analysis() -> None:
    flow = _appearance_flow()
    state = {"status": "completed", "completed": [{"phase": p.id} for p in flow.phases]}
    view = lifecycle_view(
        flow.phases,
        state,
        stale_item_keys=[ref_key("design_value:housing_colour_code")],
    )
    validity = {n.id: n.validity for n in view.nodes}
    assert validity["housing.finish"] is Validity.STALE
    assert validity["housing.render"] is Validity.POTENTIALLY_INVALID
    assert validity["design"] is Validity.POTENTIALLY_INVALID  # it reviewed the finish
    assert validity["housing.wall"] is Validity.VALID
    assert validity["housing.structural"] is Validity.VALID
    assert validity["requirements"] is Validity.VALID


# -- story 5: selective rework on the engine --------------------------------


class _Brain:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append(phase.id)
        return PhaseOutcome(summary=f"did {phase.id}", status="completed")


def _auto(flow: FlowDefinition, keep: set[str]) -> FlowDefinition:
    """Auto-approve every gate but ``keep``, so the test drives only those."""
    phases = []
    for p in flow.phases:
        if p.gate is not None and p.id not in keep:
            p = dataclasses.replace(p, gate=Gate(name=p.gate.name, auto_approve=True))
        phases.append(p)
    return FlowDefinition(id=flow.id, name=flow.name, phases=tuple(phases))


async def _awaiting(store: InMemoryRunStore, run_id: str, *, after: int) -> None:
    for _ in range(400):
        run = store.get(run_id)
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > after:
            return
        if run.is_terminal:
            raise AssertionError(f"run ended: {run.status} {run.error}")
        await asyncio.sleep(0.005)
    raise AssertionError(f"never awaiting approval: {store.get(run_id).status}")


@pytest.mark.asyncio
async def test_rework_to_a_task_reruns_only_what_reads_its_outputs() -> None:
    flow = _auto(_appearance_flow(), keep={"simulation"})
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    brain = _Brain()
    run = store.create({"goal": "g", "flow": flow.id, "project_id": "p1"})
    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord)
    task = asyncio.create_task(executor.run(run.id, flow))
    await _awaiting(store, run.id, after=0)  # the simulation gate
    coord.note_rework(run.id, "housing.finish", "wrong colour")
    store.submit_approval(run.id, ApprovalDecision.REWORK)
    await _awaiting(store, run.id, after=1)
    store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, 5)

    first = brain.calls.index("simulation")
    again = brain.calls[first + 1 :]
    assert again == ["housing.finish", "design", "housing.render", "simulation"]
    assert brain.calls.count("housing.structural") == 1
    assert brain.calls.count("housing.wall") == 1
    assert store.get(run.id).status is RunStatus.COMPLETED
