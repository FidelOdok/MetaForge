"""Input/output schemas for the generate_checklist skill."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from domain_agents.compliance.models import ChecklistItem, ComplianceRegime


class GenerateChecklistInput(BaseModel):
    """Input for the checklist generation skill."""

    project_id: str = Field(..., min_length=1, description="Project identifier")
    product_category: str = Field(
        default="consumer_electronics", description="Product category"
    )
    target_markets: list[ComplianceRegime] = Field(
        ..., min_length=1, description="Target market regimes"
    )


class GenerateChecklistOutput(BaseModel):
    """Output from the checklist generation skill."""

    project_id: str = Field(..., description="Project identifier")
    target_markets: list[ComplianceRegime] = Field(..., description="Markets included")
    items: list[ChecklistItem] = Field(..., description="Generated checklist items")
    total_items: int = Field(..., description="Total item count")
    coverage_percent: float = Field(..., description="Evidence coverage percentage")
    generated_at: datetime = Field(..., description="Generation timestamp")
