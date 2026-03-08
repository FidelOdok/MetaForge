"""Input/output schemas for the score_bom_risk skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from domain_agents.supply_chain.models import BOMRiskReport


class BOMItem(BaseModel):
    """A single BOM line item for risk scoring."""

    mpn: str = Field(..., description="Manufacturer Part Number")
    manufacturer: str = Field(default="", description="Part manufacturer")
    quantity: int = Field(default=1, ge=1, description="Required quantity")
    description: str = Field(default="", description="Part description")
    distributor_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional distributor data with keys: stock, lead_time_weeks, "
            "price, prices, lifecycle, num_sources, moq, "
            "rohs_compliant, reach_compliant"
        ),
    )


class ScoreBomRiskInput(BaseModel):
    """Input for the score_bom_risk skill."""

    project_id: str = Field(..., description="Project identifier")
    bom_items: list[BOMItem] = Field(
        ..., description="List of BOM items to score"
    )


class ScoreBomRiskOutput(BaseModel):
    """Output from the score_bom_risk skill."""

    report: BOMRiskReport = Field(..., description="Complete BOM risk report")
