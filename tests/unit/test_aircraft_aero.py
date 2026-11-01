"""Fixed-wing sizing, cruise, trim and endurance arithmetic (FORGE-670).

Fixture numbers with explicit made-up sources: the point is the arithmetic
and the refusal to assume, not a real aircraft.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from tool_registry.tools.aircraft.adapter import AircraftServer
from tool_registry.tools.aircraft.aero import (
    STANDARD_GRAVITY_M_S2,
    CruiseRequest,
    EnduranceRequest,
    TrimRequest,
    WingRequest,
    cruise_performance,
    electric_endurance,
    helmbold_lift_slope,
    trim_stability,
    wing_sizing,
)

G = STANDARD_GRAVITY_M_S2


def f(value: float, source: str = "fixture") -> dict:
    return {"value": value, "source": source}


# -- wing sizing ------------------------------------------------------------


def _wing(**over: object) -> WingRequest:
    base: dict = {
        "mass_kg": f(6.0, "REQ-MTOW"),
        "max_stall_speed_mps": f(12.0, "REQ-STALL"),
        "cl_max": f(1.3, "airfoil polar"),
        "air_density_kg_m3": f(1.225, "ISA sea level"),
        "aspect_ratio": f(8.0, "configuration"),
    }
    base.update(over)
    return WingRequest.model_validate(base)


def test_the_wing_is_sized_for_the_stall_speed() -> None:
    result = wing_sizing(_wing())
    area = 2 * 6.0 * G / (1.225 * 12.0**2 * 1.3)
    assert result.required_wing_area_m2 == pytest.approx(area)
    assert result.wing_area_m2 == pytest.approx(area)
    assert result.stall_speed_mps == pytest.approx(12.0)
    assert result.wing_span_m == pytest.approx(math.sqrt(8.0 * area))
    assert result.verdict == "pass"


def test_a_chosen_area_too_small_fails_with_the_area_it_needs() -> None:
    result = wing_sizing(_wing(wing_area_m2=f(0.4)))
    assert result.stall_speed_mps == pytest.approx(math.sqrt(2 * 6.0 * G / (1.225 * 0.4 * 1.3)))
    assert result.verdict == "fail"
    assert "needs at least" in result.findings[0]


def test_a_wing_without_cl_max_is_not_established() -> None:
    result = wing_sizing(_wing(cl_max=None))
    assert "cl_max" in result.unknowns
    assert result.required_wing_area_m2 is None
    assert result.verdict == "not_established"


# -- cruise -----------------------------------------------------------------


def _cruise(**over: object) -> CruiseRequest:
    base: dict = {
        "mass_kg": f(5.0, "mass roll-up"),
        "wing_area_m2": f(0.55, "wing sizing"),
        "aspect_ratio": f(8.0),
        "oswald_efficiency": f(0.8, "planform estimate"),
        "cd0": f(0.03, "drag build-up"),
        "air_density_kg_m3": f(1.225, "ISA"),
        "cruise_speed_mps": f(18.0, "REQ-CRUISE"),
        "propulsive_efficiency": f(0.55, "prop x motor x ESC datasheets"),
        "cl_max": f(1.3, "airfoil polar"),
    }
    base.update(over)
    return CruiseRequest.model_validate(base)


def test_the_drag_polar_gives_cruise_power() -> None:
    result = cruise_performance(_cruise())
    q = 0.5 * 1.225 * 18.0**2
    cl = 5.0 * G / (q * 0.55)
    k = 1 / (math.pi * 0.8 * 8.0)
    cd = 0.03 + k * cl**2
    assert result.cruise_lift_coefficient == pytest.approx(cl)
    assert result.drag_coefficient == pytest.approx(cd)
    assert result.lift_to_drag == pytest.approx(cl / cd)
    assert result.shaft_power_w == pytest.approx(q * 0.55 * cd * 18.0)
    assert result.electrical_power_w == pytest.approx(q * 0.55 * cd * 18.0 / 0.55)
    assert result.best_lift_to_drag == pytest.approx(1 / (2 * math.sqrt(k * 0.03)))
    assert result.verdict == "pass"


def test_cruising_below_the_stall_fails() -> None:
    result = cruise_performance(_cruise(cruise_speed_mps=f(8.0)))
    assert result.verdict == "fail"
    assert "below the stall" in result.findings[0]


def test_cruise_without_cd0_has_no_power() -> None:
    result = cruise_performance(_cruise(cd0=None))
    assert result.cruise_lift_coefficient is not None
    assert result.electrical_power_w is None
    assert result.verdict == "not_established"


# -- trim and stability -----------------------------------------------------


def _trim(**over: object) -> TrimRequest:
    base: dict = {
        "wing_aspect_ratio": f(8.0),
        "wing_airfoil_lift_slope_per_rad": f(2 * math.pi, "thin-airfoil theory"),
        "tail_aspect_ratio": f(4.0),
        "tail_airfoil_lift_slope_per_rad": f(2 * math.pi, "thin-airfoil theory"),
        "tail_volume_coefficient": f(0.5, "layout"),
        "tail_efficiency": f(0.9, "textbook estimate"),
        "wing_ac_position_mac": f(0.25, "quarter chord, subsonic"),
        "cg_position_mac": f(0.30, "layout mass breakdown"),
        "wing_cm_ac": f(-0.05, "airfoil polar"),
        "cruise_lift_coefficient": f(0.48, "cruise run"),
        "tail_cl_max": f(0.5, "tail airfoil polar"),
        "required_static_margin": f(0.10, "REQ-STAB"),
    }
    base.update(over)
    return TrimRequest.model_validate(base)


def test_helmbold_reduces_to_the_textbook_form_for_a_thin_airfoil() -> None:
    for ar in (4.0, 8.0, 20.0):
        textbook = 2 * math.pi * ar / (2 + math.sqrt(ar**2 + 4))
        assert helmbold_lift_slope(2 * math.pi, ar) == pytest.approx(textbook)


def test_neutral_point_static_margin_and_trim() -> None:
    result = trim_stability(_trim())
    a_w = helmbold_lift_slope(2 * math.pi, 8.0)
    a_t = helmbold_lift_slope(2 * math.pi, 4.0)
    de = 2 * a_w / (math.pi * 8.0)
    h_n = 0.25 + 0.9 * 0.5 * (a_t / a_w) * (1 - de)
    assert result.downwash_gradient == pytest.approx(de)
    assert result.neutral_point_mac == pytest.approx(h_n)
    assert result.static_margin == pytest.approx(h_n - 0.30)
    cl_t = (-0.05 + 0.48 * (0.30 - 0.25)) / (0.9 * 0.5)
    assert result.trim_tail_cl == pytest.approx(cl_t)
    assert result.trim_tail_cl_margin == pytest.approx(0.5 - abs(cl_t))
    assert result.verdict == "pass"


def test_an_aft_cg_fails_the_required_margin() -> None:
    result = trim_stability(_trim(cg_position_mac=f(0.40)))
    assert result.static_margin is not None and 0 < result.static_margin < 0.10
    assert result.verdict == "fail"
    assert any("below the required" in finding for finding in result.findings)


def test_a_cg_behind_the_neutral_point_is_unstable() -> None:
    result = trim_stability(_trim(cg_position_mac=f(0.60)))
    assert any("statically unstable" in finding for finding in result.findings)


def test_a_tail_too_small_to_trim_fails() -> None:
    result = trim_stability(_trim(tail_cl_max=f(0.02)))
    assert result.trim_tail_cl_margin is not None and result.trim_tail_cl_margin < 0
    assert result.verdict == "fail"


def test_trim_without_airfoil_data_is_not_established() -> None:
    result = trim_stability(_trim(wing_cm_ac=None))
    assert "wing_cm_ac" in result.unknowns
    assert result.trim_tail_cl is None
    assert result.static_margin is not None  # the stability half does not need it
    assert result.verdict == "not_established"


# -- endurance --------------------------------------------------------------


def _endurance(**over: object) -> EnduranceRequest:
    base: dict = {
        "battery_capacity_wh": f(200.0, "pack datasheet"),
        "usable_fraction": f(0.8, "cell datasheet and reserve"),
        "propulsion_power_w": f(140.0, "cruise run"),
        "avionics_power_w": f(10.0, "electronics power budget"),
        "cruise_speed_mps": f(18.0, "REQ-CRUISE"),
        "required_endurance_min": f(60.0, "REQ-ENDURANCE"),
    }
    base.update(over)
    return EnduranceRequest.model_validate(base)


def test_endurance_range_and_the_capacity_that_meets_it() -> None:
    result = electric_endurance(_endurance())
    endurance = 200.0 * 0.8 / 150.0 * 60
    assert result.endurance_min == pytest.approx(endurance)
    assert result.range_km == pytest.approx(18.0 * endurance * 60 / 1000)
    assert result.required_capacity_wh == pytest.approx(150.0 * 1.0 / 0.8)
    assert result.endurance_margin == pytest.approx(endurance / 60 - 1)
    assert result.verdict == "pass"


def test_a_short_endurance_fails_with_the_capacity_needed() -> None:
    result = electric_endurance(_endurance(battery_capacity_wh=f(150.0)))
    assert result.verdict == "fail"
    assert "188 Wh would meet it" in result.findings[0]


def test_sizing_mode_needs_no_capacity() -> None:
    result = electric_endurance(_endurance(battery_capacity_wh=None))
    assert result.required_capacity_wh == pytest.approx(187.5)
    assert result.verdict == "not_established"
    assert "battery_capacity_wh" in result.unknowns


@pytest.mark.parametrize(
    ("build", "over"),
    [
        (_wing, {"mass_kg": f(-1.0)}),
        (_cruise, {"oswald_efficiency": f(1.2)}),
        (_trim, {"tail_efficiency": f(0.0)}),
        (_endurance, {"avionics_power_w": f(-5.0)}),
        (_wing, {"cl_max": {"value": 1.3, "source": ""}}),
    ],
)
def test_bad_figures_are_refused(build, over: dict) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        build(**over)


@pytest.mark.asyncio
async def test_the_adapter_serves_all_four_tools() -> None:
    server = AircraftServer()
    assert {
        "aircraft.wing_sizing",
        "aircraft.cruise_performance",
        "aircraft.trim_stability",
        "aircraft.electric_endurance",
    } <= set(server.tool_ids)
    assert (await server.wing_sizing(_wing().model_dump()))["verdict"] == "pass"
    assert (await server.cruise_performance(_cruise().model_dump()))["verdict"] == "pass"
    assert (await server.trim_stability(_trim().model_dump()))["verdict"] == "pass"
    assert (await server.electric_endurance(_endurance().model_dump()))["verdict"] == "pass"
