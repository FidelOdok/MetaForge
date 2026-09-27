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
from twin_core.consistency.budgets import budget_from_entity
from twin_core.consistency.hierarchy_budget import compute_budget_allocation_status
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
    # FORGE-264 (gap G-B4): set only when a "mass"/"cost" budget entity has
    # an allocation whose target resolves to this node's id. None means no
    # allocation targets this node -- not "not over budget".
    massBudgetKg: float | None = None  # noqa: N815
    massOverBudget: bool | None = None  # noqa: N815
    costBudget: float | None = None  # noqa: N815
    costOverBudget: bool | None = None  # noqa: N815


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

        # FORGE-264: mass/cost allocation status per node, from any "budget"
        # entity whose allocation target resolves to a real node here.
        # Project-scoped only -- an unscoped listing (project_id omitted)
        # skips budget lookup entirely rather than guessing which project's
        # budgets apply.
        mass_status_by_node: dict[UUID, Any] = {}
        cost_status_by_node: dict[UUID, Any] = {}
        if scoped is not None:
            budget_entities = await _twin.list_engineering_entities(
                project_id=scoped, entity_type="budget"
            )
            for entity in budget_entities:
                try:
                    budget = budget_from_entity(entity, scoped)
                except ValueError as exc:
                    logger.warning(
                        "hierarchy_budget_entity_malformed",
                        entity_id=str(entity.id),
                        error=str(exc),
                    )
                    continue
                if budget.metric not in ("mass", "cost"):
                    continue
                statuses = await compute_budget_allocation_status(_twin, budget)
                target_map = mass_status_by_node if budget.metric == "mass" else cost_status_by_node
                for status in statuses:
                    try:
                        target_id = UUID(status.target)
                    except ValueError:
                        continue
                    if target_id not in target_map:  # first matching budget wins
                        target_map[target_id] = status

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
            mass_status = mass_status_by_node.get(node.id)
            cost_status = cost_status_by_node.get(node.id)
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
                    massBudgetKg=mass_status.allocated if mass_status else None,
                    massOverBudget=mass_status.over_budget if mass_status else None,
                    costBudget=cost_status.allocated if cost_status else None,
                    costOverBudget=cost_status.over_budget if cost_status else None,
                )
            )
        span.set_attribute("hierarchy.count", len(result))
        logger.info("hierarchy_tree_listed", count=len(result), project_id=project_id)
        return HierarchyTreeResponse(nodes=result)
