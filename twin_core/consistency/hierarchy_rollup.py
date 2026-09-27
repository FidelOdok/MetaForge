"""Per-branch mass/cost rollup over the product hierarchy (FORGE-260, gap G-B1).

Distinct from ``twin_core.consistency.metrics.compute_metric_total``, which
sums a metadata key flatly across every WorkProduct in a *project* (no
hierarchy awareness at all -- used by the G3 budget/invariant gate for one
project-wide total). This module walks a *branch* of the hierarchy tree
rooted at one HierarchyNode, so "what's the moving mass of just the Upper
Arm subsystem" is answerable, not only "what's the whole project's mass."

Sources read, never re-instrumented (FORGE-260 deliberately adds zero new
fields to WorkProduct/BOMItem):

- ``WorkProduct.metadata["mass_kg"]`` (the FORGE-100 canonical measured-
  property key, already populated on any committed ``cad_model``), read
  through each node's ``EdgeType.REALIZED_BY`` targets.
- ``BOMItem.unit_cost``, read through each node's ``EdgeType.INSTANCE_OF``
  target (a COTS leaf's canonical component record).
- Each ``EdgeType.CONTAINS`` edge's own ``metadata["quantity"]`` (default 1)
  weights that child's whole rolled-up subtotal, not just its own mass --
  3 identical gripper fingers each carrying an upstream assembly of their
  own all multiply through correctly.

A value present but non-numeric is skipped, not coerced (same discipline
``metric_total_with_skips`` uses) -- a rollup silently returning 0 for bad
data would look identical to "genuinely no mass yet," which is exactly the
"no data" vs "pass" distinction the Structure tab (FORGE-261) needs to
render honestly.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from twin_core.api import TwinAPI
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct

_ROLLUP_EDGE_TYPES = [EdgeType.CONTAINS, EdgeType.REALIZED_BY, EdgeType.INSTANCE_OF]
# Generous enough for any real product hierarchy (Product -> System ->
# Subsystem -> Assembly -> Part, plus one hop for its REALIZED_BY/
# INSTANCE_OF leaf link) without an unbounded/configurable depth this
# module doesn't need yet.
_MAX_ROLLUP_DEPTH = 20


class HierarchyRollup(BaseModel):
    """The computed mass/cost total for one hierarchy branch."""

    root_id: UUID
    mass_kg: float
    cost: float
    node_count: int
    """HierarchyNode count in the branch (root included) -- NOT the count of
    REALIZED_BY/INSTANCE_OF leaves, which can outnumber or undernumber it."""
    skipped_node_ids: list[UUID]
    """Nodes whose linked mass_kg/unit_cost existed but wasn't numeric --
    contributed 0, distinct from a node with no value at all."""


async def compute_hierarchy_rollup(twin: TwinAPI, root_id: UUID) -> HierarchyRollup:
    """Sum mass/cost over the CONTAINS tree rooted at ``root_id``.

    Raises ``KeyError`` if ``root_id`` doesn't exist (mirrors
    ``get_subgraph``'s own contract) or isn't a HierarchyNode.
    """
    subgraph = await twin.get_subgraph(
        root_id, depth=_MAX_ROLLUP_DEPTH, edge_types=_ROLLUP_EDGE_TYPES, direction="outgoing"
    )
    nodes_by_id = {n.id: n for n in subgraph.nodes}
    if not isinstance(nodes_by_id.get(root_id), HierarchyNode):
        raise KeyError(f"{root_id} is not a HierarchyNode")
    contains_children: dict[UUID, list[tuple[UUID, float]]] = {}
    realized_by: dict[UUID, list[UUID]] = {}
    instance_of: dict[UUID, list[UUID]] = {}
    for edge in subgraph.edges:
        if edge.edge_type == EdgeType.CONTAINS:
            qty = edge.metadata.get("quantity", 1)
            qty = qty if isinstance(qty, (int, float)) else 1
            contains_children.setdefault(edge.source_id, []).append((edge.target_id, qty))
        elif edge.edge_type == EdgeType.REALIZED_BY:
            realized_by.setdefault(edge.source_id, []).append(edge.target_id)
        elif edge.edge_type == EdgeType.INSTANCE_OF:
            instance_of.setdefault(edge.source_id, []).append(edge.target_id)

    skipped: list[UUID] = []
    visited_for_count: set[UUID] = set()

    def _own_mass_cost(node_id: UUID) -> tuple[float, float]:
        mass = 0.0
        cost = 0.0
        for target_id in realized_by.get(node_id, []):
            target = nodes_by_id.get(target_id)
            if not isinstance(target, WorkProduct):
                continue
            value = target.metadata.get("mass_kg")
            if value is None:
                continue
            try:
                mass += float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                skipped.append(target_id)
        for target_id in instance_of.get(node_id, []):
            target = nodes_by_id.get(target_id)
            if not isinstance(target, BOMItem) or target.unit_cost is None:
                continue
            cost += target.unit_cost
        return mass, cost

    def _rollup(node_id: UUID) -> tuple[float, float]:
        visited_for_count.add(node_id)
        mass, cost = _own_mass_cost(node_id)
        for child_id, qty in contains_children.get(node_id, []):
            child_mass, child_cost = _rollup(child_id)
            mass += child_mass * qty
            cost += child_cost * qty
        return mass, cost

    total_mass, total_cost = _rollup(root_id)
    return HierarchyRollup(
        root_id=root_id,
        mass_kg=total_mass,
        cost=total_cost,
        node_count=len(visited_for_count),
        skipped_node_ids=skipped,
    )
