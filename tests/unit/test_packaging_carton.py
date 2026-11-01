"""Carton compression and RSC dieline arithmetic (FORGE-672).

Fixture numbers with explicit made-up sources: the point is the arithmetic
and the refusal to assume, not a real carton.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from tool_registry.tools.packaging.adapter import PackagingServer
from tool_registry.tools.packaging.carton import (
    MCKEE_K,
    STANDARD_GRAVITY_M_S2,
    CompressionRequest,
    DielineRequest,
    box_compression,
    rsc_dieline,
)


def f(value: float, source: str = "fixture") -> dict:
    return {"value": value, "source": source}


def _compression(**over: object) -> CompressionRequest:
    base: dict = {
        "ect_n_per_m": f(7000.0, "mill datasheet, 32 ECT C-flute"),
        "caliper_m": f(0.004, "mill datasheet"),
        "inner_length_m": f(0.4, "product envelope"),
        "inner_width_m": f(0.3, "product envelope"),
        "gross_mass_kg": f(8.0, "packed sample, weighed"),
        "stack_count": 6,
        "required_safety_factor": f(3.0, "REQ-STACK"),
        "deratings": [
            {"name": "humidity 85% RH", "factor": f(0.6, "industry derating table")},
            {"name": "storage 90 days", "factor": f(0.7, "industry derating table")},
        ],
    }
    base.update(over)
    return CompressionRequest.model_validate(base)


def test_mckee_estimate_derated_against_the_stack_load() -> None:
    result = box_compression(_compression())
    bct = MCKEE_K * 7000.0 * math.sqrt(0.004 * 1.4)
    load = 5 * 8.0 * STANDARD_GRAVITY_M_S2
    assert result.perimeter_m == pytest.approx(1.4)
    assert result.bct_estimate_n == pytest.approx(bct)
    assert result.derating_factor == pytest.approx(0.42)
    assert result.bct_derated_n == pytest.approx(bct * 0.42)
    assert result.stack_load_n == pytest.approx(load)
    assert result.required_strength_n == pytest.approx(load * 3.0)
    assert result.compression_margin == pytest.approx(bct * 0.42 / (load * 3.0) - 1)
    assert result.verdict == "pass"


def test_a_taller_stack_fails_with_the_numbers() -> None:
    result = box_compression(_compression(stack_count=10))
    assert result.verdict == "fail"
    assert "below the required" in result.findings[0]


def test_no_deratings_is_said_not_assumed() -> None:
    result = box_compression(_compression(deratings=[]))
    assert result.derating_factor == 1.0
    assert any("standard conditions only" in finding for finding in result.findings)


def test_a_missing_figure_is_not_established() -> None:
    result = box_compression(_compression(ect_n_per_m=None))
    assert "ect_n_per_m" in result.unknowns
    assert result.bct_estimate_n is None
    assert result.verdict == "not_established"


def test_a_single_box_carries_no_stack() -> None:
    result = box_compression(_compression(stack_count=1))
    assert result.stack_load_n == 0
    assert result.verdict == "pass"


@pytest.mark.parametrize(
    "over",
    [
        {"required_safety_factor": f(0.5)},
        {"caliper_m": f(-0.001)},
        {"deratings": [{"name": "x", "factor": f(1.5)}]},
        {"ect_n_per_m": {"value": 7000.0, "source": ""}},
    ],
)
def test_bad_figures_are_refused(over: dict) -> None:
    with pytest.raises(ValidationError):
        _compression(**over)


def _dieline(**over: object) -> DielineRequest:
    base: dict = {
        "inner_length_m": f(0.4),
        "inner_width_m": f(0.3),
        "inner_depth_m": f(0.25),
        "caliper_m": f(0.004),
        "score_allowance_m": f(0.004, "converter's rules"),
        "glue_flap_m": f(0.035, "converter's rules"),
        "min_glue_flap_m": f(0.03, "converter's rules"),
        "min_panel_m": f(0.05, "converter's rules"),
        "max_sheet_length_m": f(2.0, "converter's corrugator"),
        "max_sheet_width_m": f(1.2, "converter's corrugator"),
    }
    base.update(over)
    return DielineRequest.model_validate(base)


def test_rsc_blank_size() -> None:
    result = rsc_dieline(_dieline())
    assert result.blank_length_m == pytest.approx(2 * 0.7 + 4 * 0.004 + 0.035)
    assert result.flap_depth_m == pytest.approx(0.15 + 0.004)
    assert result.blank_width_m == pytest.approx(0.25 + 2 * 0.004 + 2 * 0.154)
    assert result.violations == []
    assert result.verdict == "pass"


def test_converter_rules_are_enforced() -> None:
    result = rsc_dieline(
        _dieline(glue_flap_m=f(0.02), max_sheet_length_m=f(1.0), inner_depth_m=f(0.03))
    )
    assert result.verdict == "fail"
    assert any("glue flap 20 mm" in v for v in result.violations)
    assert any("exceeds the 1000 mm sheet" in v for v in result.violations)
    assert any("panel depth 30 mm" in v for v in result.violations)


def test_missing_converter_rules_are_not_established() -> None:
    result = rsc_dieline(_dieline(min_panel_m=None))
    assert result.verdict == "not_established"
    assert "min_panel_m" in result.unknowns


@pytest.mark.asyncio
async def test_the_adapter_serves_both_tools() -> None:
    server = PackagingServer()
    assert {"packaging.box_compression", "packaging.rsc_dieline"} <= set(server.tool_ids)
    assert (await server.box_compression(_compression().model_dump()))["verdict"] == "pass"
    assert (await server.rsc_dieline(_dieline().model_dump()))["verdict"] == "pass"
