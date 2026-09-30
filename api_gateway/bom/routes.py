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
from twin_core.consistency.hierarchical_bom import compute_hierarchical_bom
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
    priceCurrency: str  # noqa: N815 — ISO 4217, e.g. "USD"/"GBP"
    status: Literal["available", "low_stock", "out_of_stock", "alternate_needed"]
    category: str
    projectId: str  # noqa: N815
    imageUrl: str | None = None  # noqa: N815
    purchaseUrl: str | None = None  # noqa: N815
    datasheetUrl: str | None = None  # noqa: N815
    footprint: str | None = None
    cadModelUrl: str | None = None  # noqa: N815


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
        priceCurrency=item.price_currency or "USD",
        status=status,  # type: ignore[arg-type]
        category=str(specs.get("category", "uncategorized")),
        projectId=str(item.project_id) if item.project_id else "",
        imageUrl=item.image_url or None,
        purchaseUrl=item.purchase_url or None,
        datasheetUrl=item.datasheet_url or None,
        footprint=item.footprint or None,
        cadModelUrl=item.cad_model_url or None,
    )


@router.get("", response_model=BomListResponse)
async def list_bom(project_id: str | None = None, category: str | None = None) -> BomListResponse:
    """List BOM components, optionally scoped to a project and/or filtered
    by ``category`` (case-insensitive exact match against
    ``BOMItem.specifications.category``, e.g. ``category=fastener`` for a
    fastener list -- FORGE-294, gap G-H2).

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
        if category:
            span.set_attribute("bom.category_filter", category)
        items = await _twin.list_bom_items(project_id=scoped)
        components: list[BomComponentResponse] = []
        skipped = 0
        for i in items:
            # FORGE-242: list_bom_items is typed list[BOMItem], but a legacy
            # row (written before a node_type existed, or by some other path
            # that never set it) can still degrade to a bare NodeBase on
            # read-back -- one such row must not 500 the whole endpoint for
            # every project. The real fix is closing the deserialization gap
            # (neo4j_graph_engine.py's _props_to_node) so this almost never
            # fires; this is the belt-and-suspenders backstop for whatever
            # that fix doesn't cover (truly legacy data, a future new gap).
            if not isinstance(i, BOMItem):
                skipped += 1
                logger.warning(
                    "bom_item_skipped_not_bom_item",
                    node_id=str(getattr(i, "id", "?")),
                    node_type=type(i).__name__,
                )
                continue
            components.append(_item_to_component(i))
        if category:
            wanted = category.strip().lower()
            components = [c for c in components if c.category.lower() == wanted]
        span.set_attribute("bom.count", len(components))
        if skipped:
            span.set_attribute("bom.skipped", skipped)
        logger.info("bom_listed", count=len(components), skipped=skipped, project_id=project_id)
        return BomListResponse(components=components, total=len(components))


class HierarchicalBomLineResponse(BaseModel):
    """One derived EBOM line, in the dashboard's camelCase shape."""

    hierarchyNodeId: str  # noqa: N815 — dashboard contract is camelCase
    path: list[str]
    quantity: float
    source: str
    componentId: str  # noqa: N815
    partNumber: str | None = None  # noqa: N815
    manufacturer: str | None = None
    description: str
    unitCost: float | None = None  # noqa: N815


class HierarchicalBomResponse(BaseModel):
    lines: list[HierarchicalBomLineResponse]
    total: int


@router.get("/hierarchical", response_model=HierarchicalBomResponse)
async def list_hierarchical_bom(project_id: str | None = None) -> HierarchicalBomResponse:
    """Derive the hierarchical BOM (EBOM) -- FORGE-267, gap G-C3 -- from
    every "product"-kind HierarchyNode's CONTAINS tree in a project.

    Empty (not an error) when the project has no product hierarchy yet
    (FORGE-260's tools haven't been used for it) -- matches the flat BOM's
    own convention.
    """
    with tracer.start_as_current_span("bom.list_hierarchical") as span:
        scoped: UUID | None = None
        if project_id:
            try:
                scoped = UUID(project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
            span.set_attribute("bom.project_id", project_id)

        roots = await _twin.list_hierarchy_nodes(project_id=scoped, kind="product")
        lines: list[HierarchicalBomLineResponse] = []
        for root in roots:
            try:
                derived = await compute_hierarchical_bom(_twin, root.id)
            except KeyError:
                # Shouldn't happen (every root here came from
                # list_hierarchy_nodes itself), but one bad root must never
                # break the whole listing.
                continue
            for line in derived:
                lines.append(
                    HierarchicalBomLineResponse(
                        hierarchyNodeId=str(line.hierarchy_node_id),
                        path=line.path,
                        quantity=line.quantity,
                        source=line.source,
                        componentId=str(line.component_id),
                        partNumber=line.part_number,
                        manufacturer=line.manufacturer,
                        description=line.description,
                        unitCost=line.unit_cost,
                    )
                )
        span.set_attribute("bom.hierarchical_line_count", len(lines))
        logger.info("hierarchical_bom_listed", count=len(lines), project_id=project_id)
        return HierarchicalBomResponse(lines=lines, total=len(lines))
