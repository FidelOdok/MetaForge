"""Typed edge models and subgraph response for the Digital Twin."""

from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.models.base import EdgeBase, NodeBase
from twin_core.models.enums import EdgeType


class DependsOnEdge(EdgeBase):
    """WorkProduct A requires WorkProduct B."""

    edge_type: EdgeType = EdgeType.DEPENDS_ON
    dependency_type: str = "hard"  # "hard" or "soft"
    description: str = ""


class UsesComponentEdge(EdgeBase):
    """WorkProduct references a physical component."""

    edge_type: EdgeType = EdgeType.USES_COMPONENT
    reference_designator: str = ""  # e.g. "R1", "U3"
    quantity: int = 1


class ConstrainedByEdge(EdgeBase):
    """Constraint applies to an work_product."""

    edge_type: EdgeType = EdgeType.CONSTRAINED_BY
    scope: str = "local"  # "local" or "global"
    priority: int = 0


class SubGraph(BaseModel):
    """A traversal result containing a subset of the graph."""

    nodes: list[NodeBase] = Field(default_factory=list)
    edges: list[EdgeBase] = Field(default_factory=list)
    root_id: UUID
    depth: int
