"""Input/output schemas for the create_procurement_record skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ProcurementLineItem(BaseModel):
    """A single line item on a procurement record."""

    part_number: str = Field(..., min_length=1)
    description: str = Field(default="")
    quantity: float = Field(..., gt=0)
    unit_cost: float = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    distributor: str = Field(default="")
    lead_time_days: int = Field(default=0, ge=0)


class CreateProcurementRecordInput(BaseModel):
    """Input for the procurement-record skill."""

    name: str = Field(..., min_length=1, description="Record name, e.g. 'Quadruped Rev A PO'")
    line_items: list[ProcurementLineItem] = Field(..., min_length=1)
    notes: str = Field(default="")
    bom_work_product_id: UUID | None = Field(
        default=None, description="Source BOM work product this was sourced from"
    )
    project_id: str | None = Field(default=None)


class CreateProcurementRecordOutput(BaseModel):
    """Output from the procurement-record skill."""

    node_id: str = Field(..., description="PROCUREMENT_RECORD work-product node id")
    line_item_count: int = Field(..., ge=0)
    total_cost: float = Field(..., ge=0)
    currency: str = Field(...)
    max_lead_time_days: int = Field(..., ge=0)
