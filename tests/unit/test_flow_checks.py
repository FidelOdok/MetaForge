"""Task checks at stage gates and coupled blocks (composable flows, stories 6-7).

Story 6: a stage gate evaluates the checks of the tasks it reviews. A failed
check and a value nobody recorded both block -- no data is a gap, not a pass.

Story 7: a feedback loop (battery capacity -> mass -> hover power -> battery
capacity) is one block phase solved to convergence. One that does not settle
stops at its limit and says it needs an engineering decision; nothing is
loosened to make it close.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.runs.gate_eval import TwinDesignValueReader
from api_gateway.runs.routes import _GateCheckers
from orchestrator.design_flow.checks import CheckOutcome, attached_tasks, evaluate_stage
from orchestrator.design_flow.compose import compose
from orchestrator.design_flow.coupling import ConvergenceStatus, judge, solve
from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.fragments import fragment_from_mapping, load_fragments
from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.graph import build_graph
from orchestrator.design_flow.spec import Coupling, FlowDefinition, Gate, Phase, get_flow
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus


def _thermal_flow() -> FlowDefinition:
    return compose(get_flow("design_v1"), [load_fragments()["thermal_v1"]]).definition


_GOOD = {
    "heat_load_w": 12.0,
    "thermal_resistance_budget_c_per_w": 4.5,
    "max_junction_temp_c": 78.0,
    "max_allowed_junction_temp_c": 105.0,
    "thermal_margin_c": 27.0,
}


# -- evaluation -------------------------------------------------------------


def test_every_check_under_a_stage_passes_on_good_values() -> None:
    report = evaluate_stage("simulation", _thermal_flow().phases, _GOOD)
    assert not report.blocking
    assert {r.check for r in report.results} == {"max_junction_temp_c", "thermal_margin_c"}
    assert all(r.outcome is CheckOutcome.PASS for r in report.results)


def test_a_failing_check_blocks_with_the_numbers() -> None:
    report = evaluate_stage(
        "simulation", _thermal_flow().phases, {**_GOOD, "max_junction_temp_c": 118.0}
    )
    assert report.blocking
    (finding,) = [f for f in report.findings if "max_junction_temp_c" in f]
    assert "failed" in finding and "recorded 118" in finding and "bound 105" in finding


def test_an_unrecorded_value_is_no_data_and_blocks() -> None:
    values = {k: v for k, v in _GOOD.items() if k != "thermal_margin_c"}
    report = evaluate_stage("simulation", _thermal_flow().phases, values)
    (result,) = [r for r in report.results if r.check == "thermal_margin_c"]
    assert result.outcome is CheckOutcome.NO_DATA
    assert report.blocking


def test_an_unrecorded_limit_is_no_data_and_says_where_it_comes_from() -> None:
    values = {k: v for k, v in _GOOD.items() if k != "max_allowed_junction_temp_c"}
    report = evaluate_stage("simulation", _thermal_flow().phases, values)
    (result,) = [r for r in report.results if r.check == "max_junction_temp_c"]
    assert result.outcome is CheckOutcome.NO_DATA
    assert "requirements or a datasheet" in result.reason


def test_a_non_numeric_value_is_no_data() -> None:
    report = evaluate_stage(
        "design", _thermal_flow().phases, {**_GOOD, "heat_load_w": "about twelve"}
    )
    (result,) = [r for r in report.results if r.check == "heat_load_w"]
    assert result.outcome is CheckOutcome.NO_DATA


def test_values_that_could_not_be_read_block_every_declared_check() -> None:
    report = evaluate_stage("design", _thermal_flow().phases, None)
    assert not report.evaluated and report.blocking
    assert "no data is not a pass" in report.findings[0]


def test_a_stage_without_checked_tasks_has_nothing_to_report() -> None:
    report = evaluate_stage("requirements", _thermal_flow().phases, None)
    assert not report.blocking and report.results == ()


def test_checks_read_frozen_phases_too() -> None:
    frozen = freeze_flow(_thermal_flow())
    assert len(attached_tasks(frozen.phases, "simulation")) == 2
    report = evaluate_stage("simulation", frozen.phases, _GOOD)
    assert not report.blocking and len(report.results) == 2


# -- the in-process engine --------------------------------------------------


class _Brain:
    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        return PhaseOutcome(summary=f"did {phase.id}", status="completed")


class _Values:
    def __init__(self, values: dict[str, float], history: dict | None = None) -> None:
        self._values = values
        self._history = history or {}

    async def values(self, project_id: str | None) -> dict[str, float]:
        return self._values

    async def history(self, project_id: str | None, names: list[str]) -> dict:
        return {n: self._history.get(n, []) for n in names}


def _only_gate(flow: FlowDefinition, gate_id: str) -> FlowDefinition:
    import dataclasses

    return FlowDefinition(
        id=flow.id,
        name=flow.name,
        phases=tuple(
            dataclasses.replace(p, gate=Gate(name=p.gate.name, auto_approve=True))
            if p.gate is not None and p.id != gate_id
            else p
            for p in flow.phases
        ),
    )


async def _gate_state(values: _Values | None) -> dict:
    flow = _only_gate(_thermal_flow(), "simulation")
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "g", "flow": flow.id, "project_id": "p1"})
    executor = DesignFlowExecutor(
        store=store, brain=_Brain(), coordinator=coord, value_reader=values
    )
    task = asyncio.create_task(executor.run(run.id, flow))
    for _ in range(400):
        if store.get(run.id).status is RunStatus.AWAITING_APPROVAL:
            break
        await asyncio.sleep(0.005)
    state = dict(coord.gate_state(run.id) or {})
    store.submit_approval(run.id, ApprovalDecision.REJECT)
    await asyncio.wait_for(task, 5)
    return state


@pytest.mark.asyncio
async def test_the_stage_gate_is_ready_when_its_tasks_checks_pass() -> None:
    state = await _gate_state(_Values(_GOOD))
    assert state["ready"] is True


@pytest.mark.asyncio
async def test_the_stage_gate_is_not_ready_when_a_check_fails() -> None:
    state = await _gate_state(_Values({**_GOOD, "thermal_margin_c": -3.0}))
    assert state["ready"] is False


@pytest.mark.asyncio
async def test_the_stage_gate_is_not_ready_without_a_value_reader() -> None:
    state = await _gate_state(None)
    assert state["ready"] is False


# -- the Temporal path's gateway checker -------------------------------------


class _Evaluator:
    def __init__(self, present: set[str]) -> None:
        self._present = present

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]:
        return self._present


class _Constraints:
    async def check(self, project_id: str | None, since_ts: float = 0.0) -> SimpleNamespace:
        return SimpleNamespace(passed=True, checked=True, violations=[])


@pytest.mark.asyncio
async def test_the_gateway_checker_reports_task_findings_for_temporal() -> None:
    frozen = freeze_flow(_thermal_flow())
    stage = next(p for p in frozen.phases if p.id == "simulation")
    tasks = [p for p in frozen.phases if p.fragment and p.stage == "simulation"]
    checkers = _GateCheckers(
        _Evaluator({"design_value", "design_decision"}),
        _Constraints(),
        None,
        _Values({**_GOOD, "max_junction_temp_c": 130.0}),
    )
    check = await checkers.evaluate(stage, str(uuid4()), tasks=tasks)
    assert any("simulation_result" in f for f in check.check_findings)
    assert any("max_junction_temp_c" in f and "failed" in f for f in check.check_findings)
    assert {r["outcome"] for r in check.check_results} == {"FAIL", "PASS"}


@pytest.mark.asyncio
async def test_the_gateway_checker_without_tasks_is_unchanged() -> None:
    checkers = _GateCheckers(_Evaluator({"design_decision"}), _Constraints(), None, None)
    phase = get_flow("design_v1").phases[2]
    check = await checkers.evaluate(phase, str(uuid4()))
    assert check.check_findings == [] and check.check_results == []


@pytest.mark.asyncio
async def test_the_twin_reader_takes_the_latest_record_per_name() -> None:
    def entity(name: str, value: object, at: str) -> SimpleNamespace:
        return SimpleNamespace(metadata={"name": name, "value": value}, created_at=at)

    rows = [
        entity("heat_load_w", 10, "2026-10-01T10:00:00+00:00"),
        entity("heat_load_w", 12, "2026-10-01T11:00:00+00:00"),
        entity("max_junction_temp_c", 80, "2026-10-01T10:30:00+00:00"),
        SimpleNamespace(metadata={"name": "no_value"}, created_at=None),
    ]

    class _Twin:
        async def list_engineering_entities(self, project_id=None, entity_type=None):  # noqa: ANN001
            assert entity_type == "design_value"
            return rows

    reader = TwinDesignValueReader(_Twin())
    project = str(uuid4())
    assert await reader.values(project) == {"heat_load_w": 12, "max_junction_temp_c": 80}
    assert await reader.history(project, ["heat_load_w", "x"]) == {"heat_load_w": [10, 12], "x": []}


# -- story 7: coupled blocks -------------------------------------------------

_COUPLING = Coupling(
    members=("battery", "mass", "power"),
    solves_for=("battery_capacity_wh", "total_mass_kg", "hover_power_w"),
    tolerance=0.001,
    max_iterations=50,
)


def _steps(endurance_h: float):  # noqa: ANN202
    """A closed-form multicopter energy loop (fixture numbers, not design data)."""
    dry_mass_kg, specific_energy_wh_per_kg, usable, k = 1.2, 150.0, 0.8, 110.0
    return [
        lambda v: {"hover_power_w": k * v["total_mass_kg"] ** 1.5},
        lambda v: {"battery_capacity_wh": v["hover_power_w"] * endurance_h / usable},
        lambda v: {
            "total_mass_kg": dry_mass_kg + v["battery_capacity_wh"] / specific_energy_wh_per_kg
        },
    ]


def test_a_stable_energy_loop_converges() -> None:
    verdict, history = solve(_steps(0.33), {"total_mass_kg": 1.2}, _COUPLING)
    assert verdict.status is ConvergenceStatus.CONVERGED
    assert 2 < verdict.iterations < _COUPLING.max_iterations
    assert all(change < _COUPLING.tolerance for change in verdict.changes.values())
    # The closed loop is self-consistent.
    v = verdict.values
    assert v["battery_capacity_wh"] == pytest.approx(v["hover_power_w"] * 0.33 / 0.8, rel=1e-2)


def test_a_loop_that_cannot_close_stops_and_asks_for_a_decision() -> None:
    verdict, history = solve(_steps(1.5), {"total_mass_kg": 1.2}, _COUPLING)
    assert not verdict.converged
    assert verdict.status in (ConvergenceStatus.NOT_CONVERGED, ConvergenceStatus.DIVERGED)
    assert len(history) <= _COUPLING.max_iterations
    if verdict.status is ConvergenceStatus.NOT_CONVERGED:
        assert "engineering decision" in verdict.reason


def test_judge_needs_two_complete_passes() -> None:
    assert judge([], _COUPLING).status is ConvergenceStatus.NO_DATA
    one = {"battery_capacity_wh": 1.0, "total_mass_kg": 1.0, "hover_power_w": 1.0}
    assert judge([one], _COUPLING).status is ConvergenceStatus.NO_DATA
    assert judge([one, one], _COUPLING).status is ConvergenceStatus.CONVERGED
    nan = {**one, "hover_power_w": float("nan")}
    assert judge([one, nan], _COUPLING).status is ConvergenceStatus.DIVERGED


def _energy_fragment() -> dict:
    return {
        "id": "multirotor_v1",
        "version": "1.0.0",
        "domain": "multirotor",
        "tasks": [
            {
                "id": "battery",
                "stage": "design",
                "objective": "Size the battery for the endurance.",
                "inputs": ["design_value:hover_power_w", "design_value:endurance_min"],
                "outputs": ["design_value:battery_capacity_wh"],
            },
            {
                "id": "mass",
                "stage": "design",
                "objective": "Roll up the all-up mass.",
                "inputs": ["design_value:battery_capacity_wh"],
                "outputs": ["design_value:total_mass_kg"],
            },
            {
                "id": "power",
                "stage": "design",
                "objective": "Estimate hover power for the mass.",
                "inputs": ["design_value:total_mass_kg"],
                "outputs": ["design_value:hover_power_w"],
                "checks": ["hover_power_w <= max_hover_power_w"],
            },
            {
                "id": "flight_time",
                "stage": "simulation",
                "objective": "Verify the endurance.",
                "inputs": ["design_value:battery_capacity_wh", "design_value:hover_power_w"],
                "outputs": ["design_value:predicted_endurance_min"],
                "checks": ["predicted_endurance_min >= endurance_min"],
            },
        ],
        "blocks": [
            {
                "id": "energy_closure",
                "stage": "design",
                "title": "Energy closure",
                "members": ["battery", "mass", "power"],
                "solves_for": ["battery_capacity_wh", "total_mass_kg", "hover_power_w"],
                "tolerance": 0.01,
                "max_iterations": 20,
            }
        ],
    }


def test_a_block_composes_as_one_phase_and_keeps_the_graph_acyclic() -> None:
    composed = compose(get_flow("design_v1"), [fragment_from_mapping(_energy_fragment())])
    ids = [p.id for p in composed.definition.phases]
    assert "multirotor.energy_closure" in ids
    assert not {"multirotor.battery", "multirotor.mass", "multirotor.power"} & set(ids)
    block = next(p for p in composed.definition.phases if p.coupling is not None)
    assert block.coupling.members == ("multirotor.battery", "multirotor.mass", "multirotor.power")
    # Internal inputs are dropped; the outside ones remain.
    assert set(block.inputs) == {"design_value:endurance_min"}
    assert {s.name for s in block.slots} == {
        "battery_capacity_wh",
        "total_mass_kg",
        "hover_power_w",
    }
    graph = build_graph(composed.definition.phases)
    assert "multirotor.energy_closure" in graph.upstream("multirotor.flight_time")
    assert {e.ref for e in composed.external_inputs} == {
        "design_value:endurance_min",
        "design_value:max_hover_power_w",
    }
    assert "do not relax a requirement" in block.objective
    freeze_flow(composed.definition).verify()


def test_a_block_gate_checks_convergence_from_the_recorded_iterations() -> None:
    flow = compose(get_flow("design_v1"), [fragment_from_mapping(_energy_fragment())]).definition
    values = {
        "battery_capacity_wh": 70.0,
        "total_mass_kg": 1.67,
        "hover_power_w": 237.0,
        "max_hover_power_w": 400.0,
    }
    settled = {
        "battery_capacity_wh": [60.0, 69.5, 70.0],
        "total_mass_kg": [1.6, 1.665, 1.67],
        "hover_power_w": [222.0, 236.0, 237.0],
    }
    ok = evaluate_stage("design", flow.phases, values, settled)
    assert not ok.blocking, ok.findings
    moving = {k: [v[0], v[0] * 1.3] for k, v in settled.items()}
    bad = evaluate_stage("design", flow.phases, values, moving)
    (converged,) = [r for r in bad.results if r.check == "converged"]
    assert converged.outcome is CheckOutcome.FAIL
    none = evaluate_stage("design", flow.phases, values, {})
    (converged,) = [r for r in none.results if r.check == "converged"]
    assert converged.outcome is CheckOutcome.NO_DATA


# -- follow-up: closed drafts and engine-driven block iteration --------------


@pytest.mark.asyncio
async def test_the_twin_reader_skips_rejected_and_abandoned_revisions() -> None:
    ids = [uuid4() for _ in range(4)]
    statuses = {ids[0]: "approved", ids[1]: "rejected", ids[2]: "abandoned", ids[3]: "draft"}
    rows = [
        SimpleNamespace(
            id=node,
            metadata={"name": "total_mass_kg", "value": float(i)},
            created_at=f"2026-10-01T1{i}:00:00+00:00",
        )
        for i, node in enumerate(ids)
    ]

    class _Graph:
        async def get_edges(self, node_id, direction=None, edge_type=None):  # noqa: ANN001
            return [SimpleNamespace(metadata={"status": statuses[node_id]})]

        async def add_node(self, *a, **k): ...  # noqa: ANN002, ANN003
        async def get_node(self, *a, **k): ...  # noqa: ANN002, ANN003
        async def update_node(self, *a, **k): ...  # noqa: ANN002, ANN003
        async def list_nodes(self, *a, **k): ...  # noqa: ANN002, ANN003

    class _Twin:
        graph = _Graph()

        async def add_edge(self, *a, **k): ...  # noqa: ANN002, ANN003
        async def remove_edge(self, *a, **k): ...  # noqa: ANN002, ANN003

        async def list_engineering_entities(self, project_id=None, entity_type=None):  # noqa: ANN001
            return rows

    reader = TwinDesignValueReader(_Twin())
    project = str(uuid4())
    # The approved pass and the run's open draft are kept.
    assert await reader.history(project, ["total_mass_kg"]) == {"total_mass_kg": [0.0, 3.0]}
    assert (await reader.values(project))["total_mass_kg"] == 3.0


class _LoopBrain:
    """One pass of the energy loop per call, recorded like a phase would."""

    def __init__(self, values: _Values, endurance_h: float, record: bool = True) -> None:
        self.values = values
        self.steps = _steps(endurance_h)
        self.record = record
        self.calls: list[tuple[str, str]] = []
        self.state = {"total_mass_kg": 1.2}

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.retry_feedback))
        if phase.coupling is not None and self.record:
            try:
                for step in self.steps:
                    self.state.update(step(self.state))
            except OverflowError:  # a runaway loop: what the phase would record
                self.state = {name: float("inf") for name in phase.coupling.solves_for}
            for name in phase.coupling.solves_for:
                self.values._history.setdefault(name, []).append(self.state[name])
        return PhaseOutcome(summary=f"did {phase.id}", status="completed")


def _energy_flow() -> FlowDefinition:
    flow = compose(get_flow("design_v1"), [fragment_from_mapping(_energy_fragment())]).definition
    return _only_gate(flow, "design")


async def _run_loop(brain: _LoopBrain) -> list[str]:
    flow = _energy_flow()
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "g", "flow": flow.id, "project_id": "p1"})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, value_reader=brain.values
    )
    task = asyncio.create_task(executor.run(run.id, flow))
    for _ in range(800):
        if store.get(run.id).status is RunStatus.AWAITING_APPROVAL:
            break
        await asyncio.sleep(0.005)
    store.submit_approval(run.id, ApprovalDecision.REJECT)
    await asyncio.wait_for(task, 5)
    return [c[0] for c in brain.calls]


@pytest.mark.asyncio
async def test_the_engine_repeats_a_block_until_it_converges() -> None:
    values = _Values({}, {})
    brain = _LoopBrain(values, endurance_h=0.33)
    ran = await _run_loop(brain)
    passes = ran.count("multirotor.energy_closure")
    assert 3 <= passes < 20
    history = values._history["hover_power_w"]
    assert abs(history[-1] - history[-2]) / history[-1] < 0.01  # settled within tolerance
    feedback = [f for p, f in brain.calls if p == "multirotor.energy_closure"]
    assert feedback[0] == ""  # first pass is a plain attempt
    assert feedback[1].startswith("BLOCK ITERATION: this is pass 2")


@pytest.mark.asyncio
async def test_a_block_that_never_settles_stops_at_its_limit() -> None:
    values = _Values({}, {})
    brain = _LoopBrain(values, endurance_h=1.5)
    ran = await _run_loop(brain)
    assert ran.count("multirotor.energy_closure") <= 20


@pytest.mark.asyncio
async def test_a_pass_that_records_nothing_ends_the_loop() -> None:
    values = _Values({}, {})
    brain = _LoopBrain(values, endurance_h=0.33, record=False)
    ran = await _run_loop(brain)
    assert ran.count("multirotor.energy_closure") == 1


@pytest.mark.asyncio
async def test_the_judge_block_activity_asks_for_another_pass_while_moving() -> None:
    from orchestrator.design_flow.temporal_activities import DesignFlowActivities

    moving = {
        "battery_capacity_wh": [60.0, 70.0],
        "total_mass_kg": [1.6, 1.7],
        "hover_power_w": [220.0, 240.0],
    }

    async def history(project_id: str | None, names: list[str]) -> dict:
        return {n: moving.get(n, []) for n in names}

    async def never(*_: object) -> object:
        raise AssertionError("not used")

    activities = DesignFlowActivities(phase_runner=never, value_history=history)
    flow = freeze_flow(
        compose(get_flow("design_v1"), [fragment_from_mapping(_energy_fragment())]).definition
    )
    block = next(p for p in flow.phases if p.coupling is not None)
    out = await activities.judge_block({"project_id": "p", "phase": block})
    assert out["another"] is True and out["iterations"] == 2
    assert out["feedback"].startswith("BLOCK ITERATION: this is pass 3")
    out = await activities.judge_block(
        {"project_id": "p", "phase": {"coupling": {"solves_for": ["x"], "max_iterations": 5}}}
    )
    assert out["another"] is True and out["status"] == "NO_DATA"
    no_reader = DesignFlowActivities(phase_runner=never)
    assert (await no_reader.judge_block({"phase": block}))["another"] is False
