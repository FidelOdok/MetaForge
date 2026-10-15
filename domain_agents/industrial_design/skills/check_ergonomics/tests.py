"""In-skill tests for check_ergonomics (run via pytest discovery)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext

from .handler import CheckErgonomicsHandler
from .schema import CheckErgonomicsInput, CheckErgonomicsOutput, ErgoFeature, ErgoLimit


@pytest.fixture()
def ctx() -> SkillContext:
    context = MagicMock(spec=SkillContext)
    context.twin = AsyncMock()
    context.logger = MagicMock()
    context.logger.bind = MagicMock(return_value=context.logger)
    context.session_id = uuid4()
    context.branch = "main"
    return context


USERS = "UK adults, 5th percentile female to 95th percentile male"


def _run(
    ctx: SkillContext, features: list[ErgoFeature], limits: list[ErgoLimit]
) -> CheckErgonomicsOutput:
    return asyncio.run(
        CheckErgonomicsHandler(ctx).execute(
            CheckErgonomicsInput(
                product="drill", user_population=USERS, features=features, limits=limits
            )
        )
    )


def _limit(kind: str, lo: float | None, hi: float | None, population: str = USERS) -> ErgoLimit:
    return ErgoLimit(
        kind=kind, min=lo, max=hi, population=population, source_ref="dataset X, table 3"
    )


def test_features_within_sourced_limits_fit(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [ErgoFeature(name="handle", kind="grip_diameter", value=40, basis="declared")],
        [_limit("grip_diameter", 30, 50)],
    )
    assert out.verdict == "fits"
    (finding,) = out.findings
    assert finding.margin == 10 and finding.unit == "mm"
    assert finding.limit_source == "dataset X, table 3"
    assert out.population_mismatches == []


def test_a_feature_outside_a_limit_does_not_fit(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [
            ErgoFeature(name="handle", kind="grip_diameter", value=58),
            ErgoFeature(name="body", kind="product_mass", value=1.2),
        ],
        [_limit("grip_diameter", 30, 50), _limit("product_mass", None, 2.3)],
    )
    assert out.verdict == "does_not_fit"
    assert [f.status for f in out.findings] == ["fail", "pass"]
    assert out.findings[0].margin == -8
    assert out.findings[1].unit == "kg"


def test_missing_limits_and_values_are_unknown_never_a_pass(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [
            ErgoFeature(name="trigger", kind="actuation_force", value=12),
            ErgoFeature(name="handle", kind="grip_diameter"),
        ],
        [_limit("grip_diameter", 30, 50)],
    )
    assert out.verdict == "incomplete"
    assert out.unknown == 2
    assert any("no sourced actuation_force limit" in u for u in out.unknowns)
    assert _run(ctx, [], []).verdict == "incomplete"


def test_a_limit_for_other_users_is_reported(ctx: SkillContext) -> None:
    out = _run(
        ctx,
        [ErgoFeature(name="handle", kind="grip_diameter", value=40)],
        [_limit("grip_diameter", 30, 50, population="US Army 1988 male")],
    )
    assert out.verdict == "fits"
    assert out.population_mismatches == [
        f"grip_diameter: limit is for 'US Army 1988 male', the users are '{USERS}'"
    ]
