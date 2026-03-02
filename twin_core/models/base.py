"""Base models for all Digital Twin graph nodes and edges."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from twin_core.models.enums import EdgeType, NodeType


class NodeBase(BaseModel):
    """Abstract base for all graph nodes."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType


class EdgeBase(BaseModel):
    """A directed relationship between two graph nodes."""

    source_id: UUID
    target_id: UUID
    edge_type: EdgeType
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)
