"""Hierarchical BOM (EBOM) derivation from the product hierarchy (FORGE-267,
gap G-C3).

The flat BOM (``api_gateway/bom/routes.py``) lists every ``BOMItem`` in a
project with no structure -- it can't answer "where is this component used"
or "how many of this part does the WHOLE assembly need, once you account
for 3 of this sub-assembly each needing 2 of it". This module derives that
structured view by walking the ``CONTAINS`` tree (FORGE-260) from a product
root: one line per leaf position reached via ``REALIZED_BY`` (a fabricated
part) or ``INSTANCE_OF`` (a COTS component), each carrying its full ancestor
``path`` (for an indented display) and a ``quantity`` that is the PRODUCT of
every ``CONTAINS`` edge's own quantity along the way -- the same
multiply-down-the-tree rule ``compute_hierarchy_rollup`` (FORGE-260) already
uses for mass/cost, applied here to a per-component count instead.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from twin_core.api import TwinAPI
from twin_core.models.bom_item import BOMItem
from twin_core.models.enums import EdgeType
from twin_core.models.hierarchy_node import HierarchyNode
from twin_core.models.work_product import WorkProduct

_BOM_EDGE_TYPES = [EdgeType.CONTAINS, EdgeType.REALIZED_BY, EdgeType.INSTANCE_OF]
# Generous enough for any real product hierarchy -- see hierarchy_rollup.py's
# own identical constant and rationale.
_MAX_DEPTH = 20


class BomLine(BaseModel):
    """One derived BOM line -- a leaf hierarchy position and the real
    component/part that fulfils it."""

    hierarchy_node_id: UUID
    path: list[str]
    """Ancestor names, root-first, ending with this leaf's own name."""
    quantity: float
    source: str
    """``"instance_of"`` (COTS component) or ``"realized_by"`` (fabricated part)."""
    component_id: UUID
    part_number: str | None
    manufacturer: str | None
    description: str
    unit_cost: float | None


async def compute_hierarchical_bom(twin: TwinAPI, root_id: UUID) -> list[BomLine]:
    """Every BOM line reachable from ``root_id``'s CONTAINS subtree.

    Raises ``KeyError`` if ``root_id`` doesn't exist or isn't a
    HierarchyNode (mirrors ``compute_hierarchy_rollup``'s own contract).
    """
    subgraph = await twin.get_subgraph(
        root_id, depth=_MAX_DEPTH, edge_types=_BOM_EDGE_TYPES, direction="outgoing"
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

    lines: list[BomLine] = []

    def _walk(node_id: UUID, ancestor_path: list[str], quantity: float) -> None:
        node = nodes_by_id.get(node_id)
        name = node.name if isinstance(node, HierarchyNode) else str(node_id)
        path = [*ancestor_path, name]

        for target_id in instance_of.get(node_id, []):
            target = nodes_by_id.get(target_id)
            if isinstance(target, BOMItem):
                lines.append(
                    BomLine(
                        hierarchy_node_id=node_id,
                        path=path,
                        quantity=quantity,
                        source="instance_of",
                        component_id=target.id,
                        part_number=target.part_number,
                        manufacturer=target.manufacturer,
                        description=target.description,
                        unit_cost=target.unit_cost,
                    )
                )
        for target_id in realized_by.get(node_id, []):
            target = nodes_by_id.get(target_id)
            if isinstance(target, WorkProduct):
                lines.append(
                    BomLine(
                        hierarchy_node_id=node_id,
                        path=path,
                        quantity=quantity,
                        source="realized_by",
                        component_id=target.id,
                        part_number=None,
                        manufacturer=None,
                        description=target.name,
                        unit_cost=None,
                    )
                )
        for child_id, child_qty in contains_children.get(node_id, []):
            _walk(child_id, path, quantity * child_qty)

    _walk(root_id, [], 1.0)
    return lines
