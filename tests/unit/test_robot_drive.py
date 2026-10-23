"""Mobile-robot drive sizing and kinematics (FORGE-671).

Fixture numbers, each with a made-up but explicit source: the point is the
arithmetic and the refusal to assume, not a real robot.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from tool_registry.tools.robot.adapter import RobotServer
from tool_registry.tools.robot.drive import (
    STANDARD_GRAVITY_M_S2,
    DriveRequest,
    KinematicsRequest,
    diff_drive_kinematics,
    size_drive,
)


def f(value: float, source: str = "fixture") -> dict:
    return {"value": value, "source": source}


def _drive(**over: object) -> DriveRequest:
    base: dict = {
        "mass_kg": f(40.0, "mass roll-up"),
        "wheel_radius_m": f(0.1, "wheel datasheet"),
        "driven_wheels": 2,
        "max_slope_deg": f(10.0, "REQ-SLOPE"),
        "rolling_resistance": f(0.02, "tyre on concrete, handbook"),
        "max_acceleration_mps2": f(0.5, "REQ-ACCEL"),
        "max_speed_mps": f(1.5, "REQ-SPEED"),
        "drivetrain_efficiency": f(0.8, "gearbox datasheet"),
        "required_torque_margin": f(0.25, "REQ-MARGIN"),
    }
    base.update(over)
    return DriveRequest.model_validate(base)


def _hand_force() -> float:
    theta = math.radians(10.0)
    return 40.0 * STANDARD_GRAVITY_M_S2 * (0.02 * math.cos(theta) + math.sin(theta)) + 40.0 * 0.5


def test_worst_case_traction_torque_speed_and_power() -> None:
    result = size_drive(_drive())
    force = _hand_force()
    assert result.traction_force_n == pytest.approx(force)
    assert result.wheel_torque_nm == pytest.approx(force * 0.1 / 2)
    assert result.wheel_speed_rpm == pytest.approx(1.5 / (2 * math.pi * 0.1) * 60)
    assert result.peak_power_w == pytest.approx(force * 1.5 / 0.8)
    # No motor given: requirements only, no verdict.
    assert result.verdict == "not_established"
    assert "no candidate motor" in result.findings[0]


def _motor(rated: float, no_load: float = 4000.0, ratio: float = 20.0) -> dict:
    return {
        "name": "M1",
        "rated_torque_nm": f(rated, "motor datasheet p.2"),
        "no_load_speed_rpm": f(no_load, "motor datasheet p.2"),
        "gear_ratio": f(ratio, "gearbox datasheet"),
    }


def test_a_motor_with_enough_margin_passes() -> None:
    required = _hand_force() * 0.1 / 2 / (20.0 * 0.8)
    result = size_drive(_drive(motor=_motor(rated=required * 1.5)))
    assert result.motor_torque_required_nm == pytest.approx(required)
    assert result.torque_margin == pytest.approx(0.5)
    assert result.verdict == "pass"


def test_a_motor_short_of_the_required_margin_fails() -> None:
    required = _hand_force() * 0.1 / 2 / (20.0 * 0.8)
    result = size_drive(_drive(motor=_motor(rated=required * 1.1)))
    assert result.verdict == "fail"
    assert "below the required 25%" in result.findings[0]


def test_a_motor_too_slow_for_the_top_speed_fails() -> None:
    result = size_drive(_drive(motor=_motor(rated=10.0, no_load=1000.0)))
    assert result.speed_margin is not None and result.speed_margin < 0
    assert result.verdict == "fail"


def test_a_missing_figure_is_unknown_never_assumed() -> None:
    result = size_drive(_drive(rolling_resistance=None, motor=_motor(rated=10.0)))
    assert "rolling_resistance" in result.unknowns
    assert result.traction_force_n is None
    assert result.verdict == "not_established"


def test_a_figure_without_a_source_is_refused() -> None:
    with pytest.raises(ValidationError):
        _drive(mass_kg={"value": 40.0, "source": ""})


@pytest.mark.parametrize(
    "field", [("max_slope_deg", 95.0), ("drivetrain_efficiency", 1.2), ("mass_kg", -1.0)]
)
def test_out_of_range_figures_are_refused(field: tuple[str, float]) -> None:
    name, value = field
    with pytest.raises(ValidationError):
        _drive(**{name: f(value)})


def _kin(**over: object) -> KinematicsRequest:
    base: dict = {
        "wheel_radius_m": f(0.1),
        "track_width_m": f(0.5),
        "motor_max_speed_rpm": f(3000.0, "motor datasheet, speed at rated load"),
        "gear_ratio": f(20.0),
        "required_speed_mps": f(1.5, "REQ-SPEED"),
        "required_yaw_rate_dps": f(90.0, "REQ-TURN"),
    }
    base.update(over)
    return KinematicsRequest.model_validate(base)


def test_differential_drive_speed_and_yaw_rate() -> None:
    result = diff_drive_kinematics(_kin())
    v = 3000.0 / 20.0 * 2 * math.pi / 60 * 0.1
    assert result.max_speed_mps == pytest.approx(v)
    assert result.max_yaw_rate_dps == pytest.approx(math.degrees(2 * v / 0.5))
    assert result.min_turning_radius_m == 0.0
    assert result.verdict == "pass"


def test_too_slow_a_drive_fails_its_speed_requirement() -> None:
    result = diff_drive_kinematics(_kin(required_speed_mps=f(3.0, "REQ-SPEED")))
    assert result.verdict == "fail"
    assert "below the required 3 m/s" in result.findings[0]


def test_kinematics_without_a_requirement_is_not_established() -> None:
    result = diff_drive_kinematics(_kin(required_speed_mps=None))
    assert result.verdict == "not_established"
    assert "required_speed_mps" in result.unknowns


@pytest.mark.asyncio
async def test_the_adapter_serves_both_tools() -> None:
    server = RobotServer()
    assert {"robot.size_drive", "robot.diff_drive_kinematics"} <= set(server.tool_ids)
    out = await server.size_drive(_drive().model_dump())
    assert out["verdict"] == "not_established" and out["traction_force_n"] > 0
    out = await server.diff_drive_kinematics(_kin().model_dump())
    assert out["verdict"] == "pass"
