"""In-skill tests for check_cmf_compatibility (run via pytest discovery)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext

from .handler import CheckCmfCompatibilityHandler
from .schema import (
    AppearanceRecord,
    CheckCmfCompatibilityInput,
    CheckCmfCompatibilityOutput,
    PartCmf,
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


ANODISED = AppearanceRecord(
    name="black_anodised_6061",
    finish="anodised",
    materials=["aluminium_6061_t6"],
    processes=["anodising"],
    colour={"code": "RAL 9005", "needs_review": True},
    properties={"render_metallic": {"value": 0.0, "needs_review": True}},
)
UNRECORDED = AppearanceRecord(name="ral9006_white_aluminium_powder_coat", finish="powder_coated")


def _run(ctx: SkillContext, parts: list[PartCmf]) -> CheckCmfCompatibilityOutput:
    return asyncio.run(
        CheckCmfCompatibilityHandler(ctx).execute(
            CheckCmfCompatibilityInput(parts=parts, appearances=[ANODISED, UNRECORDED])
        )
    )


def test_an_appearance_recorded_for_the_material_is_compatible(ctx: SkillContext) -> None:
    out = _run(
        ctx, [PartCmf(name="body", material="Aluminum_6061_T6", appearance="black_anodised_6061")]
    )
    assert out.verdict == "compatible"
    assert out.finishing_processes == ["anodising"]
    assert out.needs_review == [
        "black_anodised_6061: colour",
        "black_anodised_6061: render_metallic",
    ]


def test_an_appearance_on_another_material_is_incompatible(ctx: SkillContext) -> None:
    out = _run(ctx, [PartCmf(name="cap", material="abs", appearance="black_anodised_6061")])
    assert out.verdict == "incompatible"
    assert "recorded for aluminium_6061_t6, not abs" in out.findings[0].message


def test_what_is_not_recorded_is_unknown(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [
            PartCmf(
                name="legs", material="steel", appearance="ral9006_white_aluminium_powder_coat"
            ),
            PartCmf(name="top", material="birch_ply"),
            PartCmf(name="foot", material="tpu", appearance="soft_touch"),
            PartCmf(name="lid", appearance="black_anodised_6061"),
        ],
    )
    assert out.verdict == "incomplete"
    assert [f.status for f in out.findings] == ["unknown"] * 4
    assert "does not record which materials" in out.findings[0].message
    assert "no appearance chosen" in out.findings[1].message
    assert "read it with library.get_appearance" in out.findings[2].message
    assert "no material stated" in out.findings[3].message
