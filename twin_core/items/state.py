"""Revision state for context readers: current, draft, rejected (FORGE-530).

The brief, the staleness scorer and the rework helper all need the same answer
to "which revision of this item is the one to show, and what happened to the
others". This module is that read, kept apart from ``service.py`` (which owns
how revisions are written and read for history).

Status is FORGE-525's, on the ``REVISION_OF`` edge (``status``):

- ``committed`` (written outside any run) and ``approved`` (committed by a
  gate) are both part of the baseline, read here as :data:`APPROVED`. A
  missing status is ``committed``, the same default ``item_history`` uses;
- ``draft``: written inside a run (``change_set`` = the run id), not part of
  the baseline until that run's gate approves it;
- ``rejected``: closed by a gate verdict, with ``status_reason``. Kept as a
  lesson ("already tried, failed because");
- ``abandoned``: closed because the run failed, was cancelled, or was sent
  back. History, but not a verdict, so not a lesson.

The current revision is the item's HEAD (FORGE-525 moves it only on approval),
falling back to the newest approved revision when HEAD is missing from the
history. Reads only. Nothing here writes to the twin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from twin_core.models.enums import EdgeType
from twin_core.models.item import Item

__all__ = [
    "ABANDONED",
    "APPROVED",
    "DRAFT",
    "REJECTED",
    "RevisionView",
    "current_revision",
    "revision_reason",
    "revision_run",
    "revision_status",
    "revision_views",
    "run_drafts",
]

DRAFT = "draft"
APPROVED = "approved"
REJECTED = "rejected"
ABANDONED = "abandoned"

#: FORGE-525 edge statuses -> the state a context reader cares about.
_STATUS = {
    "committed": APPROVED,
    "approved": APPROVED,
    "draft": DRAFT,
    "rejected": REJECTED,
    "abandoned": ABANDONED,
}


def revision_status(edge_meta: dict[str, Any] | None) -> str:
    """:data:`APPROVED`, :data:`DRAFT`, :data:`REJECTED` or :data:`ABANDONED`.

    A missing status is ``committed`` (approved). An unknown one is treated as
    a draft: something this reader does not understand must not be shown as
    the baseline.
    """
    raw = (edge_meta or {}).get("status")
    if not isinstance(raw, str) or not raw.strip():
        return APPROVED
    return _STATUS.get(raw.strip().lower(), DRAFT)


def revision_reason(edge_meta: dict[str, Any] | None) -> str | None:
    """Why a revision was closed (FORGE-525 ``status_reason``), if recorded."""
    value = (edge_meta or {}).get("status_reason")
    return value.strip() if isinstance(value, str) and value.strip() else None


def revision_run(edge_meta: dict[str, Any] | None) -> str | None:
    """The run (change set) a revision was written in, if any."""
    meta = edge_meta or {}
    raw = meta.get("change_set") or meta.get("run_id")
    return str(raw) if raw else None


@dataclass
class RevisionView:
    """One revision of an item as a context reader sees it."""

    item: Item
    revision: int
    node_id: UUID
    node: Any
    status: str = APPROVED
    run_id: str | None = None
    change_reason: str | None = None
    reason: str | None = None
    is_head: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.item.key}@{self.revision}"


async def revision_views(twin: Any, item: Item) -> list[RevisionView]:
    """Every revision of ``item`` with its status, oldest first (drafts included)."""
    edges = await twin.graph.get_edges(
        item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
    )
    out: list[RevisionView] = []
    for edge in edges:
        meta = dict(edge.metadata or {})
        node = await twin.graph.get_node(edge.source_id)
        node_meta = dict(getattr(node, "metadata", None) or {})
        out.append(
            RevisionView(
                item=item,
                revision=int(meta.get("revision") or node_meta.get("item_revision") or 0),
                node_id=edge.source_id,
                node=node,
                status=revision_status(meta),
                run_id=revision_run(meta),
                change_reason=meta.get("change_reason"),
                reason=revision_reason(meta),
                is_head=item.head_node_id is not None and edge.source_id == item.head_node_id,
                metadata=node_meta,
            )
        )
    out.sort(key=lambda r: r.revision)
    return out


def current_revision(views: list[RevisionView]) -> RevisionView | None:
    """The revision the baseline shows: the item's HEAD.

    Falls back to the newest approved revision when no view is the head (an
    adopted legacy chain, or a head edge that did not make it). ``None`` when
    the item has no approved revision at all (it exists only as drafts or
    closed drafts).
    """
    head = next((v for v in views if v.is_head and v.status == APPROVED), None)
    if head is not None:
        return head
    approved = [v for v in views if v.status == APPROVED]
    return approved[-1] if approved else None


def run_drafts(views: list[RevisionView], run_id: str | None) -> list[RevisionView]:
    """The open draft revisions ``run_id`` wrote, oldest first."""
    if not run_id:
        return []
    return [v for v in views if v.status == DRAFT and v.run_id == run_id]
