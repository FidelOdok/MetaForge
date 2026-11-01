"""In-skill tests for check_manufacturability (run via pytest discovery)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext

from .handler import CheckManufacturabilityHandler
from .schema import CheckManufacturabilityInput, DesignFeature, ProcessLimit


@pytest.fixture()
def ctx() -> SkillContext:
    context = MagicMock(spec=SkillContext)
    context.twin = AsyncMock()
    context.logger = MagicMock()
    context.logger.bind = MagicMock(return_value=context.logger)
    context.session_id = uuid4()
    context.branch = "main"
    return context


def _run(ctx: SkillContext, features: list[DesignFeature], limits: list[ProcessLimit]) -> object:
    import asyncio

    return asyncio.run(
        CheckManufacturabilityHandler(ctx).execute(
            CheckManufacturabilityInput(
                process="laser cut + weld", features=features, limits=limits
            )
        )
    )


def test_all_within_limits_is_manufacturable(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [
            DesignFeature(name="leg wall", kind="wall_thickness", value=2.0),
            DesignFeature(name="frame", kind="envelope", dims_mm=[1200, 800, 740]),
        ],
        [
            ProcessLimit(kind="wall_thickness", min=1.5, source_ref="VAL-7"),
            ProcessLimit(kind="envelope", max_dims_mm=[3000, 1500, 1500], source_ref="VAL-8"),
        ],
    )
    assert out.verdict == "manufacturable"  # type: ignore[attr-defined]
    assert out.passed == 2  # type: ignore[attr-defined]


def test_a_feature_outside_its_limit_fails_with_margin(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [DesignFeature(name="slot corner", kind="inside_corner_radius", value=1.0)],
        [ProcessLimit(kind="inside_corner_radius", min=3.0, source_ref="router 6 mm cutter")],
    )
    finding = out.findings[0]  # type: ignore[attr-defined]
    assert out.verdict == "not_manufacturable"  # type: ignore[attr-defined]
    assert finding.status == "fail" and finding.margin == pytest.approx(-2.0)
    assert finding.limit_source == "router 6 mm cutter"


def test_no_sourced_limit_is_unknown_never_a_pass(ctx: SkillContext) -> None:
    out = _run(ctx, [DesignFeature(name="top holes", kind="hole_diameter", value=8.0)], [])
    assert out.verdict == "incomplete"  # type: ignore[attr-defined]
    assert out.findings[0].status == "unknown"  # type: ignore[attr-defined]
    assert "no sourced hole_diameter limit" in out.unknowns[0]  # type: ignore[attr-defined]


def test_nothing_given_is_incomplete(ctx: SkillContext) -> None:
    out = _run(ctx, [], [])
    assert out.verdict == "incomplete"  # type: ignore[attr-defined]
    assert out.unknowns  # type: ignore[attr-defined]


def test_the_basis_is_carried_to_the_finding(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [DesignFeature(name="overhang", kind="overhang_angle", value=50.0, basis="measured")],
        [ProcessLimit(kind="overhang_angle", max=45.0, source_ref="printer guide")],
    )
    finding = out.findings[0]  # type: ignore[attr-defined]
    assert finding.basis == "measured" and finding.status == "fail"
    assert "deg" in finding.message
