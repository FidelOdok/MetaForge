"""HierarchyNode — the product hierarchy tree (FORGE-260, gap G-B1).

Mirrors :class:`~twin_core.models.engineering_entity.EngineeringEntity`'s
precedent exactly: one generic node type discriminated by ``kind``, not one
model class per organizational level. A hierarchy node is a pure
*organizational* position (Product/System/Subsystem/Assembly) -- it never
holds design content of its own. What a position actually IS made of is
reached via edges, not fields:

- ``EdgeType.CONTAINS`` (parent -> child, ``metadata={"quantity", "placement"}``)
  nests one hierarchy position inside another.
- ``EdgeType.REALIZED_BY`` (this node -> a ``cad_model``/``robot_description``
  WorkProduct) links a position to the real geometry that fulfils it.
- ``EdgeType.INSTANCE_OF`` (this node -> a ``BOMItem``) links a COTS leaf
  position to the one canonical component record it's an instance of.

A fabricated part or COTS component is deliberately NOT a HierarchyNode --
it's the already-real ``WorkProductType.CAD_MODEL`` or ``BOMItem`` a
hierarchy leaf node's REALIZED_BY/INSTANCE_OF edge points to, so nothing is
duplicated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType

HierarchyNodeKind = Literal["product", "system", "subsystem", "assembly"]


class HierarchyNode(NodeBase):
    """One position in a project's product hierarchy tree."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.HIERARCHY_NODE
    kind: HierarchyNodeKind
    name: str
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Type-specific/derived fields (maturity, lifecycle risk, ...) live here,
    # the same convention WorkProduct.metadata/EngineeringEntity.metadata
    # already use everywhere in this codebase. Rolled-up mass/cost are
    # deliberately NOT cached here -- see
    # twin_core.consistency.hierarchy_rollup.compute_hierarchy_rollup,
    # computed live so it can never go stale relative to its children.
    metadata: dict = Field(default_factory=dict)
