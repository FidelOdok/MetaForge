"""Version node — a point-in-time snapshot of the artifact graph."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class Version(NodeBase):
    """A version in the Twin's Git-like branching history."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.VERSION
    branch_name: str
    parent_id: UUID | None = None
    merge_parent_id: UUID | None = None
    commit_message: str
    snapshot_hash: str
    author: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    artifact_ids: list[UUID] = Field(default_factory=list)


class ArtifactChange(BaseModel):
    """A single artifact change between two versions."""

    artifact_id: UUID
    change_type: str  # "added", "modified", "deleted"
    old_content_hash: str | None = None
    new_content_hash: str | None = None


class VersionDiff(BaseModel):
    """The diff between two versions."""

    version_a: UUID
    version_b: UUID
    changes: list[ArtifactChange]
    constraints_added: list[UUID] = Field(default_factory=list)
    constraints_removed: list[UUID] = Field(default_factory=list)
