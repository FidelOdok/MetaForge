"""In-skill tests for rank_manufacturing_processes (run via pytest discovery)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext

from .handler import RankManufacturingProcessesHandler
from .schema import CandidateProcess, RankManufacturingProcessesInput


@pytest.fixture()
def ctx() -> SkillContext:
    context = MagicMock(spec=SkillContext)
    context.twin = AsyncMock()
    context.logger = MagicMock()
    context.logger.bind = MagicMock(return_value=context.logger)
    context.session_id = uuid4()
    context.branch = "main"
    return context


def _laser_weld(**over: object) -> CandidateProcess:
    base: dict[str, object] = {
        "process": "laser cut + MIG weld",
        "route": "vendor",
        "available": True,
        "max_envelope_mm": [3000, 1500, 1500],
        "min_feature_mm": 1.0,
        "achievable_tolerance_mm": 0.5,
        "materials": ["steel"],
        "setup_cost": 120.0,
        "unit_cost": 85.0,
        "source_refs": ["VAL-1"],
    }
    base.update(over)
    return CandidateProcess(**base)  # type: ignore[arg-type]


def _need(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "quantity": 4,
        "material_class": "steel",
        "part_envelope_mm": [1200, 800, 740],
        "min_feature_mm": 2.0,
        "tightest_tolerance_mm": 1.0,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_a_fitting_process_is_ranked_with_cost_at_quantity(ctx: SkillContext) -> None:
    out = await RankManufacturingProcessesHandler(ctx).execute(
        RankManufacturingProcessesInput(candidates=[_laser_weld()], **_need())  # type: ignore[arg-type]
    )
    top = out.ranked[0]
    assert top.verdict == "fits"
    assert top.cost_at_quantity == pytest.approx(120 + 85 * 4)
    assert out.recommended == "laser cut + MIG weld"


@pytest.mark.asyncio
async def test_a_failing_criterion_ranks_last_and_is_never_recommended(ctx: SkillContext) -> None:
    small = _laser_weld(process="desktop CNC", max_envelope_mm=[400, 300, 100])
    out = await RankManufacturingProcessesHandler(ctx).execute(
        RankManufacturingProcessesInput(candidates=[small, _laser_weld()], **_need())  # type: ignore[arg-type]
    )
    assert [r.process for r in out.ranked] == ["laser cut + MIG weld", "desktop CNC"]
    assert out.ranked[1].verdict == "does_not_fit"
    assert any("envelope" in r for r in out.ranked[1].reasons)


@pytest.mark.asyncio
async def test_a_missing_capability_is_unknown_never_assumed(ctx: SkillContext) -> None:
    vague = _laser_weld(process="vendor X", achievable_tolerance_mm=None, unit_cost=None)
    out = await RankManufacturingProcessesHandler(ctx).execute(
        RankManufacturingProcessesInput(candidates=[vague], **_need())  # type: ignore[arg-type]
    )
    only = out.ranked[0]
    assert only.verdict == "incomplete"
    assert only.cost_at_quantity is None
    assert {c.name: c.status for c in only.criteria}["tolerance"] == "unknown"
    assert out.recommended == "vendor X"  # best available, flagged incomplete


@pytest.mark.asyncio
async def test_unknown_part_needs_are_listed(ctx: SkillContext) -> None:
    out = await RankManufacturingProcessesHandler(ctx).execute(
        RankManufacturingProcessesInput(candidates=[_laser_weld()], quantity=None)
    )
    assert "quantity" in out.unknown_inputs
    assert out.ranked[0].verdict == "incomplete"


@pytest.mark.asyncio
async def test_nothing_fits_means_no_recommendation(ctx: SkillContext) -> None:
    wrong = _laser_weld(materials=["aluminium"])
    out = await RankManufacturingProcessesHandler(ctx).execute(
        RankManufacturingProcessesInput(candidates=[wrong], **_need())  # type: ignore[arg-type]
    )
    assert out.recommended is None
