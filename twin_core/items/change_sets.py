"""Run change sets: drafts until the gate, approval moves heads (FORGE-525).

Every definition write made inside a design-flow run is a draft revision in
that run's change set (see :func:`twin_core.items.service.commit_revision`).
The change set has no node of its own: it is the set of items whose
``Item.drafts`` carries an entry for the run, plus the ``REVISION_OF`` edges
stamped ``change_set=<run id>``. The gate decides what becomes of it:

* :func:`commit_change_set` (gate approve): every item's HEAD moves to the
  run's latest draft, the drafts' status becomes ``approved`` with the gate
  and the gate's reason as ``change_reason``, and SUPERSEDES links each draft
  to the revision it replaced. Optimistic concurrency (spec section 41): the
  change set recorded each item's head when the run first drafted it; if any
  of those heads moved since (another run's approval, or a direct write), the
  whole commit is refused with :class:`ChangeSetConflictError` before anything
  moves, and its message tells the gate how to rebase.
* :func:`close_change_set` (reject, retry, rework, a failed or canceled
  run): the drafts' status becomes ``rejected`` or ``abandoned`` with the
  reason; HEAD never moves; the revisions stay in the item's history.

Why not ``TransactionEngine`` (FORGE-50)? It applies a :class:`Patch` of
field edits to controlled entities. Here the revisions already exist as
immutable nodes and the commit is a head move, so there is nothing for a
patch to carry. What is shared is the ECT's discipline: check every
precondition first, then write, and say plainly that ``GraphEngine`` has no
multi-write transaction. A failure part-way through is compensated (the heads
already moved are put back) and reported, never left silent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.items.service import ItemError, _collector, _to_uuid, list_items
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.item import Item

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.items.change_sets")

#: Statuses a closed draft can take.
CLOSED_STATUSES = ("rejected", "abandoned")

_REASON_MAX = 1000

_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """One commit at a time per event loop, so two approvals in this process
    cannot both pass the base check before either moves a head."""
    loop_id = id(asyncio.get_running_loop())
    lock = _locks.get(loop_id)
    if lock is None:
        lock = _locks[loop_id] = asyncio.Lock()
    return lock


@dataclass
class ChangeSetConflict:
    """One item whose head moved after the run drafted it."""

    key: str
    base_revision: int
    head_revision: int
    draft_revision: int

    def describe(self) -> str:
        return (
            f"{self.key} was @{self.base_revision} when this run drafted "
            f"{self.key}@{self.draft_revision}, and is now @{self.head_revision}"
        )


class ChangeSetConflictError(ItemError):
    """The approval was refused: heads moved since the run drafted them (PATCH_CONFLICT)."""

    def __init__(self, change_set: str, conflicts: list[ChangeSetConflict]) -> None:
        self.change_set = change_set
        self.conflicts = conflicts
        detail = "; ".join(c.describe() for c in conflicts)
        super().__init__(
            "PATCH_CONFLICT: this run's work was drafted on revisions that have since "
            f"changed ({detail}). Nothing was committed. Retry the phase so it redoes "
            "its work on the current revisions (the retry is told what changed), or reject."
        )


class ChangeSetCommitError(ItemError):
    """The commit failed part-way; heads already moved were put back."""


@dataclass
class ChangeSetResult:
    """What a commit or close did."""

    change_set: str
    outcome: str
    #: One entry per item: key, revision, item_ref, base_revision.
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def refs(self) -> list[str]:
        return [str(i["item_ref"]) for i in self.items]


async def open_drafts(
    twin: Any, change_set: str, project_id: UUID | str | None = None
) -> list[Item]:
    """The items ``change_set`` has open drafts on, ordered by key."""
    if not change_set:
        return []
    items = await list_items(twin, project_id=project_id, include_unheaded=True)
    return [i for i in items if change_set in i.drafts]


async def check_change_set(
    twin: Any, change_set: str, project_id: UUID | str | None = None
) -> list[ChangeSetConflict]:
    """The items whose head moved since ``change_set`` first drafted them."""
    conflicts: list[ChangeSetConflict] = []
    for item in await open_drafts(twin, change_set, project_id):
        entry = item.drafts[change_set]
        # Node ids are never reused, so comparing them is the whole check. An
        # item first drafted by this run has no base (None) and no head yet.
        if item.head_node_id != _to_uuid(entry.get("base_node_id")):
            conflicts.append(
                ChangeSetConflict(
                    key=item.key,
                    base_revision=int(entry.get("base_revision") or 0),
                    head_revision=item.head_revision,
                    draft_revision=int(entry.get("revision") or 0),
                )
            )
    return conflicts


async def _draft_edges(twin: Any, item: Item, change_set: str) -> list[Any]:
    edges = await twin.graph.get_edges(
        item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
    )
    out = [
        e
        for e in edges
        if (e.metadata or {}).get("change_set") == change_set
        and (e.metadata or {}).get("status") == "draft"
    ]
    out.sort(key=lambda e: int((e.metadata or {}).get("revision") or 0))
    return out


async def _restamp(twin: Any, edge: Any, updates: dict[str, Any]) -> None:
    """Rewrite one REVISION_OF edge's metadata (edges have no update in GraphEngine)."""
    meta = {**(edge.metadata or {}), **updates}
    await twin.remove_edge(edge.source_id, edge.target_id, EdgeType.REVISION_OF)
    await twin.add_edge(edge.source_id, edge.target_id, EdgeType.REVISION_OF, metadata=meta)


def _without(drafts: dict[str, dict[str, Any]], change_set: str) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in drafts.items() if k != change_set}


async def _propagate_staleness(twin: Any, item: Item, replaced: UUID | None) -> None:
    """Spec section 21: what depended on the replaced head is now behind (best-effort)."""
    if replaced is None or item.project_id is None:
        return
    try:
        node = await twin.graph.get_node(replaced)
        if getattr(node, "node_type", None) != NodeType.WORK_PRODUCT:
            return
        from twin_core.consistency.staleness import StalenessEngine

        await StalenessEngine(twin).propagate(item.project_id, "work_product", replaced)
    except Exception as exc:  # noqa: BLE001 - staleness must never undo an approval
        logger.warning(
            "run_change_set_staleness_failed",
            item_key=item.key,
            node_id=str(replaced),
            error=str(exc),
        )


async def commit_change_set(
    twin: Any,
    change_set: str,
    *,
    project_id: UUID | str | None = None,
    gate: str | None = None,
    decided_by: str = "",
    reason: str = "",
) -> ChangeSetResult:
    """Gate approve: move every item's HEAD to ``change_set``'s drafts, atomically.

    Raises :class:`ChangeSetConflictError` (nothing written) when a base head
    moved, :class:`ChangeSetCommitError` when a write failed part-way (moved
    heads restored). A change set with no drafts commits nothing and succeeds,
    so a second approval of the same gate is harmless.
    """
    with tracer.start_as_current_span("twin.items.commit_change_set") as span:
        span.set_attribute("change_set.id", change_set)
        async with _lock():
            conflicts = await check_change_set(twin, change_set, project_id)
            if conflicts:
                span.set_attribute("change_set.outcome", "refused")
                logger.warning(
                    "run_change_set_refused",
                    change_set=change_set,
                    gate=gate,
                    conflicts=[c.describe() for c in conflicts],
                )
                _collector().record_twin_change_set("refused")
                raise ChangeSetConflictError(change_set, conflicts)
            items = await open_drafts(twin, change_set, project_id)
            if not items:
                span.set_attribute("change_set.outcome", "empty")
                return ChangeSetResult(change_set=change_set, outcome="empty")
            now = datetime.now(UTC).isoformat()
            note = (reason or "").strip()[:_REASON_MAX] or None
            moved: list[tuple[Item, dict[str, Any]]] = []
            result = ChangeSetResult(change_set=change_set, outcome="committed")
            try:
                for item in items:
                    entry = item.drafts[change_set]
                    new_head = UUID(str(entry["node_id"]))
                    for edge in await _draft_edges(twin, item, change_set):
                        meta = edge.metadata or {}
                        await _restamp(
                            twin,
                            edge,
                            {
                                "status": "approved",
                                "gate": gate,
                                "decided_by": decided_by or None,
                                "approved_at": now,
                                "draft_change_reason": meta.get("change_reason"),
                                "change_reason": note or meta.get("change_reason"),
                            },
                        )
                        prior = _to_uuid(meta.get("prior_node_id"))
                        if prior is not None and prior != edge.source_id:
                            await twin.add_edge(edge.source_id, prior, EdgeType.SUPERSEDES)
                    if item.head_node_id is not None:
                        await twin.remove_edge(item.id, item.head_node_id, EdgeType.HEAD)
                    await twin.add_edge(item.id, new_head, EdgeType.HEAD)
                    await twin.graph.update_node(
                        item.id,
                        {
                            "head_revision": int(entry.get("revision") or 0),
                            "head_node_id": new_head,
                            "name": str(entry.get("name") or item.name),
                            "drafts": _without(item.drafts, change_set),
                            "updated_at": datetime.now(UTC),
                        },
                    )
                    moved.append((item, entry))
                    revision = int(entry.get("revision") or 0)
                    result.items.append(
                        {
                            "key": item.key,
                            "revision": revision,
                            "item_ref": f"{item.key}@{revision}",
                            "base_revision": int(entry.get("base_revision") or 0),
                            "phase": entry.get("phase"),
                        }
                    )
            except Exception as exc:
                span.record_exception(exc)
                await _restore(twin, moved)
                logger.error(
                    "run_change_set_commit_failed",
                    change_set=change_set,
                    gate=gate,
                    moved_then_restored=[i.key for i, _ in moved],
                    error=str(exc),
                )
                _collector().record_twin_change_set("failed")
                raise ChangeSetCommitError(
                    f"committing this run's drafts failed ({exc}); no head was left moved. "
                    "Approve again to retry."
                ) from exc
        for item, entry in moved:
            await _propagate_staleness(twin, item, _to_uuid(entry.get("base_node_id")))
        span.set_attribute("change_set.outcome", "committed")
        span.set_attribute("change_set.items", len(result.items))
        logger.info(
            "run_change_set_committed",
            change_set=change_set,
            gate=gate,
            decided_by=decided_by,
            items=result.refs,
        )
        _collector().record_twin_change_set("committed")
        return result


async def _restore(twin: Any, moved: list[tuple[Item, dict[str, Any]]]) -> None:
    """Put back the heads a failed commit already moved (best-effort, logged)."""
    for item, entry in moved:
        try:
            new_head = UUID(str(entry["node_id"]))
            await twin.remove_edge(item.id, new_head, EdgeType.HEAD)
            if item.head_node_id is not None:
                await twin.add_edge(item.id, item.head_node_id, EdgeType.HEAD)
            await twin.graph.update_node(
                item.id,
                {
                    "head_revision": item.head_revision,
                    "head_node_id": item.head_node_id,
                    "name": item.name,
                    "drafts": item.drafts,
                },
            )
        except Exception as exc:  # noqa: BLE001 - keep restoring the rest
            logger.error("run_change_set_restore_failed", item_key=item.key, error=str(exc))


async def close_change_set(
    twin: Any,
    change_set: str,
    *,
    status: str,
    reason: str = "",
    project_id: UUID | str | None = None,
) -> ChangeSetResult:
    """Reject / retry / rework / failure: close ``change_set``'s drafts without a head move.

    ``status`` is ``rejected`` or ``abandoned``. The drafts stay in each item's
    history with that status and ``reason``. Idempotent.
    """
    if status not in CLOSED_STATUSES:
        raise ValueError(f"close_change_set: status must be one of {CLOSED_STATUSES}")
    with tracer.start_as_current_span("twin.items.close_change_set") as span:
        span.set_attribute("change_set.id", change_set)
        span.set_attribute("change_set.outcome", status)
        result = ChangeSetResult(change_set=change_set, outcome=status)
        now = datetime.now(UTC).isoformat()
        note = (reason or "").strip()[:_REASON_MAX] or None
        async with _lock():
            for item in await open_drafts(twin, change_set, project_id):
                entry = item.drafts[change_set]
                for edge in await _draft_edges(twin, item, change_set):
                    await _restamp(
                        twin, edge, {"status": status, "status_reason": note, "closed_at": now}
                    )
                await twin.graph.update_node(
                    item.id,
                    {"drafts": _without(item.drafts, change_set), "updated_at": datetime.now(UTC)},
                )
                revision = int(entry.get("revision") or 0)
                result.items.append(
                    {
                        "key": item.key,
                        "revision": revision,
                        "item_ref": f"{item.key}@{revision}",
                        "base_revision": int(entry.get("base_revision") or 0),
                        "phase": entry.get("phase"),
                    }
                )
        if result.items:
            logger.info(
                "run_change_set_closed",
                change_set=change_set,
                status=status,
                reason=note,
                items=result.refs,
            )
            _collector().record_twin_change_set(status)
        span.set_attribute("change_set.items", len(result.items))
        return result
