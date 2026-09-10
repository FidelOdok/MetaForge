"""Version node — a point-in-time snapshot of the work_product graph."""

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
    work_product_ids: list[UUID] = Field(default_factory=list)
    # Set by GitVersionEngine — the real git commit backing this Version
    # node, so branch/merge/diff/log can delegate to actual git plumbing.
    # None for InMemoryVersionEngine (no git repo backs it).
    git_commit_sha: str | None = None


class WorkProductChange(BaseModel):
    """A single work_product change between two versions."""

    # Optional: GitVersionEngine can only recover a work_product_id when the
    # changed git path follows its default `work_products/<uuid>` layout.
    # A stable custom path (e.g. `mechanical/cad_src/bracket.py`, used so
    # regenerations with fresh ids still accumulate history — see
    # GitVersionEngine.commit's `paths` arg) has no single id across
    # commits, so `path` below is the reliable identifier in that case.
    work_product_id: UUID | None = None
    change_type: str  # "added", "modified", "deleted"
    old_content_hash: str | None = None
    new_content_hash: str | None = None
    # Real unified diff text, populated only when GitVersionEngine has
    # actual file content to diff (not just opaque content hashes).
    patch: str | None = None
    # The git-tracked path that changed. Only set by GitVersionEngine.
    path: str | None = None


class VersionDiff(BaseModel):
    """The diff between two versions."""

    version_a: UUID
    version_b: UUID
    changes: list[WorkProductChange]
    constraints_added: list[UUID] = Field(default_factory=list)
    constraints_removed: list[UUID] = Field(default_factory=list)
