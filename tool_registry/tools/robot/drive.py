"""Wheeled mobile-robot drive sizing and kinematics (FORGE-671).

Pure arithmetic over figures the caller supplies, like the power budget
(FORGE-544): nothing is looked up and nothing is assumed. Every physical
figure arrives as a :class:`Figure` carrying its source. A figure that is
not given is *unknown*, and a result that depends on an unknown is
``not_established``, never a guess.

**Drive sizing** (:func:`size_drive`). The worst-case traction force is
taken, conservatively, as climbing the steepest slope *while* accelerating
at the maximum rate::

    F = m * g * (Crr * cos(theta) + sin(theta)) + m * a

shared equally across the driven wheels. Per driven wheel that gives the
torque ``F * r / n`` and the speed ``v / r``. Peak mechanical power is the
worst-case force at top speed, through the drivetrain efficiency. With a
candidate motor and gearbox, the required motor torque
``T_wheel / (ratio * efficiency)`` and speed ``n_wheel * ratio`` are compared
with the motor's rated torque and no-load speed, against the margin the
requirements ask for.

**Differential-drive kinematics** (:func:`diff_drive_kinematics`). Two
independently driven wheels ``track_width`` apart: the top linear speed is
the wheel's top surface speed, and the top yaw rate (spinning in place, the
wheels at opposite full speed) is ``2 * v / track_width``.

Units: SI throughout (kg, m, s, N, N*m, W), with speeds also in rpm for
comparison with a motor datasheet.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "DriveRequest",
    "DriveResult",
    "Figure",
    "KinematicsRequest",
    "KinematicsResult",
    "Motor",
    "diff_drive_kinematics",
    "size_drive",
]

#: Standard gravity (CODATA), m/s^2 -- a defined constant, not a design value.
STANDARD_GRAVITY_M_S2 = 9.80665

Verdict = Literal["pass", "fail", "not_established"]


class Figure(BaseModel):
    """One engineering figure and where it came from."""

    value: float
    source: str = Field(..., min_length=1, description="Datasheet, requirement, measurement")

    @model_validator(mode="after")
    def _finite(self) -> Figure:
        if not math.isfinite(self.value):
            raise ValueError("a figure must be finite")
        return self


class Motor(BaseModel):
    """A candidate drive motor and its gearbox."""

    name: str = Field(..., min_length=1)
    rated_torque_nm: Figure | None = Field(
        default=None, description="Continuous rated torque at the motor shaft"
    )
    no_load_speed_rpm: Figure | None = None
    gear_ratio: Figure | None = Field(default=None, description="Motor turns per wheel turn")


class DriveRequest(BaseModel):
    mass_kg: Figure | None = Field(default=None, description="All-up mass, payload included")
    wheel_radius_m: Figure | None = None
    driven_wheels: int = Field(..., ge=1, le=16)
    max_slope_deg: Figure | None = None
    rolling_resistance: Figure | None = Field(default=None, description="Coefficient Crr")
    max_acceleration_mps2: Figure | None = None
    max_speed_mps: Figure | None = None
    drivetrain_efficiency: Figure | None = Field(default=None, description="0 < eta <= 1")
    required_torque_margin: Figure | None = Field(
        default=None, description="e.g. 0.25 for 25 % above the worst case (from requirements)"
    )
    motor: Motor | None = None

    @model_validator(mode="after")
    def _ranges(self) -> DriveRequest:
        checks = [
            ("mass_kg", self.mass_kg, lambda v: v > 0),
            ("wheel_radius_m", self.wheel_radius_m, lambda v: v > 0),
            ("max_slope_deg", self.max_slope_deg, lambda v: 0 <= v < 90),
            ("rolling_resistance", self.rolling_resistance, lambda v: v >= 0),
            ("max_acceleration_mps2", self.max_acceleration_mps2, lambda v: v >= 0),
            ("max_speed_mps", self.max_speed_mps, lambda v: v > 0),
            ("drivetrain_efficiency", self.drivetrain_efficiency, lambda v: 0 < v <= 1),
            ("required_torque_margin", self.required_torque_margin, lambda v: v >= 0),
        ]
        for name, fig, ok in checks:
            if fig is not None and not ok(fig.value):
                raise ValueError(f"{name} = {fig.value} is out of range")
        return self


class DriveResult(BaseModel):
    verdict: Verdict
    traction_force_n: float | None = None
    wheel_torque_nm: float | None = None
    wheel_speed_rpm: float | None = None
    peak_power_w: float | None = None
    motor_torque_required_nm: float | None = None
    motor_speed_required_rpm: float | None = None
    torque_margin: float | None = None
    speed_margin: float | None = None
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def _v(fig: Figure | None) -> float | None:
    return None if fig is None else fig.value


def size_drive(request: DriveRequest) -> DriveResult:
    """Worst-case traction, wheel torque and speed, and the motor's margins."""
    unknowns: list[str] = []
    for name in (
        "mass_kg",
        "wheel_radius_m",
        "max_slope_deg",
        "rolling_resistance",
        "max_acceleration_mps2",
        "max_speed_mps",
        "drivetrain_efficiency",
    ):
        if getattr(request, name) is None:
            unknowns.append(name)
    assumptions = [
        "worst case taken as climbing the maximum slope while accelerating at the maximum rate",
        "traction shared equally across the driven wheels; no wheel slip",
        f"g = {STANDARD_GRAVITY_M_S2} m/s^2 (standard gravity)",
    ]
    m, r = _v(request.mass_kg), _v(request.wheel_radius_m)
    slope, crr = _v(request.max_slope_deg), _v(request.rolling_resistance)
    accel, vmax = _v(request.max_acceleration_mps2), _v(request.max_speed_mps)
    eta = _v(request.drivetrain_efficiency)
    n = request.driven_wheels

    force = None
    if m is not None and slope is not None and crr is not None and accel is not None:
        theta = math.radians(slope)
        g = STANDARD_GRAVITY_M_S2
        force = m * g * (crr * math.cos(theta) + math.sin(theta)) + m * accel
    wheel_torque = force * r / n if force is not None and r is not None else None
    wheel_rpm = vmax / (2 * math.pi * r) * 60 if vmax is not None and r is not None else None
    power = force * vmax / eta if None not in (force, vmax, eta) else None  # type: ignore[operator]

    result = DriveResult(
        verdict="not_established",
        traction_force_n=force,
        wheel_torque_nm=wheel_torque,
        wheel_speed_rpm=wheel_rpm,
        peak_power_w=power,
        unknowns=unknowns,
        assumptions=assumptions,
    )
    motor = request.motor
    if motor is None:
        result.findings.append("no candidate motor given: drive requirements only, no verdict")
        return result

    ratio = _v(motor.gear_ratio)
    rated = _v(motor.rated_torque_nm)
    no_load = _v(motor.no_load_speed_rpm)
    margin_needed = _v(request.required_torque_margin)
    for name, value in (
        ("motor.gear_ratio", ratio),
        ("motor.rated_torque_nm", rated),
        ("motor.no_load_speed_rpm", no_load),
        ("required_torque_margin", margin_needed),
    ):
        if value is None:
            result.unknowns.append(name)
    if ratio is not None and wheel_torque is not None and eta is not None:
        result.motor_torque_required_nm = wheel_torque / (ratio * eta)
    if ratio is not None and wheel_rpm is not None:
        result.motor_speed_required_rpm = wheel_rpm * ratio
    if rated is not None and result.motor_torque_required_nm:
        result.torque_margin = rated / result.motor_torque_required_nm - 1
    if no_load is not None and result.motor_speed_required_rpm:
        result.speed_margin = no_load / result.motor_speed_required_rpm - 1
        result.assumptions.append(
            "no-load speed is an upper bound: at the worst-case torque the motor runs slower"
        )

    failed = False
    if result.torque_margin is not None and margin_needed is not None:
        if result.torque_margin < margin_needed:
            failed = True
            result.findings.append(
                f"{motor.name}: torque margin {result.torque_margin:.0%} is below the required "
                f"{margin_needed:.0%}"
            )
    if result.speed_margin is not None and result.speed_margin < 0:
        failed = True
        result.findings.append(
            f"{motor.name}: no-load speed is below the speed the top speed needs"
        )
    if failed:
        result.verdict = "fail"
    elif (
        not result.unknowns and result.torque_margin is not None and result.speed_margin is not None
    ):
        result.verdict = "pass"
    return result


class KinematicsRequest(BaseModel):
    wheel_radius_m: Figure | None = None
    track_width_m: Figure | None = Field(default=None, description="Distance between the wheels")
    motor_max_speed_rpm: Figure | None = Field(
        default=None, description="Usable motor speed at the working load"
    )
    gear_ratio: Figure | None = None
    required_speed_mps: Figure | None = Field(default=None, description="From the requirements")
    required_yaw_rate_dps: Figure | None = Field(default=None, description="From the requirements")

    @model_validator(mode="after")
    def _positive(self) -> KinematicsRequest:
        for name in ("wheel_radius_m", "track_width_m", "motor_max_speed_rpm", "gear_ratio"):
            fig = getattr(self, name)
            if fig is not None and fig.value <= 0:
                raise ValueError(f"{name} must be positive")
        return self


class KinematicsResult(BaseModel):
    verdict: Verdict
    max_speed_mps: float | None = None
    max_yaw_rate_dps: float | None = None
    min_turning_radius_m: float | None = None
    speed_margin: float | None = None
    yaw_rate_margin: float | None = None
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)


def diff_drive_kinematics(request: KinematicsRequest) -> KinematicsResult:
    """Top speed and yaw rate of a differential drive, against the requirements."""
    unknowns = [
        name
        for name in ("wheel_radius_m", "track_width_m", "motor_max_speed_rpm", "gear_ratio")
        if getattr(request, name) is None
    ]
    r, w = _v(request.wheel_radius_m), _v(request.track_width_m)
    rpm, ratio = _v(request.motor_max_speed_rpm), _v(request.gear_ratio)
    result = KinematicsResult(verdict="not_established", unknowns=unknowns)
    if None in (r, rpm, ratio):
        return result
    wheel_rad_s = rpm / ratio * 2 * math.pi / 60  # type: ignore[operator]
    result.max_speed_mps = wheel_rad_s * r  # type: ignore[operator]
    # A differential drive can turn about its own centre.
    result.min_turning_radius_m = 0.0
    if w is not None:
        result.max_yaw_rate_dps = math.degrees(2 * result.max_speed_mps / w)
    need_v, need_yaw = _v(request.required_speed_mps), _v(request.required_yaw_rate_dps)
    if need_v is None:
        result.unknowns.append("required_speed_mps")
    else:
        result.speed_margin = result.max_speed_mps / need_v - 1 if need_v else None
    if need_yaw is not None and result.max_yaw_rate_dps is not None and need_yaw:
        result.yaw_rate_margin = result.max_yaw_rate_dps / need_yaw - 1
    failed = False
    if result.speed_margin is not None and result.speed_margin < 0:
        failed = True
        result.findings.append(
            f"top speed {result.max_speed_mps:.2f} m/s is below the required {need_v:g} m/s"
        )
    if result.yaw_rate_margin is not None and result.yaw_rate_margin < 0:
        failed = True
        result.findings.append(
            f"top yaw rate {result.max_yaw_rate_dps:.0f} deg/s is below the required {need_yaw:g}"
        )
    if failed:
        result.verdict = "fail"
    elif not result.unknowns:
        result.verdict = "pass"
    return result
