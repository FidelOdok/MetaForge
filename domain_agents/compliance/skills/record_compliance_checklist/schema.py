"""Input/output schemas for the record_compliance_checklist skill."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from domain_agents.compliance.models import (
    ChecklistItem,
    ComplianceRegime,
    ExcludedItem,
    ItemEvidence,
)


class RecordComplianceChecklistInput(BaseModel):
    """Input for the checklist-persistence skill -- identical shape to
    generate_checklist's input, since it reuses the same ChecklistGenerator."""

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


class RecordComplianceChecklistOutput(BaseModel):
    """Output from the checklist-persistence skill."""

    node_id: str = Field(..., description="COMPLIANCE_CHECKLIST work-product node id")
    target_markets: list[ComplianceRegime] = Field(..., description="Markets included")
    items: list[ChecklistItem] = Field(..., description="Generated checklist items")
    total_items: int = Field(..., description="Total item count")
    coverage_percent: float = Field(..., description="Evidence coverage percentage")
    excluded_items: list[ExcludedItem] = Field(default_factory=list)
    conditional_items: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(..., description="Generation timestamp")
