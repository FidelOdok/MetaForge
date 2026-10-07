"""Input/output schemas for the generate_checklist skill."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from domain_agents.compliance.models import (
    ChecklistItem,
    ComplianceRegime,
    ExcludedItem,
    ItemEvidence,
)


class GenerateChecklistInput(BaseModel):
    """Input for the checklist generation skill."""

    project_id: str = Field(..., min_length=1, description="Project identifier")
    product_category: str = Field(
        default="consumer_electronics",
        description="Product category, a label; applicability comes from product_features",
    )
    target_markets: list[ComplianceRegime] = Field(
        ..., min_length=1, description="Target market regimes"
    )
    product_features: list[str] | None = Field(
        default=None,
        description=(
            "What the product has: radio, mains_powered, battery, connected, body_worn. "
            "Items needing a feature it lacks are excluded (FORGE-553). Left out, every "
            "item is kept and the conditional ones are listed in conditional_items"
        ),
    )
    evidence: dict[str, ItemEvidence] = Field(
        default_factory=dict,
        description="Evidence already held, by checklist item id; drives coverage_percent",
    )


class GenerateChecklistOutput(BaseModel):
    """Output from the checklist generation skill."""

    project_id: str = Field(..., description="Project identifier")
    target_markets: list[ComplianceRegime] = Field(..., description="Markets included")
    items: list[ChecklistItem] = Field(..., description="Generated checklist items")
    total_items: int = Field(..., description="Total item count")
    coverage_percent: float = Field(..., description="Evidence coverage percentage")
    excluded_items: list[ExcludedItem] = Field(
        default_factory=list, description="Items left out because the product lacks a feature"
    )
    conditional_items: list[str] = Field(
        default_factory=list,
        description="Items kept only because product_features was not stated",
    )
    generated_at: datetime = Field(..., description="Generation timestamp")
