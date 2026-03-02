"""Artifact node — any design output tracked in the Digital Twin."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import ArtifactType, NodeType


class Artifact(NodeBase):
    """A design artifact: schematic, BOM, PCB layout, firmware source, etc."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.ARTIFACT
    name: str
    type: ArtifactType
    domain: str
    file_path: str
    content_hash: str
    format: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str
