"""Mobile robot, end to end (FORGE-671).

``mobile_robot_v1`` composed into ``design_v1`` and run on the real
in-process engine: real composition, gates, task checks, the engine-driven
energy loop (mass <-> drive power <-> battery capacity) and the lifecycle
verdict. The drive and kinematics numbers come from the real
``robot.size_drive`` / ``robot.diff_drive_kinematics`` arithmetic; only the
phase work around them is scripted (no LLM, no simulator). Every recorded
value carries a source; the store serves only sourced values, as the twin
does (FORGE-586).

Fixture numbers: a 10 kg-payload indoor robot, stated here, not design data.
"""

from __future__ import annotations

import asyncio
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
from tool_registry.tools.robot.drive import (
    DriveRequest,
    KinematicsRequest,
    diff_drive_kinematics,
    size_drive,
)

REQUIREMENTS = {
    "payload_mass_kg": 10.0,
    "max_slope_deg": 8.0,
    "rolling_resistance": 0.015,
    "max_speed_mps": 1.2,
    "max_acceleration_mps2": 0.5,
    "drivetrain_efficiency": 0.8,
    "required_runtime_h": 4.0,
    "required_torque_margin": 0.25,
    "required_speed_mps": 1.2,
    "required_nav_success_rate": 0.9,
}
DRY_MASS_KG = 18.0  # structure, drive and electronics (scripted roll-up)
CELL_WH_PER_KG = 150.0  # cell datasheet (fixture)
ELECTRONICS_W = 15.0  # electronics load (fixture)
DUTY = 0.35  # mean / peak drive power over the mission (fixture)
USABLE_DOD = 0.8  # cell datasheet (fixture)
MOTOR = {"rated_torque_nm": 0.6, "no_load_speed_rpm": 3500.0, "gear_ratio": 20.0}
MOTOR_SPEED_AT_LOAD_RPM = 3000.0


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


class RobotBrain:
    def __init__(self, values: ValueStore) -> None:
        self.values = values
        self.calls: list[str] = []

    def current(self) -> dict[str, float]:
        return {n: v for n, v, s in self.values.records if s}

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append(phase.id)
        rec, now = self.values.record, self.current()
        if phase.id == "requirements":
            for name, value in REQUIREMENTS.items():
                rec(name, value, f"REQ-{name.upper()}")
        elif phase.id == "mobile_robot.locomotion":
            rec("driven_wheel_count", 2, "differential drive (decision)")
            rec("wheel_radius_m", 0.1, "wheel datasheet")
            rec("track_width_m", 0.45, "envelope REQ-ENV")
        elif phase.id == "mobile_robot.energy_closure":
            # One pass of the loop, each step on the values the previous set.
            battery = now.get("battery_capacity_wh", 0.0)
            mass = DRY_MASS_KG + now["payload_mass_kg"] + battery / CELL_WH_PER_KG
            rec("total_mass_kg", mass, "mass roll-up")
            drive = size_drive(
                DriveRequest.model_validate(
                    {
                        "mass_kg": fig(mass, "mass roll-up"),
                        "wheel_radius_m": fig(now["wheel_radius_m"], "wheel datasheet"),
                        "driven_wheels": int(now["driven_wheel_count"]),
                        "max_slope_deg": fig(now["max_slope_deg"], "REQ"),
                        "rolling_resistance": fig(now["rolling_resistance"], "REQ"),
                        "max_acceleration_mps2": fig(now["max_acceleration_mps2"], "REQ"),
                        "max_speed_mps": fig(now["max_speed_mps"], "REQ"),
                        "drivetrain_efficiency": fig(now["drivetrain_efficiency"], "REQ"),
                        "required_torque_margin": fig(now["required_torque_margin"], "REQ"),
                        "motor": {
                            "name": "M1",
                            **{k: fig(v, "motor datasheet") for k, v in MOTOR.items()},
                        },
                    }
                )
            )
            assert drive.peak_power_w is not None and drive.torque_margin is not None
            rec("drive_power_w", drive.peak_power_w, "robot.size_drive run")
            rec("motor_torque_margin", drive.torque_margin, "robot.size_drive run")
            rec("gear_ratio", MOTOR["gear_ratio"], "gearbox datasheet")
            wh = (drive.peak_power_w * DUTY + ELECTRONICS_W) * now["required_runtime_h"]
            rec("battery_capacity_wh", wh / USABLE_DOD, "runtime x mean load / usable DoD")
        elif phase.id == "mobile_robot.kinematics":
            kin = diff_drive_kinematics(
                KinematicsRequest.model_validate(
                    {
                        "wheel_radius_m": fig(now["wheel_radius_m"], "wheel datasheet"),
                        "track_width_m": fig(now["track_width_m"], "REQ-ENV"),
                        "motor_max_speed_rpm": fig(MOTOR_SPEED_AT_LOAD_RPM, "motor datasheet"),
                        "gear_ratio": fig(now["gear_ratio"], "gearbox datasheet"),
                        "required_speed_mps": fig(now["required_speed_mps"], "REQ"),
                    }
                )
            )
            assert kin.max_speed_mps is not None
            rec("max_speed_capability_mps", kin.max_speed_mps, "robot.diff_drive_kinematics run")
        elif phase.id == "mobile_robot.dynamics":
            rec("slope_climb_speed_mps", 0.7, "gazebo.run_simulation result")
        elif phase.id == "mobile_robot.navigation":
            rec("nav_success_rate", 0.95, "19 of 20 simulated trials")
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


def _flow() -> FlowDefinition:
    return compose(get_flow("design_v1"), [load_fragments()["mobile_robot_v1"]]).definition


async def _drive_run(flow: FlowDefinition, brain: RobotBrain) -> tuple[InMemoryRunStore, str]:
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "an indoor delivery robot", "flow": flow.id, "project_id": "amr"})
    executor = DesignFlowExecutor(
        store=store, brain=brain, coordinator=coord, value_reader=brain.values
    )
    task = asyncio.create_task(executor.run(run.id, flow))
    seen = 0
    while True:
        for _ in range(1600):
            current = store.get(run.id)
            if current.is_terminal:
                break
            if (
                current.status is RunStatus.AWAITING_APPROVAL
                and current.history.count(current.status) > seen
            ):
                break
            await asyncio.sleep(0.005)
        current = store.get(run.id)
        if current.is_terminal:
            break
        seen += 1
        reason = current.approval_reason or ""
        assert "NOT READY" not in reason, reason
        store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)
    return store, run.id


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
async def test_a_mobile_robot_runs_end_to_end_and_verifies() -> None:
    flow = _flow()
    values = ValueStore()
    brain = RobotBrain(values)
    store, run_id = await _drive_run(flow, brain)
    assert store.get(run_id).status is RunStatus.COMPLETED

    # The engine iterated the energy loop until it settled.
    passes = brain.calls.count("mobile_robot.energy_closure")
    assert 3 <= passes < 20
    history = (await values.history(None, ["total_mass_kg"]))["total_mass_kg"]
    assert abs(history[-1] - history[-2]) / history[-1] < 0.01

    current = await values.values(None)
    assert current["motor_torque_margin"] >= REQUIREMENTS["required_torque_margin"]
    loop = await values.history(None, ["total_mass_kg", "drive_power_w", "battery_capacity_wh"])
    view = lifecycle_view(
        flow.phases,
        _in_process_state(store.get(run_id)),
        requirements=_requirements(flow, current, loop),
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )


def test_navigation_testing_is_a_blocking_gap_until_its_adapter_exists() -> None:
    flow = _flow()
    registered = {
        "twin.record_engineering_entity",
        "twin.record_decision",
        "robot.size_drive",
        "robot.diff_drive_kinematics",
        "gazebo.run_simulation",
    }
    report = assess_capabilities(flow.phases, producers={}, registered=registered)
    tool_gaps = {g.phase_id: g for g in report.gaps if g.capability.startswith("tool:")}
    assert set(tool_gaps) == {"mobile_robot.navigation"}
    gap = tool_gaps["mobile_robot.navigation"]
    assert gap.capability == "tool:gazebo.run_navigation_scenario"
    assert gap.severity is GapSeverity.REQUIRES_NEW_CAPABILITY and gap.blocking
    assert report.status == "BLOCKED"


def test_a_heavier_payload_reruns_the_energy_loop_not_the_locomotion_choice() -> None:
    from orchestrator.design_flow.graph import build_graph

    graph = build_graph(_flow().phases)
    # payload_mass_kg is read by the block; locomotion reads nothing upstream of it.
    assert "mobile_robot.locomotion" not in graph.affected("mobile_robot.energy_closure")
    assert "mobile_robot.navigation" in graph.affected("mobile_robot.energy_closure")
