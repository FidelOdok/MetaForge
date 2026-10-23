"""Fixed-wing aircraft, end to end (FORGE-670).

``aircraft_v1`` composed into ``design_v1`` and run on the real in-process
engine: real composition, gates, task checks, the engine-driven power loop
(all-up mass <-> cruise power <-> battery capacity) and the lifecycle verdict.
The wing, cruise, trim and endurance numbers come from the real
``aircraft.*`` arithmetic; only the phase work around them is scripted (no
LLM, no flight simulator -- the mission endurance is a scripted figure, which
is why ``jsbsim.run_mission`` is still a capability gap). Every recorded
value carries a source.

The run: the first layout puts the CG too far aft; the static margin falls
short of the requirement at the V&V gate, the reviewer sends the run back to
the layout, the CG moves forward, and the run verifies -- without re-running
the power loop, which reads nothing the layout writes. Fixture numbers, not
design data.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest

from api_gateway.runs.routes import _in_process_state
from orchestrator.design_flow.capabilities import GapSeverity, assess_capabilities
from orchestrator.design_flow.checks import CheckOutcome, evaluate_stage
from orchestrator.design_flow.compose import compose
from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.fragments import load_fragments
from orchestrator.design_flow.lifecycle import CompletionClass, lifecycle_view
from orchestrator.design_flow.spec import FlowDefinition, Phase, get_flow
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus
from tool_registry.tools.aircraft.aero import (
    CruiseRequest,
    EnduranceRequest,
    TrimRequest,
    WingRequest,
    cruise_performance,
    electric_endurance,
    trim_stability,
    wing_sizing,
)

REQUIREMENTS = {
    "max_takeoff_mass_kg": 6.0,
    "max_stall_speed_mps": 12.0,
    "payload_mass_kg": 1.0,
    "cruise_speed_mps": 18.0,
    "air_density_kg_m3": 1.225,  # ISA sea level, the condition the requirement names
    "required_endurance_min": 60.0,
    "required_static_margin": 0.10,
    "avionics_power_w": 10.0,
    "usable_battery_fraction": 0.8,
    "propulsive_efficiency": 0.55,  # propeller x motor x ESC datasheets
}
CONFIGURATION = {
    "aspect_ratio": 8.0,
    "cl_max": 1.3,
    "zero_lift_drag_coefficient": 0.03,
    "oswald_efficiency": 0.8,
}
DRY_MASS_KG = 3.2  # structure, propulsion and avionics (scripted roll-up)
CELL_WH_PER_KG = 180.0  # cell datasheet (fixture)
CHOSEN_WING_AREA_M2 = 0.55
AFT_CG, FORWARD_CG = 0.40, 0.30
AIRFOIL = {"lift_slope": 2 * math.pi, "cm_ac": -0.05, "tail_cl_max": 0.5}
TAIL = {"aspect_ratio": 4.0, "volume": 0.5, "efficiency": 0.9}


class ValueStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, float, str]] = []

    def record(self, name: str, value: float, source: str) -> None:
        self.records.append((name, float(value), source))

    async def values(self, project_id: str | None) -> dict[str, float]:
        return {n: v for n, v, s in self.records if s}

    async def history(self, project_id: str | None, names: list[str]) -> dict[str, list[float]]:
        return {n: [v for name, v, s in self.records if name == n and s] for n in names}


def fig(value: float, source: str) -> dict[str, Any]:
    return {"value": value, "source": source}


class AircraftBrain:
    def __init__(self, values: ValueStore) -> None:
        self.values = values
        self.calls: list[str] = []

    def current(self) -> dict[str, float]:
        return {n: v for n, v, s in self.values.records if s}

    def _trim(self, now: dict[str, float]) -> Any:
        return trim_stability(
            TrimRequest.model_validate(
                {
                    "wing_aspect_ratio": fig(now["aspect_ratio"], "configuration"),
                    "wing_airfoil_lift_slope_per_rad": fig(AIRFOIL["lift_slope"], "thin airfoil"),
                    "tail_aspect_ratio": fig(TAIL["aspect_ratio"], "layout"),
                    "tail_airfoil_lift_slope_per_rad": fig(AIRFOIL["lift_slope"], "thin airfoil"),
                    "tail_volume_coefficient": fig(now["tail_volume_coefficient"], "layout"),
                    "tail_efficiency": fig(TAIL["efficiency"], "textbook estimate"),
                    "wing_ac_position_mac": fig(0.25, "quarter chord"),
                    "cg_position_mac": fig(now["cg_position_mac"], "layout"),
                    "wing_cm_ac": fig(AIRFOIL["cm_ac"], "airfoil polar"),
                    "cruise_lift_coefficient": fig(now["cruise_lift_coefficient"], "cruise run"),
                    "tail_cl_max": fig(AIRFOIL["tail_cl_max"], "tail airfoil polar"),
                    "required_static_margin": fig(now["required_static_margin"], "REQ-STAB"),
                }
            )
        )

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append(phase.id)
        rec, now = self.values.record, self.current()
        if phase.id == "requirements":
            for name, value in REQUIREMENTS.items():
                rec(name, value, f"REQ-{name.upper()}")
        elif phase.id == "aircraft.configuration":
            for name, value in CONFIGURATION.items():
                rec(name, value, "airfoil polar and drag build-up")
        elif phase.id == "aircraft.wing_sizing":
            wing = wing_sizing(
                WingRequest.model_validate(
                    {
                        "mass_kg": fig(now["max_takeoff_mass_kg"], "REQ-MTOW"),
                        "max_stall_speed_mps": fig(now["max_stall_speed_mps"], "REQ-STALL"),
                        "cl_max": fig(now["cl_max"], "airfoil polar"),
                        "air_density_kg_m3": fig(now["air_density_kg_m3"], "ISA"),
                        "aspect_ratio": fig(now["aspect_ratio"], "configuration"),
                        "wing_area_m2": fig(CHOSEN_WING_AREA_M2, "rounded up from the sizing"),
                    }
                )
            )
            assert wing.verdict == "pass", wing.findings
            rec("wing_area_m2", CHOSEN_WING_AREA_M2, "aircraft.wing_sizing run")
            rec("wing_span_m", wing.wing_span_m or 0.0, "aircraft.wing_sizing run")
            rec("stall_speed_mps", wing.stall_speed_mps or 0.0, "aircraft.wing_sizing run")
        elif phase.id == "aircraft.layout":
            cg = FORWARD_CG if context.retry_feedback.startswith("REWORK") else AFT_CG
            rec("tail_volume_coefficient", TAIL["volume"], "tail area and moment arm")
            rec("cg_position_mac", cg, "layout mass breakdown")
        elif phase.id == "aircraft.power_closure":
            # One pass of the loop, each step on the values the previous set.
            battery = now.get("battery_capacity_wh", 0.0)
            mass = DRY_MASS_KG + now["payload_mass_kg"] + battery / CELL_WH_PER_KG
            rec("total_mass_kg", mass, "mass roll-up")
            cruise = cruise_performance(
                CruiseRequest.model_validate(
                    {
                        "mass_kg": fig(mass, "mass roll-up"),
                        "wing_area_m2": fig(now["wing_area_m2"], "wing sizing"),
                        "aspect_ratio": fig(now["aspect_ratio"], "configuration"),
                        "oswald_efficiency": fig(now["oswald_efficiency"], "configuration"),
                        "cd0": fig(now["zero_lift_drag_coefficient"], "drag build-up"),
                        "air_density_kg_m3": fig(now["air_density_kg_m3"], "ISA"),
                        "cruise_speed_mps": fig(now["cruise_speed_mps"], "REQ-CRUISE"),
                        "propulsive_efficiency": fig(now["propulsive_efficiency"], "datasheets"),
                        "cl_max": fig(now["cl_max"], "airfoil polar"),
                    }
                )
            )
            assert cruise.verdict == "pass", cruise.findings
            assert cruise.electrical_power_w and cruise.cruise_lift_coefficient
            rec("cruise_power_w", cruise.electrical_power_w, "aircraft.cruise_performance run")
            rec(
                "cruise_lift_coefficient",
                cruise.cruise_lift_coefficient,
                "aircraft.cruise_performance run",
            )
            rec("lift_to_drag", cruise.lift_to_drag or 0.0, "aircraft.cruise_performance run")
            sizing = electric_endurance(
                EnduranceRequest.model_validate(
                    {
                        "usable_fraction": fig(now["usable_battery_fraction"], "cell datasheet"),
                        "propulsion_power_w": fig(cruise.electrical_power_w, "cruise run"),
                        "avionics_power_w": fig(now["avionics_power_w"], "REQ-AVIONICS"),
                        "required_endurance_min": fig(now["required_endurance_min"], "REQ"),
                    }
                )
            )
            assert sizing.required_capacity_wh
            rec("battery_capacity_wh", sizing.required_capacity_wh, "aircraft.electric_endurance")
        elif phase.id in ("aircraft.trim", "aircraft.stability"):
            result = self._trim(now)
            if phase.id == "aircraft.trim":
                assert result.trim_tail_cl_margin is not None
                rec("trim_tail_cl_margin", result.trim_tail_cl_margin, "aircraft.trim_stability")
            else:
                assert result.static_margin is not None
                rec("static_margin", result.static_margin, "aircraft.trim_stability run")
        elif phase.id == "aircraft.flight_simulation":
            rec("mission_endurance_min", 61.0, "mission simulation result (scripted fixture)")
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


def _flow() -> FlowDefinition:
    return compose(get_flow("design_v1"), [load_fragments()["aircraft_v1"]]).definition


def _requirements(
    flow: FlowDefinition, values: dict[str, float], history: dict[str, list[float]]
) -> list[dict[str, str]]:
    """The requirement matrix, from the real checks (convergence included)."""
    rows = []
    for stage in ("design", "simulation"):
        for result in evaluate_stage(stage, flow.phases, values, history).results:
            status = {
                CheckOutcome.PASS: "pass",
                CheckOutcome.FAIL: "fail",
                CheckOutcome.NO_DATA: "no_data",
            }[result.outcome]
            rows.append({"id": f"{result.task}:{result.check}", "status": status})
    return rows


@pytest.mark.asyncio
async def test_an_aft_cg_is_reworked_in_the_layout_and_the_aircraft_verifies() -> None:
    flow = _flow()
    values = ValueStore()
    brain = AircraftBrain(values)
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "a 60-minute survey UAV", "flow": flow.id, "project_id": "uav"})
    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord, value_reader=values)
    task = asyncio.create_task(executor.run(run.id, flow))
    seen, decisions, loop_passes_at_rework = 0, [], -1
    while True:
        for _ in range(1600):
            current = store.get(run.id)
            if current.is_terminal or (
                current.status is RunStatus.AWAITING_APPROVAL
                and current.history.count(current.status) > seen
            ):
                break
            await asyncio.sleep(0.005)
        current = store.get(run.id)
        if current.is_terminal:
            break
        seen += 1
        phase = str((coord.gate_state(run.id) or {}).get("phase"))
        reason = current.approval_reason or ""
        if "NOT READY" in reason:
            assert phase == "simulation" and "static_margin" in reason, reason
            decisions.append((phase, "rework"))
            loop_passes_at_rework = brain.calls.count("aircraft.power_closure")
            coord.note_rework(run.id, "aircraft.layout", "static margin short: move the CG forward")
            store.submit_approval(run.id, ApprovalDecision.REWORK)
        else:
            decisions.append((phase, "approve"))
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)

    assert ("simulation", "rework") in decisions
    assert store.get(run.id).status is RunStatus.COMPLETED
    # The engine iterated the power loop until it settled.
    passes = brain.calls.count("aircraft.power_closure")
    assert 3 <= passes < 20
    history = (await values.history(None, ["total_mass_kg"]))["total_mass_kg"]
    assert abs(history[-1] - history[-2]) / history[-1] < 0.01
    # The rework followed the data: the layout and what reads it re-ran;
    # the power loop and the wing did not.
    assert brain.calls.count("aircraft.layout") == 2
    assert brain.calls.count("aircraft.stability") == 2
    assert brain.calls.count("aircraft.wing_sizing") == 1
    assert passes == loop_passes_at_rework

    current = await values.values(None)
    assert current["cg_position_mac"] == FORWARD_CG
    assert current["static_margin"] >= REQUIREMENTS["required_static_margin"]
    assert current["total_mass_kg"] <= REQUIREMENTS["max_takeoff_mass_kg"]
    loop = await values.history(None, ["total_mass_kg", "cruise_power_w", "battery_capacity_wh"])
    view = lifecycle_view(
        flow.phases,
        _in_process_state(store.get(run.id)),
        requirements=_requirements(flow, current, loop),
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )


def test_flight_simulation_is_a_blocking_gap_until_its_adapter_exists() -> None:
    registered = {
        "twin.record_engineering_entity",
        "twin.record_decision",
        "aircraft.wing_sizing",
        "aircraft.cruise_performance",
        "aircraft.trim_stability",
        "aircraft.electric_endurance",
    }
    report = assess_capabilities(_flow().phases, producers={}, registered=registered)
    tool_gaps = {g.phase_id: g for g in report.gaps if g.capability.startswith("tool:")}
    assert set(tool_gaps) == {"aircraft.flight_simulation"}
    gap = tool_gaps["aircraft.flight_simulation"]
    assert gap.capability == "tool:jsbsim.run_mission"
    assert gap.severity is GapSeverity.REQUIRES_NEW_CAPABILITY and gap.blocking
    assert report.status == "BLOCKED"


def test_a_layout_change_reruns_trim_and_stability_not_the_power_loop() -> None:
    from orchestrator.design_flow.graph import build_graph

    graph = build_graph(_flow().phases)
    affected = graph.affected("aircraft.layout")
    assert {"aircraft.trim", "aircraft.stability"} <= set(affected)
    assert "aircraft.power_closure" not in affected
    assert "aircraft.wing_sizing" not in affected
    # A heavier payload re-runs the loop and everything that reads it.
    assert "aircraft.flight_simulation" in graph.affected("aircraft.power_closure")
