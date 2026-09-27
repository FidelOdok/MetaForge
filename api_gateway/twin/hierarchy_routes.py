"""Product hierarchy tree API (FORGE-261, sibling to FORGE-260's data model).

``GET /v1/twin/hierarchy`` for the dashboard's Structure tab: every
HierarchyNode in a project, each carrying its own parent link (from the
incoming ``CONTAINS`` edge) and its own rolled-up mass/cost (via
``compute_hierarchy_rollup``), so the dashboard can render a tree-table
without doing any graph traversal client-side.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observability.tracing import get_tracer
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.hierarchy_rollup import compute_hierarchy_rollup
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.hierarchy_routes")

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default in-memory twin with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("hierarchy_routes_twin_initialized", twin_type=type(twin).__name__)


router = APIRouter(prefix="/v1/twin", tags=["twin"])


class HierarchyNodeResponse(BaseModel):
    """One HierarchyNode, in the dashboard's camelCase shape."""

    id: str
    name: str
    kind: str
    parentId: str | None = None  # noqa: N815 — dashboard contract is camelCase
    quantity: float | None = None
    placement: dict[str, Any] | None = None
    massKg: float  # noqa: N815
    cost: float


class HierarchyTreeResponse(BaseModel):
    nodes: list[HierarchyNodeResponse]


@router.get("/hierarchy", response_model=HierarchyTreeResponse)
async def get_hierarchy_tree(project_id: str | None = None) -> HierarchyTreeResponse:
    """List a project's hierarchy nodes, each with its parent link and its
    own rolled-up mass/cost.

    Empty (not a 404) when the project has none yet, matching ``/v1/bom``.
    Note: computes one rollup per node (each its own subtree walk) -- fine
    at the scale a hand-built product hierarchy actually reaches, not
    optimized for a tree of thousands of nodes.
    """
    with tracer.start_as_current_span("twin.get_hierarchy_tree") as span:
        scoped: UUID | None = None
        if project_id:
            try:
                scoped = UUID(project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
            span.set_attribute("hierarchy.project_id", project_id)

        nodes = await _twin.list_hierarchy_nodes(project_id=scoped)

        parent_of: dict[UUID, UUID] = {}
        quantity_of: dict[UUID, float] = {}
        placement_of: dict[UUID, dict[str, Any] | None] = {}
        for node in nodes:
            incoming = await _twin.get_edges(
                node.id, direction="incoming", edge_type=EdgeType.CONTAINS
            )
            if incoming:
                edge = incoming[0]
                parent_of[node.id] = edge.source_id
                qty = edge.metadata.get("quantity", 1)
                quantity_of[node.id] = qty if isinstance(qty, (int, float)) else 1
                placement_of[node.id] = edge.metadata.get("placement")

        result: list[HierarchyNodeResponse] = []
        for node in nodes:
            try:
                rollup = await compute_hierarchy_rollup(_twin, node.id)
                mass_kg, cost = rollup.mass_kg, rollup.cost
            except KeyError:
                # Shouldn't happen (every node here came from list_hierarchy_nodes
                # itself), but a rollup failure must never break the whole list.
                mass_kg, cost = 0.0, 0.0
            parent_id = parent_of.get(node.id)
            result.append(
                HierarchyNodeResponse(
                    id=str(node.id),
                    name=node.name,
                    kind=node.kind,
                    parentId=str(parent_id) if parent_id else None,
                    quantity=quantity_of.get(node.id),
                    placement=placement_of.get(node.id),
                    massKg=mass_kg,
                    cost=cost,
                )
            )
        span.set_attribute("hierarchy.count", len(result))
        logger.info("hierarchy_tree_listed", count=len(result), project_id=project_id)
        return HierarchyTreeResponse(nodes=result)
