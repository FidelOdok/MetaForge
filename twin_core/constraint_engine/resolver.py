"""Constraint discovery — resolve which constraints apply to a set of work_products."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from twin_core.models.constraint import Constraint
from twin_core.models.enums import EdgeType, NodeType

if TYPE_CHECKING:
    from twin_core.graph_engine import GraphEngine


async def resolve_constraints(
    graph: GraphEngine,
    work_product_ids: list[UUID],
) -> list[Constraint]:
    """Two-phase constraint resolution.

    1. Follow outgoing CONSTRAINED_BY edges from each work_product to find direct constraints.
    2. Include all ``cross_domain=True`` constraints from the graph.
    3. Deduplicate by constraint ID.
    """
    seen: dict[UUID, Constraint] = {}

    # Phase 1: direct constraints via CONSTRAINED_BY edges
    for work_product_id in work_product_ids:
        edges = await graph.get_edges(
            work_product_id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
        )
        for edge in edges:
            if edge.target_id not in seen:
                node = await graph.get_node(edge.target_id)
                if node is not None and isinstance(node, Constraint):
                    seen[node.id] = node

    # Phase 2: all cross-domain constraints
    all_constraints = await graph.list_nodes(node_type=NodeType.CONSTRAINT)
    for node in all_constraints:
        if isinstance(node, Constraint) and node.cross_domain and node.id not in seen:
            seen[node.id] = node

    return list(seen.values())


async def find_constrained_work_products(
    graph: GraphEngine,
    constraint_id: UUID,
) -> list[UUID]:
    """Reverse lookup: find which work_products a constraint applies to.

    Follows incoming CONSTRAINED_BY edges to the constraint node.
    """
    edges = await graph.get_edges(
        constraint_id, direction="incoming", edge_type=EdgeType.CONSTRAINED_BY
    )
    return [edge.source_id for edge in edges]
