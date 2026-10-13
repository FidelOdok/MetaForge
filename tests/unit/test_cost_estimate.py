"""cost.estimate_manufacturing: a rough estimate, never a price (FORGE-615)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from mcp_core.annotations import OPEN_WORLD, READ_ONLY
from mcp_core.profiles import PROFILES
from tool_registry.tools.cost.adapter import CostServer
from tool_registry.tools.cost.estimate import EstimateRequest, estimate


def fig(value: float, source: str = "test figure") -> dict[str, Any]:
    return {"value": value, "source": source}


CNC: dict[str, Any] = {
    "process": "cnc_milling",
    "quantity": 10,
    "currency": "GBP",
    "density_kg_per_m3": fig(2700, "6061 datasheet"),
    "material_price_per_kg": fig(10, "supplier price list"),
    "shop_rate_per_hour": fig(60, "shop's published rate"),
    "setup_hours": fig(2, "shop said"),
    "stock_dims_mm": {"value": [100, 100, 10], "source": "stock size"},
    "part_volume_mm3": fig(40_000, "freecad.measure"),
    "removal_rate_mm3_per_min": fig(1_000, "shop said"),
}

SHEET: dict[str, Any] = {
    "process": "sheet_metal",
    "quantity": 50,
    "currency": "GBP",
    "density_kg_per_m3": fig(7850),
    "material_price_per_kg": fig(2),
    "shop_rate_per_hour": fig(90),
    "setup_hours": fig(1),
    "thickness_mm": fig(2),
    "blank_area_mm2": fig(200 * 300),
    "cut_length_mm": fig(1_200),
    "cut_speed_mm_per_min": fig(6_000),
    "bend_count": 4,
    "seconds_per_bend": fig(15),
}


def run(data: dict[str, Any]) -> dict[str, Any]:
    return estimate(EstimateRequest.model_validate(data))


def test_cnc_arithmetic() -> None:
    r = run(CNC)
    # stock 100,000 mm3 = 1e-4 m3 -> 0.27 kg -> 2.70
    assert r["per_unit"]["material"] == 2.70
    # removed 60,000 mm3 / 1,000 per min = 60 min -> 60.00
    assert r["per_unit"]["process_minutes"] == 60.0
    assert r["per_unit"]["process"] == 60.0
    # setup 2 h x 60 = 120 per batch -> 12 per unit at 10
    assert r["setup_per_batch"] == 120.0
    assert r["per_unit"]["setup_share"] == 12.0
    assert r["per_unit"]["total"] == 74.70
    assert r["batch_total"] == 747.0
    assert r["for_ranking"] == {
        "setup_cost": 120.0,
        "unit_cost": 62.70,
        "source_refs": r["for_ranking"]["source_refs"],
    }
    assert "not a quote" in r["for_ranking"]["source_refs"][0]


def test_sheet_metal_arithmetic() -> None:
    r = run(SHEET)
    # 2 x 60,000 mm3 = 1.2e-4 m3 x 7850 = 0.942 kg x 2 = 1.884
    assert r["per_unit"]["material"] == 1.88
    # 1200/6000 = 0.2 min + 4 x 15 s = 1 min -> 1.2 min
    assert r["per_unit"]["process_minutes"] == 1.2
    assert r["per_unit"]["process"] == 1.8
    assert r["per_unit"]["setup_share"] == 1.8


def test_every_result_says_it_is_an_estimate_and_what_it_leaves_out() -> None:
    r = run(CNC)
    assert r["kind"] == "estimate"
    assert "not a price" in r["not_a_quote"]
    assert any("tolerance" in e for e in r["excludes"])
    assert r["sources"]["shop_rate_per_hour"] == "shop's published rate"


def test_a_missing_figure_leaves_what_needs_it_unknown() -> None:
    data = {k: v for k, v in CNC.items() if k != "removal_rate_mm3_per_min"}
    r = run(data)
    assert r["unknown_inputs"] == ["removal_rate_mm3_per_min"]
    assert r["per_unit"]["material"] == 2.70
    assert r["per_unit"]["process"] is None
    assert r["per_unit"]["total"] is None
    assert r["batch_total"] is None
    assert "for_ranking" not in r


def test_a_figure_without_a_source_is_refused() -> None:
    with pytest.raises(ValidationError):
        run({**CNC, "shop_rate_per_hour": {"value": 60}})
    with pytest.raises(ValidationError):
        run({**CNC, "shop_rate_per_hour": {"value": 60, "source": ""}})


def test_inputs_for_the_other_process_are_refused() -> None:
    with pytest.raises(ValidationError, match="do not apply to cnc_milling"):
        run({**CNC, "bend_count": 2})


def test_a_part_bigger_than_its_stock_is_refused() -> None:
    with pytest.raises(ValueError, match="larger than the stock"):
        run({**CNC, "part_volume_mm3": fig(200_000)})


def test_no_bends_need_no_bend_time() -> None:
    data = {k: v for k, v in SHEET.items() if k != "seconds_per_bend"}
    r = run({**data, "bend_count": 0})
    assert r["per_unit"]["process_minutes"] == 0.2
    assert "seconds_per_bend" not in r["unknown_inputs"]


@pytest.mark.asyncio
async def test_the_tool_reads_only_and_is_in_the_manufacturing_profile() -> None:
    assert "cost.estimate_manufacturing" in READ_ONLY
    assert "cost.estimate_manufacturing" not in OPEN_WORLD
    assert "cost.estimate_manufacturing" in PROFILES["manufacturing"]
    r = await CostServer().estimate_manufacturing(CNC)
    assert r["per_unit"]["total"] == 74.70


@pytest.mark.asyncio
async def test_bootstrap_registers_it_by_default() -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    registry = await bootstrap_tool_registry(adapter_ids=["cost"])
    assert "cost.estimate_manufacturing" in {t.tool_id for t in registry.list_tools()}
