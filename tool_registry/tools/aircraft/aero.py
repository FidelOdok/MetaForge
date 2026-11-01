"""Fixed-wing aircraft sizing, cruise, trim and endurance arithmetic (FORGE-670).

Pure arithmetic over figures the caller supplies, like the power budget
(FORGE-544): nothing is looked up and nothing is assumed. Every figure is a
:class:`Figure` with its source; one not given is *unknown*, and a result
that depends on it is ``not_established``.

These are the classical first-order estimates a designer runs before a
vortex-lattice or panel code, never a replacement for one:

**Wing sizing** (:func:`wing_sizing`). The wing area that holds the stall
speed at the maximum take-off mass, ``S = 2 m g / (rho V_s^2 CL_max)``; with a
chosen area, the stall speed it gives; span ``b = sqrt(AR * S)``.

**Cruise performance** (:func:`cruise_performance`). The parabolic drag
polar ``CD = CD0 + CL^2 / (pi e AR)`` at the cruise lift coefficient
``CL = 2 m g / (rho V^2 S)``; drag, shaft power ``D V`` and electrical power
through the propulsive efficiency; and the best lift-to-drag ratio
``1 / (2 sqrt(k CD0))``.

**Trim and static stability** (:func:`trim_stability`). Each surface's
lift-curve slope from its airfoil's section slope by Helmbold's equation for an unswept
wing; the downwash gradient from the elliptic estimate
``d(eps)/d(alpha) = 2 a_w / (pi AR_w)``; the stick-fixed neutral point
``h_n = h_ac + eta V_H (a_t / a_w) (1 - d(eps)/d(alpha))``, and the static
margin ``h_n - h_cg``. The tail lift coefficient that trims the cruise
condition, from ``Cm_ac + CL (h_cg - h_ac) - eta V_H CL_t = 0``, against the
tail's usable lift coefficient. Fuselage and nacelle contributions and the
tail's share of total lift are neglected, and said so.

**Electric endurance** (:func:`electric_endurance`). Usable battery energy
over the electrical load (cruise plus avionics), and the range at the cruise
speed; with a required endurance, the capacity that would meet it.

Units: SI throughout (kg, m, s, N, W), energy in Wh, endurance in minutes.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "CruiseRequest",
    "CruiseResult",
    "EnduranceRequest",
    "EnduranceResult",
    "Figure",
    "TrimRequest",
    "TrimResult",
    "WingRequest",
    "WingResult",
    "cruise_performance",
    "electric_endurance",
    "helmbold_lift_slope",
    "trim_stability",
    "wing_sizing",
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


def _v(fig: Figure | None) -> float | None:
    return None if fig is None else fig.value


def _unknowns(request: BaseModel, names: tuple[str, ...]) -> list[str]:
    return [n for n in names if getattr(request, n) is None]


def _require_positive(request: BaseModel, names: tuple[str, ...]) -> None:
    for name in names:
        fig = getattr(request, name)
        if fig is not None and fig.value <= 0:
            raise ValueError(f"{name} must be positive")


def _require_fraction(request: BaseModel, names: tuple[str, ...]) -> None:
    for name in names:
        fig = getattr(request, name)
        if fig is not None and not 0 < fig.value <= 1:
            raise ValueError(f"{name} must be in (0, 1]")


# -- wing sizing ------------------------------------------------------------


class WingRequest(BaseModel):
    mass_kg: Figure | None = Field(default=None, description="Maximum take-off mass, kg")
    max_stall_speed_mps: Figure | None = Field(default=None, description="From the requirements")
    cl_max: Figure | None = Field(default=None, description="Wing maximum lift coefficient")
    air_density_kg_m3: Figure | None = Field(
        default=None, description="At the condition the requirement names (e.g. ISA sea level)"
    )
    aspect_ratio: Figure | None = None
    wing_area_m2: Figure | None = Field(
        default=None, description="A chosen area to check; omit to size one"
    )

    @model_validator(mode="after")
    def _positive(self) -> WingRequest:
        _require_positive(
            self,
            (
                "mass_kg",
                "max_stall_speed_mps",
                "cl_max",
                "air_density_kg_m3",
                "aspect_ratio",
                "wing_area_m2",
            ),
        )
        return self


class WingResult(BaseModel):
    verdict: Verdict
    required_wing_area_m2: float | None = None
    wing_area_m2: float | None = Field(default=None, description="Chosen, else the required")
    stall_speed_mps: float | None = Field(default=None, description="At the given mass")
    wing_span_m: float | None = None
    mean_chord_m: float | None = None
    wing_loading_n_m2: float | None = None
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def wing_sizing(request: WingRequest) -> WingResult:
    """The wing area that holds the stall speed, or the stall speed of a chosen area."""
    result = WingResult(
        verdict="not_established",
        unknowns=_unknowns(
            request, ("mass_kg", "max_stall_speed_mps", "cl_max", "air_density_kg_m3")
        ),
        assumptions=[
            "stall in level flight at the given mass: L = W at CL_max",
            "rectangular-equivalent planform: span = sqrt(AR * S), mean chord = S / span",
        ],
    )
    m, vs = _v(request.mass_kg), _v(request.max_stall_speed_mps)
    cl, rho = _v(request.cl_max), _v(request.air_density_kg_m3)
    weight = None if m is None else m * STANDARD_GRAVITY_M_S2
    if None not in (weight, vs, cl, rho):
        result.required_wing_area_m2 = 2 * weight / (rho * vs**2 * cl)  # type: ignore[operator]
    area = _v(request.wing_area_m2) or result.required_wing_area_m2
    result.wing_area_m2 = area
    if area is not None and None not in (weight, cl, rho):
        result.stall_speed_mps = math.sqrt(2 * weight / (rho * area * cl))  # type: ignore[operator]
        result.wing_loading_n_m2 = weight / area  # type: ignore[operator]
    ar = _v(request.aspect_ratio)
    if area is not None and ar is not None:
        result.wing_span_m = math.sqrt(ar * area)
        result.mean_chord_m = area / result.wing_span_m
    if result.stall_speed_mps is not None and vs is not None and not result.unknowns:
        if result.stall_speed_mps > vs * (1 + 1e-9):
            result.verdict = "fail"
            result.findings.append(
                f"stall speed {result.stall_speed_mps:.2f} m/s exceeds the "
                f"{vs:.2f} m/s limit: the wing needs at least "
                f"{result.required_wing_area_m2:.3f} m^2"
            )
        else:
            result.verdict = "pass"
    return result


# -- cruise performance -----------------------------------------------------


class CruiseRequest(BaseModel):
    mass_kg: Figure | None = Field(default=None, description="All-up mass in cruise, kg")
    wing_area_m2: Figure | None = None
    aspect_ratio: Figure | None = None
    oswald_efficiency: Figure | None = Field(default=None, description="Span efficiency e")
    cd0: Figure | None = Field(default=None, description="Zero-lift drag coefficient (build-up)")
    air_density_kg_m3: Figure | None = None
    cruise_speed_mps: Figure | None = None
    propulsive_efficiency: Figure | None = Field(
        default=None, description="Shaft-to-thrust x motor x controller, electrical to thrust power"
    )
    cl_max: Figure | None = Field(default=None, description="To check cruise is above the stall")

    @model_validator(mode="after")
    def _checks(self) -> CruiseRequest:
        _require_positive(
            self,
            (
                "mass_kg",
                "wing_area_m2",
                "aspect_ratio",
                "cd0",
                "air_density_kg_m3",
                "cruise_speed_mps",
                "cl_max",
            ),
        )
        _require_fraction(self, ("oswald_efficiency", "propulsive_efficiency"))
        return self


class CruiseResult(BaseModel):
    verdict: Verdict
    cruise_lift_coefficient: float | None = None
    drag_coefficient: float | None = None
    lift_to_drag: float | None = None
    drag_n: float | None = None
    shaft_power_w: float | None = Field(default=None, description="Drag x speed")
    electrical_power_w: float | None = Field(
        default=None, description="Shaft power / propulsive efficiency"
    )
    best_lift_to_drag: float | None = None
    best_lift_to_drag_speed_mps: float | None = None
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def cruise_performance(request: CruiseRequest) -> CruiseResult:
    """Parabolic drag polar at the cruise condition, and the power it takes."""
    result = CruiseResult(
        verdict="not_established",
        unknowns=_unknowns(
            request,
            (
                "mass_kg",
                "wing_area_m2",
                "aspect_ratio",
                "oswald_efficiency",
                "cd0",
                "air_density_kg_m3",
                "cruise_speed_mps",
                "propulsive_efficiency",
            ),
        ),
        assumptions=[
            "steady level cruise: L = W, T = D",
            "parabolic drag polar CD = CD0 + CL^2 / (pi e AR); no compressibility",
        ],
    )
    m, s, ar = _v(request.mass_kg), _v(request.wing_area_m2), _v(request.aspect_ratio)
    e, cd0 = _v(request.oswald_efficiency), _v(request.cd0)
    rho, v = _v(request.air_density_kg_m3), _v(request.cruise_speed_mps)
    eta = _v(request.propulsive_efficiency)
    k = None if e is None or ar is None else 1 / (math.pi * e * ar)
    if k is not None and cd0 is not None:
        result.best_lift_to_drag = 1 / (2 * math.sqrt(k * cd0))
        if None not in (m, s, rho):
            cl_star = math.sqrt(cd0 / k)
            result.best_lift_to_drag_speed_mps = math.sqrt(
                2 * m * STANDARD_GRAVITY_M_S2 / (rho * s * cl_star)  # type: ignore[operator]
            )
    if None in (m, s, rho, v):
        return result
    q = 0.5 * rho * v**2  # type: ignore[operator]
    cl = m * STANDARD_GRAVITY_M_S2 / (q * s)  # type: ignore[operator]
    result.cruise_lift_coefficient = cl
    if k is not None and cd0 is not None:
        cd = cd0 + k * cl**2
        result.drag_coefficient = cd
        result.lift_to_drag = cl / cd
        result.drag_n = q * s * cd  # type: ignore[operator]
        result.shaft_power_w = result.drag_n * v  # type: ignore[operator]
        if eta is not None:
            result.electrical_power_w = result.shaft_power_w / eta
    cl_max = _v(request.cl_max)
    if cl_max is not None and cl >= cl_max:
        result.verdict = "fail"
        result.findings.append(
            f"cruise needs CL {cl:.2f}, at or above CL_max {cl_max:.2f}: "
            "the cruise speed is below the stall"
        )
    elif not result.unknowns:
        result.verdict = "pass"
    return result


# -- trim and static stability ----------------------------------------------


def helmbold_lift_slope(airfoil_slope_per_rad: float, aspect_ratio: float) -> float:
    """Finite unswept wing lift-curve slope (per rad), Helmbold's equation."""
    x = airfoil_slope_per_rad / (math.pi * aspect_ratio)
    return airfoil_slope_per_rad / (math.sqrt(1 + x**2) + x)


class TrimRequest(BaseModel):
    wing_aspect_ratio: Figure | None = None
    wing_airfoil_lift_slope_per_rad: Figure | None = Field(
        default=None, description="Section lift slope (airfoil data or thin-airfoil 2 pi, cited)"
    )
    tail_aspect_ratio: Figure | None = None
    tail_airfoil_lift_slope_per_rad: Figure | None = None
    tail_volume_coefficient: Figure | None = Field(
        default=None, description="V_H = S_t l_t / (S c), from the layout"
    )
    tail_efficiency: Figure | None = Field(
        default=None, description="Tail dynamic-pressure ratio eta_t"
    )
    wing_ac_position_mac: Figure | None = Field(
        default=None, description="Wing aerodynamic centre, fraction of the MAC"
    )
    cg_position_mac: Figure | None = Field(default=None, description="Fraction of the MAC")
    wing_cm_ac: Figure | None = Field(
        default=None, description="Wing pitching moment about its a.c. (airfoil data)"
    )
    cruise_lift_coefficient: Figure | None = None
    tail_cl_max: Figure | None = Field(
        default=None, description="Usable tail lift coefficient magnitude"
    )
    required_static_margin: Figure | None = Field(
        default=None, description="Fraction of the MAC, from the requirements"
    )

    @model_validator(mode="after")
    def _checks(self) -> TrimRequest:
        _require_positive(
            self,
            (
                "wing_aspect_ratio",
                "wing_airfoil_lift_slope_per_rad",
                "tail_aspect_ratio",
                "tail_airfoil_lift_slope_per_rad",
                "tail_volume_coefficient",
                "tail_cl_max",
            ),
        )
        _require_fraction(self, ("tail_efficiency",))
        return self


class TrimResult(BaseModel):
    verdict: Verdict
    wing_lift_slope_per_rad: float | None = None
    tail_lift_slope_per_rad: float | None = None
    downwash_gradient: float | None = None
    neutral_point_mac: float | None = None
    static_margin: float | None = Field(default=None, description="h_n - h_cg, fraction of MAC")
    static_margin_excess: float | None = Field(
        default=None, description="Static margin - required (>= 0 passes)"
    )
    trim_tail_cl: float | None = None
    trim_tail_cl_margin: float | None = Field(
        default=None, description="Usable tail CL - |trim tail CL| (>= 0 passes)"
    )
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def trim_stability(request: TrimRequest) -> TrimResult:
    """Stick-fixed neutral point, static margin and the tail CL that trims cruise."""
    result = TrimResult(
        verdict="not_established",
        unknowns=_unknowns(
            request,
            (
                "wing_aspect_ratio",
                "wing_airfoil_lift_slope_per_rad",
                "tail_aspect_ratio",
                "tail_airfoil_lift_slope_per_rad",
                "tail_volume_coefficient",
                "tail_efficiency",
                "wing_ac_position_mac",
                "cg_position_mac",
                "wing_cm_ac",
                "cruise_lift_coefficient",
                "tail_cl_max",
                "required_static_margin",
            ),
        ),
        assumptions=[
            "unswept surfaces: lift slopes by Helmbold's equation",
            "downwash gradient by the elliptic estimate 2 a_w / (pi AR_w)",
            "stick-fixed; fuselage, nacelles and the tail's share of total lift neglected",
        ],
    )
    a0w, arw = _v(request.wing_airfoil_lift_slope_per_rad), _v(request.wing_aspect_ratio)
    a0t, art = _v(request.tail_airfoil_lift_slope_per_rad), _v(request.tail_aspect_ratio)
    vh, eta = _v(request.tail_volume_coefficient), _v(request.tail_efficiency)
    h_ac, h = _v(request.wing_ac_position_mac), _v(request.cg_position_mac)
    if a0w is not None and arw is not None:
        result.wing_lift_slope_per_rad = helmbold_lift_slope(a0w, arw)
        result.downwash_gradient = 2 * result.wing_lift_slope_per_rad / (math.pi * arw)
    if a0t is not None and art is not None:
        result.tail_lift_slope_per_rad = helmbold_lift_slope(a0t, art)
    a_w, a_t, de = (
        result.wing_lift_slope_per_rad,
        result.tail_lift_slope_per_rad,
        result.downwash_gradient,
    )
    if None not in (a_w, a_t, de, vh, eta, h_ac):
        result.neutral_point_mac = h_ac + eta * vh * (a_t / a_w) * (1 - de)  # type: ignore[operator]
        if h is not None:
            result.static_margin = result.neutral_point_mac - h
    required = _v(request.required_static_margin)
    if result.static_margin is not None and required is not None:
        result.static_margin_excess = result.static_margin - required
    cm_ac, cl = _v(request.wing_cm_ac), _v(request.cruise_lift_coefficient)
    if None not in (cm_ac, cl, h, h_ac, eta, vh):
        result.trim_tail_cl = (cm_ac + cl * (h - h_ac)) / (eta * vh)  # type: ignore[operator]
        tail_max = _v(request.tail_cl_max)
        if tail_max is not None:
            result.trim_tail_cl_margin = tail_max - abs(result.trim_tail_cl)

    f = result.findings
    if result.static_margin is not None and result.static_margin <= 0:
        f.append(
            f"statically unstable: the CG is {-result.static_margin:.3f} MAC aft of the "
            "neutral point"
        )
    if result.static_margin_excess is not None and result.static_margin_excess < 0:
        f.append(
            f"static margin {result.static_margin:.3f} MAC is below the required "
            f"{required:.3f}: move the CG forward or enlarge the tail"
        )
    if result.trim_tail_cl_margin is not None and result.trim_tail_cl_margin < 0:
        f.append(
            f"trimming cruise needs tail CL {result.trim_tail_cl:.2f}, beyond its usable "
            f"{_v(request.tail_cl_max):.2f}"
        )
    if f:
        result.verdict = "fail"
    elif not result.unknowns:
        result.verdict = "pass"
    return result


# -- electric endurance -----------------------------------------------------


class EnduranceRequest(BaseModel):
    battery_capacity_wh: Figure | None = Field(
        default=None, description="Rated capacity; omit to size one for the required endurance"
    )
    usable_fraction: Figure | None = Field(
        default=None, description="Usable depth of discharge and reserve, 0..1 (cell datasheet)"
    )
    propulsion_power_w: Figure | None = Field(
        default=None, description="Electrical power to the propulsion in cruise"
    )
    avionics_power_w: Figure | None = Field(
        default=None, description="Avionics and payload, from the electronics or the datasheets"
    )
    cruise_speed_mps: Figure | None = Field(default=None, description="For the range")
    required_endurance_min: Figure | None = None

    @model_validator(mode="after")
    def _checks(self) -> EnduranceRequest:
        _require_positive(
            self,
            (
                "battery_capacity_wh",
                "propulsion_power_w",
                "cruise_speed_mps",
                "required_endurance_min",
            ),
        )
        _require_fraction(self, ("usable_fraction",))
        if self.avionics_power_w is not None and self.avionics_power_w.value < 0:
            raise ValueError("avionics_power_w must not be negative")
        return self


class EnduranceResult(BaseModel):
    verdict: Verdict
    total_power_w: float | None = None
    endurance_min: float | None = None
    range_km: float | None = None
    required_capacity_wh: float | None = Field(
        default=None, description="Capacity that meets the required endurance"
    )
    endurance_margin: float | None = Field(
        default=None, description="Endurance / required - 1 (>= 0 passes)"
    )
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def electric_endurance(request: EnduranceRequest) -> EnduranceResult:
    """Cruise endurance and range on a battery, or the battery a required endurance needs."""
    needed = ("usable_fraction", "propulsion_power_w", "avionics_power_w")
    result = EnduranceResult(
        verdict="not_established",
        unknowns=_unknowns(request, needed),
        assumptions=[
            "the whole usable energy is spent in steady cruise at the given power; climb, "
            "loiter and descent are not modelled (a mission simulation outranks this)",
            "battery voltage sag and capacity loss with temperature are inside the usable fraction",
        ],
    )
    usable, p_prop = _v(request.usable_fraction), _v(request.propulsion_power_w)
    p_av = _v(request.avionics_power_w)
    if p_prop is not None and p_av is not None:
        result.total_power_w = p_prop + p_av
    total, required = result.total_power_w, _v(request.required_endurance_min)
    if total is not None and usable is not None and required is not None:
        result.required_capacity_wh = total * (required / 60) / usable
    capacity = _v(request.battery_capacity_wh)
    if capacity is None:
        result.unknowns.append("battery_capacity_wh")
        return result
    if total is not None and usable is not None:
        result.endurance_min = capacity * usable / total * 60
        v = _v(request.cruise_speed_mps)
        if v is not None:
            result.range_km = v * result.endurance_min * 60 / 1000
    if result.endurance_min is not None and required is not None:
        result.endurance_margin = result.endurance_min / required - 1
        if result.endurance_margin < 0:
            result.verdict = "fail"
            result.findings.append(
                f"endurance {result.endurance_min:.1f} min is short of the required "
                f"{required:.1f} min: {result.required_capacity_wh:.0f} Wh would meet it"
            )
            return result
    if result.endurance_min is not None and not result.unknowns:
        result.verdict = "pass"
    return result
