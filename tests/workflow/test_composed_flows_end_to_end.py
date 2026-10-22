"""Composed flows, end to end (composable flows, stories 8 and 9).

The engine, the gates, composition, the task checks, the rework path and the
lifecycle verdict are all the real code. Only the phase work is scripted (no
LLM, no solver): the brain records design values the way a phase would, each
with a source, and a reader plays the twin's design-value store. Values
without a source are not served, as the twin refuses to record them
(FORGE-586).

Story 8 -- thermal_v1 on design_v1. The first cooling choice (natural
convection) gives a 118 degC junction against a 105 degC limit taken from the
part's datasheet; the V&V gate is not ready, the reviewer sends the run back
to the cooling architecture, forced air gives 82 degC, and the run verifies.
A run whose limit nobody recorded cannot pass: no data is not a pass.

Story 9 -- electronics_v1 + thermal_v1. The board's power budget feeds the
heat load. A changed power dissipation makes stale exactly the power budget
and the thermal chain; the ERC and ripple simulation keep their results, and a
rework re-runs only what reads the changed value.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from api_gateway.runs.routes import _in_process_state
from orchestrator.design_flow.checks import CheckOutcome, evaluate_stage
from orchestrator.design_flow.compose import compose
from orchestrator.design_flow.contract import ref_key
from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.fragments import load_fragments
from orchestrator.design_flow.lifecycle import CompletionClass, Validity, lifecycle_view
from orchestrator.design_flow.spec import FlowDefinition, Phase, get_flow
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus

# Fixture values. Each is recorded with the source a real run would cite.
POWER_W = 12.0  # the person's stated dissipation (story 8) / the budget's sum (story 9)
AMBIENT_C = 40.0  # requirement: maximum operating ambient
TJ_LIMIT_C = 105.0  # the regulator datasheet's maximum junction temperature
RIPPLE_LIMIT_MV = 50.0  # requirement: supply ripple
R_NATURAL = 6.5  # degC/W junction-to-ambient, natural convection (scripted analysis result)
R_FORCED = 3.5  # degC/W, forced air (scripted analysis result)


class ValueStore:
    """The twin's design values, as a phase would record them."""

    def __init__(self) -> None:
        self.records: list[tuple[str, float, dict[str, str] | None]] = []

    def record(self, name: str, value: float, source: dict[str, str] | None) -> None:
        self.records.append((name, value, source))

    async def values(self, project_id: str | None) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, value, source in self.records:
            if source:  # an unsourced value is never served
                out[name] = value
        return out

    async def history(self, project_id: str | None, names: list[str]) -> dict[str, list[float]]:
        return {n: [v for name, v, s in self.records if name == n and s] for n in names}


class ScriptedBrain:
    def __init__(
        self, values: ValueStore, *, record_limit: bool = True, stated_power: bool = True
    ) -> None:
        self.values = values
        self.record_limit = record_limit
        #: Thermal alone: the person states the dissipation. With electronics,
        #: the power budget writes it instead.
        self.stated_power = stated_power
        self.calls: list[tuple[str, str]] = []
        self.cooling = ""

    def _v(self, name: str, value: float, kind: str, ref: str) -> None:
        self.values.record(name, value, {"kind": kind, "ref": ref})

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append((phase.id, context.retry_feedback))
        v = self.values
        current = {n: val for n, val, s in v.records if s}
        if phase.id == "requirements":
            self._v("max_ambient_temp_c", AMBIENT_C, "requirement", "REQ-ENV-AMBIENT")
            self._v("max_supply_ripple_mv", RIPPLE_LIMIT_MV, "requirement", "REQ-PWR-RIPPLE")
            if self.record_limit:
                self._v("max_allowed_junction_temp_c", TJ_LIMIT_C, "datasheet", "LM2596 §7.1")
            if self.stated_power:
                self._v("power_dissipation_w", POWER_W, "user", "stated in the intent")
        elif phase.id == "electronics.component_selection":
            pass  # a component_selection item; no design value
        elif phase.id == "electronics.power_budget":
            self._v("rail_load_margin_pct", 22.0, "analysis", "power.check_budget run")
            self._v("power_dissipation_w", POWER_W, "analysis", "power.check_budget run")
        elif phase.id == "electronics.schematic_check":
            self._v("erc_error_count", 0.0, "analysis", "kicad.run_erc report")
        elif phase.id == "electronics.circuit_simulation":
            self._v("supply_ripple_mv", 31.0, "analysis", "spice.run_simulation result")
        elif phase.id == "thermal.heat_load":
            self._v("heat_load_w", current["power_dissipation_w"], "analysis", "sum of dissipation")
        elif phase.id == "thermal.cooling_architecture":
            self.cooling = "forced" if context.retry_feedback.startswith("REWORK") else "natural"
            limit = current.get("max_allowed_junction_temp_c", TJ_LIMIT_C)
            budget = (limit - current["max_ambient_temp_c"]) / current["heat_load_w"]
            self._v("thermal_resistance_budget_c_per_w", budget, "analysis", "budget calc")
        elif phase.id == "thermal.heat_transfer":
            r = R_FORCED if self.cooling == "forced" else R_NATURAL
            tj = current["max_ambient_temp_c"] + current["heat_load_w"] * r
            self._v("max_junction_temp_c", tj, "analysis", "calculix.run_thermal result")
        elif phase.id == "thermal.temperature_verification":
            limit = current.get("max_allowed_junction_temp_c")
            if limit is not None:
                margin = limit - current["max_junction_temp_c"]
                self._v("thermal_margin_c", margin, "analysis", "verification")
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


async def _next_gate(store: InMemoryRunStore, run_id: str, seen: int) -> bool:
    for _ in range(1600):
        run = store.get(run_id)
        if run.status is RunStatus.AWAITING_APPROVAL and run.history.count(run.status) > seen:
            return True
        if run.is_terminal:
            return False
        await asyncio.sleep(0.005)
    raise AssertionError(f"never reached gate {seen + 1}: {store.get(run_id).status}")


async def _drive(
    flow: FlowDefinition,
    brain: ScriptedBrain,
    on_not_ready: Any,
) -> tuple[InMemoryRunStore, str, list[tuple[str, str, str]]]:
    """Run ``flow``; approve ready gates; hand not-ready ones to ``on_not_ready``."""
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "g", "flow": flow.id, "project_id": "board"})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, value_reader=brain.values
    )
    task = asyncio.create_task(executor.run(run.id, flow))
    seen = 0
    decisions: list[tuple[str, str, str]] = []
    while await _next_gate(store, run.id, seen):
        seen += 1
        phase = str((coord.gate_state(run.id) or {}).get("phase"))
        reason = store.get(run.id).approval_reason or ""
        if "NOT READY" in reason:
            action = on_not_ready(phase, reason, coord, run.id)
            decisions.append((phase, action, reason))
            store.submit_approval(
                run.id,
                ApprovalDecision.REWORK if action == "rework" else ApprovalDecision.REJECT,
            )
        else:
            decisions.append((phase, "approve", reason))
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)
    return store, run.id, decisions


def _requirements(flow: FlowDefinition, values: dict[str, float]) -> list[dict[str, str]]:
    """The requirement matrix, from the real checks over the final values."""
    rows = []
    for stage in ("design", "simulation"):
        for result in evaluate_stage(stage, flow.phases, values).results:
            status = {
                CheckOutcome.PASS: "pass",
                CheckOutcome.FAIL: "fail",
                CheckOutcome.NO_DATA: "no_data",
            }[result.outcome]
            rows.append({"id": f"{result.task}:{result.check}", "status": status})
    return rows


# -- story 8 -------------------------------------------------------------------


def _thermal() -> FlowDefinition:
    return compose(get_flow("design_v1"), [load_fragments()["thermal_v1"]]).definition


@pytest.mark.asyncio
async def test_thermal_runs_end_to_end_and_verifies_only_after_a_passing_analysis() -> None:
    flow = _thermal()
    values = ValueStore()
    brain = ScriptedBrain(values)

    def rework(phase: str, reason: str, coord: GateCoordinator, run_id: str) -> str:
        assert phase == "simulation", reason
        assert "max_junction_temp_c" in reason and "failed" in reason
        coord.note_rework(run_id, "thermal.cooling_architecture", "118 degC > 105: add airflow")
        return "rework"

    store, run_id, decisions = await _drive(flow, brain, rework)
    assert [d[:2] for d in decisions if d[1] != "approve"] == [("simulation", "rework")]

    ran = [c[0] for c in brain.calls]
    first = ran.index("simulation")
    # Local repair: the heat load is not recomputed; the cooling choice and
    # everything that reads it is.
    assert ran[first + 1 :] == [
        "thermal.cooling_architecture",
        "design",
        "thermal.heat_transfer",
        "thermal.temperature_verification",
        "simulation",
    ]
    assert ran.count("thermal.heat_load") == 1

    final = store.get(run_id)
    assert final.status is RunStatus.COMPLETED
    current = await values.values(None)
    assert current["max_junction_temp_c"] == pytest.approx(AMBIENT_C + POWER_W * R_FORCED)
    view = lifecycle_view(
        flow.phases, _in_process_state(final), requirements=_requirements(flow, current)
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )


@pytest.mark.asyncio
async def test_thermal_without_a_recorded_limit_never_passes() -> None:
    flow = _thermal()
    values = ValueStore()
    brain = ScriptedBrain(values, record_limit=False)
    seen: list[str] = []

    def stop(phase: str, reason: str, coord: GateCoordinator, run_id: str) -> str:
        seen.append(reason)
        return "reject"

    store, run_id, decisions = await _drive(flow, brain, stop)
    assert decisions[-1][:2] == ("simulation", "reject")
    assert "no data" in seen[0] and "max_allowed_junction_temp_c" in seen[0]
    assert store.get(run_id).status is RunStatus.REJECTED
    view = lifecycle_view(
        flow.phases,
        _in_process_state(store.get(run_id)),
        requirements=_requirements(flow, await values.values(None)),
    )
    assert not view.completion.verified


@pytest.mark.asyncio
async def test_an_unsourced_value_does_not_count() -> None:
    values = ValueStore()
    values.record("max_allowed_junction_temp_c", 105.0, None)
    assert "max_allowed_junction_temp_c" not in await values.values(None)


# -- story 9 -------------------------------------------------------------------


def _board() -> FlowDefinition:
    fr = load_fragments()
    return compose(get_flow("design_v1"), [fr["electronics_v1"], fr["thermal_v1"]]).definition


@pytest.mark.asyncio
async def test_power_board_with_cooling_verifies_and_a_changed_dissipation_is_local() -> None:
    flow = _board()
    values = ValueStore()
    brain = ScriptedBrain(values, stated_power=False)

    def rework(phase: str, reason: str, coord: GateCoordinator, run_id: str) -> str:
        coord.note_rework(run_id, "thermal.cooling_architecture", "too hot: add airflow")
        return "rework"

    store, run_id, _ = await _drive(flow, brain, rework)
    final = store.get(run_id)
    assert final.status is RunStatus.COMPLETED
    current = await values.values(None)
    view = lifecycle_view(
        flow.phases, _in_process_state(final), requirements=_requirements(flow, current)
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )

    # The power budget feeds the heat load: composition wired them together.
    heat_load = next(p for p in flow.phases if p.id == "thermal.heat_load")
    assert "electronics.power_budget" in (heat_load.depends_on or ())

    # A changed regulator: a new revision of the board's power dissipation.
    changed = lifecycle_view(
        flow.phases,
        _in_process_state(final),
        stale_item_keys=[ref_key("design_value:power_dissipation_w")],
        requirements=_requirements(flow, current),
    )
    validity = {n.id: n.validity for n in changed.nodes}
    assert validity["electronics.power_budget"] is Validity.STALE
    for pid in (
        "thermal.heat_load",
        "thermal.cooling_architecture",
        "thermal.heat_transfer",
        "thermal.temperature_verification",
    ):
        assert validity[pid] is Validity.POTENTIALLY_INVALID, pid
    for pid in (
        "electronics.component_selection",
        "electronics.schematic_check",
        "electronics.circuit_simulation",
        "requirements",
    ):
        assert validity[pid] is Validity.VALID, pid
    assert not changed.completion.verified


@pytest.mark.asyncio
async def test_a_rework_to_the_power_budget_reruns_only_what_reads_it() -> None:
    flow = _board()
    values = ValueStore()
    brain = ScriptedBrain(values, stated_power=False)
    reworked: list[bool] = []

    def rework(phase: str, reason: str, coord: GateCoordinator, run_id: str) -> str:
        coord.note_rework(run_id, "thermal.cooling_architecture", "too hot: add airflow")
        return "rework"

    # Approve every ready gate, except: send the V&V gate back to the power
    # budget once (a regulator swap), even though it was ready.
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "g", "flow": flow.id, "project_id": "board"})
    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord, value_reader=values)
    task = asyncio.create_task(executor.run(run.id, flow))
    seen = 0
    while await _next_gate(store, run.id, seen):
        seen += 1
        phase = str((coord.gate_state(run.id) or {}).get("phase"))
        reason = store.get(run.id).approval_reason or ""
        if "NOT READY" in reason:
            rework(phase, reason, coord, run.id)
            store.submit_approval(run.id, ApprovalDecision.REWORK)
        elif phase == "simulation" and not reworked:
            reworked.append(True)
            coord.note_rework(run.id, "electronics.power_budget", "regulator swapped")
            store.submit_approval(run.id, ApprovalDecision.REWORK)
        else:
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)
    assert store.get(run.id).status is RunStatus.COMPLETED

    ran = [c[0] for c in brain.calls]
    # Ran once: what never reads the power budget's outputs.
    for pid in (
        "electronics.component_selection",
        "electronics.schematic_check",
        "electronics.circuit_simulation",
    ):
        assert ran.count(pid) == 1, (pid, ran)
    # Re-ran: the budget and everything downstream of its data.
    assert ran.count("electronics.power_budget") == 2
    assert ran.count("thermal.heat_load") == 2
    assert ran.count("thermal.heat_transfer") >= 2
