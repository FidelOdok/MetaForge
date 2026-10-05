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

FORGE-525: a write made inside a design-flow run is a *draft* revision in
that run's change set. It gets a REVISION_OF edge (``status: draft``) and an
entry in ``Item.drafts`` keyed by the run id, but HEAD does not move until the
run's gate approves. An item first written inside a run has no head at all
until then (``head_node_id`` is ``None``, ``head_revision`` 0).

Which twin types get an Item at all is decided by the type registry in
``twin_core/items/registry.py`` (FORGE-522): definitions do, records never do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
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
    #: ``None`` only for an item whose every revision is still a draft (FORGE-525).
    head_node_id: UUID | None = None
    #: Highest revision number ever handed out, drafts included (FORGE-525).
    #: New revisions are ``max(last_revision, head_revision) + 1`` so a closed
    #: draft's number is never reused.
    last_revision: int = 0
    #: Open change sets touching this item (FORGE-525), keyed by change set id
    #: (the run id). Each entry: ``revision``/``node_id`` of the run's latest
    #: draft, ``base_revision``/``base_node_id`` (the head when the run first
    #: drafted this item, for optimistic concurrency), ``phase``, ``name``.
    drafts: dict[str, dict[str, Any]] = Field(default_factory=dict)
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
    #: FORGE-525: ``committed`` (written outside any run), ``draft`` (in an
    #: open change set), ``approved`` (committed by a gate), ``rejected`` or
    #: ``abandoned`` (closed without reaching the head).
    status: str = "committed"
    #: The change set (run id) a draft was written in; ``None`` outside a run.
    change_set: str | None = None
    phase: str | None = None
    #: The gate that approved it.
    gate: str | None = None
    #: Why a draft was closed (rejected / abandoned), or a refused commit.
    status_reason: str | None = None
