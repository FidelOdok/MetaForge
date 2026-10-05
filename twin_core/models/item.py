"""Item node: the stable identity of a versioned definition (FORGE-523).

An Item is what a person means by "the bracket" or "the payload requirement
set": one thing with a stable ``key`` that keeps its identity while its
content changes. Every write of that definition creates a new, immutable
revision node (the ``WorkProduct``/``EngineeringEntity``/``BOMItem`` the
recorder already builds), linked to the Item by ``EdgeType.REVISION_OF``.
The Item points at its current revision by ``EdgeType.HEAD``.

``head_revision``/``head_node_id`` are a denormalized mirror of the HEAD
edge, kept so a head read and ``GET /v1/twin/items`` need no edge scan (the
same mirror-of-an-edge precedent ``EngineeringEntity.parent_refs`` and
``Baseline.includes`` already set). The edges stay the source of truth for
history: ``twin_core.items.service.item_history`` reads REVISION_OF edges.

Which twin types get an Item at all is decided by the type registry in
``twin_core/items/registry.py`` (FORGE-522): definitions do, records never do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class Item(NodeBase):
    """A versioned definition with one current head revision."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.ITEM
    #: Stable, human-readable key, unique within a project (``CAD-BRACKET``).
    #: Referenced as ``KEY@n`` for one revision or bare ``KEY`` for the head.
    key: str
    #: The registry type name (``cad_model``, ``assembly``, ``intent``, ...).
    item_type: str
    #: Display name of the head revision (it may drift between revisions;
    #: the key never does).
    name: str
    head_revision: int = 1
    head_node_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = ""


class ItemRevision(BaseModel):
    """One entry of an item's history, read from its REVISION_OF edge."""

    revision: int
    node_id: UUID
    name: str | None = None
    change_reason: str | None = None
    run_id: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    is_head: bool = False
    #: True for a node that existed before its item did and was adopted into
    #: it (an old SUPERSEDES chain); its own properties carry no item stamp.
    adopted: bool = False
