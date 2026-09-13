"""Input/output schemas for the record_compliance_checklist skill."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from domain_agents.compliance.models import ChecklistItem, ComplianceRegime


class RecordComplianceChecklistInput(BaseModel):
    """Input for the checklist-persistence skill -- identical shape to
    generate_checklist's input, since it reuses the same ChecklistGenerator."""

    project_id: str = Field(..., min_length=1, description="Project identifier")
    product_category: str = Field(default="consumer_electronics", description="Product category")
    target_markets: list[ComplianceRegime] = Field(
        ..., min_length=1, description="Target market regimes"
    )


class RecordComplianceChecklistOutput(BaseModel):
    """Output from the checklist-persistence skill."""

    node_id: str = Field(..., description="COMPLIANCE_CHECKLIST work-product node id")
    target_markets: list[ComplianceRegime] = Field(..., description="Markets included")
    items: list[ChecklistItem] = Field(..., description="Generated checklist items")
    total_items: int = Field(..., description="Total item count")
    coverage_percent: float = Field(..., description="Evidence coverage percentage")
    generated_at: datetime = Field(..., description="Generation timestamp")
