"""In-skill tests for form_factor_envelope (run via pytest discovery)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext

from .handler import FormFactorEnvelopeHandler
from .schema import (
    Component,
    FormFactorEnvelopeInput,
    FormFactorEnvelopeOutput,
    SourcedEnvelope,
    SourcedLength,
)


@pytest.fixture()
def ctx() -> SkillContext:
    context = MagicMock(spec=SkillContext)
    context.twin = AsyncMock()
    context.logger = MagicMock()
    context.logger.bind = MagicMock(return_value=context.logger)
    context.session_id = uuid4()
    context.branch = "main"
    return context


WALL = SourcedLength(value_mm=2.0, source_ref="dv_wall (FDM min wall, process fdm)")
CLEAR = SourcedLength(value_mm=1.0, source_ref="stated by the user")


def _run(ctx: SkillContext, **kwargs: object) -> FormFactorEnvelopeOutput:
    data = {"clearance": CLEAR, "wall_thickness": WALL, **kwargs}
    return asyncio.run(FormFactorEnvelopeHandler(ctx).execute(FormFactorEnvelopeInput(**data)))


def test_stacked_components_add_clearance_and_wall(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        components=[
            Component(name="battery", dims_mm=[60, 35, 10], basis="datasheet"),
            Component(name="pcb", dims_mm=[50, 40, 1.6], basis="measured"),
        ],
        stack_axis="z",
        gap_mm=3,
    )
    assert out.layout == "stacked"
    assert out.contents_dims_mm == [60, 40, 14.6]
    assert out.inner_dims_mm == [62, 42, 16.6]
    assert out.outer_dims_mm == [66, 46, 20.6]
    assert out.component_volume_cm3 == pytest.approx(21 + 3.2)
    assert out.fits is None
    assert "no maximum envelope given" in out.messages[-1]
    assert out.basis == ["battery: datasheet", "pcb: measured"]


def test_positioned_components_union_and_report_overlaps(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        components=[
            Component(name="a", dims_mm=[10, 10, 10], position_mm=[0, 0, 0]),
            Component(name="b", dims_mm=[10, 10, 10], position_mm=[5, 0, 0]),
            Component(name="c", dims_mm=[5, 5, 5], position_mm=[0, 20, 0]),
        ],
    )
    assert out.layout == "positioned"
    assert out.contents_dims_mm == [15, 25, 10]
    assert out.overlaps == ["a and b overlap"]


def test_the_fit_check_is_orientation_free_and_sourced(ctx: SkillContext) -> None:
    comps = [Component(name="core", dims_mm=[100, 20, 50])]
    fits = _run(
        ctx,
        components=comps,
        stack_axis="x",
        max_envelope=SourcedEnvelope(dims_mm=[30, 110, 60], source_ref="REQ-12 pocketable"),
    )
    assert fits.outer_dims_mm == [106, 26, 56]
    assert fits.fits is True and fits.margin_mm == [4, 4, 4]
    assert "max envelope: REQ-12 pocketable" in fits.sources
    too_big = _run(
        ctx,
        components=comps,
        stack_axis="x",
        max_envelope=SourcedEnvelope(dims_mm=[25, 110, 60], source_ref="REQ-12"),
    )
    assert too_big.fits is False
    assert too_big.margin_mm is not None and too_big.margin_mm[0] == -1


def test_layout_must_be_stated(ctx: SkillContext) -> None:
    with pytest.raises(ValueError, match="stack_axis"):
        _run(ctx, components=[Component(name="a", dims_mm=[1, 1, 1])])
    with pytest.raises(ValueError, match="every component or for none"):
        _run(
            ctx,
            components=[
                Component(name="a", dims_mm=[1, 1, 1], position_mm=[0, 0, 0]),
                Component(name="b", dims_mm=[1, 1, 1]),
            ],
        )
