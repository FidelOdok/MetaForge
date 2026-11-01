"""Input/output schemas for the check_power_budget skill (FORGE-544).

The rail and load shapes are validated by the ``power.check_budget`` tool
the handler calls (domain agents do not import tool code); they are
documented here and in SKILL.md.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CheckPowerBudgetInput(BaseModel):
    """Input for the check_power_budget skill."""

    work_product_id: UUID | None = Field(
        default=None, description="Schematic or BOM work product the budget is for"
    )
    rails: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "Each: name, voltage_v, source_kind (supply/ldo/switching), "
            "rated_current_ma, input_rail (regulators), efficiency (switching), "
            "quiescent_ma (ldo), source (citation)"
        ),
    )
    loads: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Each: name, rail, current_ma or power_mw, source (citation)",
    )
    derating: float = Field(
        ...,
        gt=0,
        le=1,
        description="Allowed share of each rated output, from the user (0.8 = 80 %)",
    )


class CheckPowerBudgetOutput(BaseModel):
    """Output from the check_power_budget skill."""

    work_product_id: UUID | None = None
    verdict: Literal["pass", "fail", "not_established"]
    passed: bool
    derating: float
    rails: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per rail: load_ma, allowed_ma, headroom_ma/_pct, status, unknowns",
    )
    worst_rail: str | None = None
    source_power_mw: float | None = None
    summary: str = ""
