"""Bill-of-Materials read API (MET-504).

Serves ``GET /v1/bom`` for the dashboard BOM page, which previously 404'd. BOM
line items live in the Digital Twin as ``BOM_ITEM`` nodes (``twin_core`` model
``BOMItem``); this maps them to the dashboard's ``BomComponent`` shape, scoped to
a project. Returns an empty list (not an error) when a project has no BOM yet.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.bom_item import BOMItem

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.bom.routes")

_VALID_STATUS = {"available", "low_stock", "out_of_stock", "alternate_needed"}

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("bom_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/bom", tags=["bom"])


class BomComponentResponse(BaseModel):
    """One BOM line item, in the dashboard's camelCase shape."""

    id: str
    designator: str
    partNumber: str  # noqa: N815 — dashboard contract is camelCase
    description: str
    manufacturer: str
    quantity: int
    unitPrice: float  # noqa: N815
    status: Literal["available", "low_stock", "out_of_stock", "alternate_needed"]
    category: str
    projectId: str  # noqa: N815


class BomListResponse(BaseModel):
    components: list[BomComponentResponse]
    total: int


def _item_to_component(item: BOMItem) -> BomComponentResponse:
    specs = item.specifications or {}
    status = str(specs.get("status", "available"))
    if status not in _VALID_STATUS:
        status = "available"
    return BomComponentResponse(
        id=str(item.id),
        designator=", ".join(item.reference_designators),
        partNumber=item.part_number,
        description=item.description,
        manufacturer=item.manufacturer,
        quantity=item.quantity,
        unitPrice=float(item.unit_cost) if item.unit_cost is not None else 0.0,
        status=status,  # type: ignore[arg-type]
        category=str(specs.get("category", "uncategorized")),
        projectId=str(item.project_id) if item.project_id else "",
    )


@router.get("", response_model=BomListResponse)
async def list_bom(project_id: str | None = None) -> BomListResponse:
    """List BOM components, optionally scoped to a project.

    Empty (``{components: [], total: 0}``) when the project has no BOM — not a
    404, so the dashboard renders a clean empty state.
    """
    with tracer.start_as_current_span("bom.list") as span:
        scoped: UUID | None = None
        if project_id:
            try:
                scoped = UUID(project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
            span.set_attribute("bom.project_id", project_id)
        items = await _twin.list_bom_items(project_id=scoped)
        components = [_item_to_component(i) for i in items]
        span.set_attribute("bom.count", len(components))
        logger.info("bom_listed", count=len(components), project_id=project_id)
        return BomListResponse(components=components, total=len(components))
