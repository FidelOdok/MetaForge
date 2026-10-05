"""Item identity and immutable revisions for definitions (FORGE-523).

A definition write is two calls around the recorder's own node creation:

1. :func:`plan_revision` (before any write) resolves which item the write
   belongs to and what its revision number will be, and returns a
   :class:`RevisionPlan`. Resolution order:

   a. an explicit ``item_key`` (``KEY`` for "revise the head", ``KEY@n`` to
      additionally assert the head is still ``n``),
   b. ``supersedes``, a node id: the item that node belongs to,
   c. otherwise, within the project, an item of the same type family whose
      head has the same name, or whose key the name derives to,
   d. otherwise, ``legacy_lookup`` (today's same-name SUPERSEDES behaviour
      in the geometry recorder): an existing node with no item is adopted,
      together with its SUPERSEDES chain, as revisions ``@1..@k``.

   Nothing found means a new item at revision 1. An unscoped write (no
   project) with neither ``item_key`` nor ``supersedes`` gets no item at
   all: with no project there is no identity to match on, the same rule the
   geometry recorder's SUPERSEDES chain always followed.

2. The recorder stamps ``plan.stamp()`` into the new node's metadata (so
   the node is born carrying its key and revision and is never edited
   afterwards), creates the node, then calls :func:`commit_revision`, which
   creates or advances the :class:`~twin_core.models.item.Item`, adds the
   ``REVISION_OF`` edge (revision number, change_reason, run_id, author in
   edge metadata), moves ``HEAD``, and adds ``SUPERSEDES`` to the previous
   head.

Callers never pass anything new for the normal path: no ``item_key`` and no
``supersedes`` falls through to (c)/(d), which is exactly today's same-name
behaviour, now extended to every definition type in the registry.

Edges plus properties: REVISION_OF/HEAD edges keep history traversable in
Cypher and let a legacy node join an item without touching its properties;
``Item.head_revision``/``head_node_id`` mirror HEAD so a head read is one
node fetch. ``item_history`` reads the edges, so the edges stay the source
of truth.

Concurrent writes: two writes to one item can both plan ``n+1``.
:func:`commit_revision` re-reads the head before linking; the write that
finds the head moved is renumbered to ``n+2`` and supersedes the actual head
(``item_revision_race`` logs planned vs assigned), or, if it pinned
``KEY@n``, is refused. The re-read and the write are not one transaction, so
this narrows the window rather than closing it; run-scoped change sets
(FORGE-525) are where writes get serialised.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.items.registry import classify, family_of, is_definition
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.item import Item, ItemRevision

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.items")

_KEY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,79}$")
_MAX_ADOPTED_CHAIN = 200
_KEY_SLUG_MAX = 48

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-items")
    return _metrics


class ItemError(ValueError):
    """An item reference could not be honoured. The message is caller-facing."""


class UnknownItemError(ItemError):
    """No item (or no such revision) for the given reference."""


class AmbiguousItemKeyError(ItemError):
    """A bare key matched items in more than one project."""


class ItemRevisionConflictError(ItemError):
    """``KEY@n`` named a revision that is no longer the head."""


LegacyLookup = Callable[[], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def parse_item_ref(ref: str) -> tuple[str, int | None]:
    """``"CAD-BRACKET@3"`` -> ``("CAD-BRACKET", 3)``; a bare key -> ``(key, None)``."""
    if not isinstance(ref, str) or not ref.strip():
        raise ItemError(
            "item_key must be a non-empty string such as 'CAD-BRACKET' or 'CAD-BRACKET@2'"
        )
    text = ref.strip()
    revision: int | None = None
    if "@" in text:
        text, _, raw_rev = text.rpartition("@")
        try:
            revision = int(raw_rev)
        except ValueError as exc:
            raise ItemError(
                f"item reference {ref!r}: revision after '@' must be an integer"
            ) from exc
        if revision < 1:
            raise ItemError(f"item reference {ref!r}: revisions start at 1")
    key = text.strip().upper()
    if not _KEY_RE.match(key):
        raise ItemError(
            f"item reference {ref!r}: a key is letters, digits, '-', '_' or '.', "
            "for example 'CAD-BRACKET'"
        )
    return key, revision


def derive_key(item_type: str, name: str) -> str:
    """The key a new item of ``item_type`` named ``name`` gets (before de-duplication)."""
    spec = classify(item_type)
    prefix = (spec.key_prefix if spec and spec.key_prefix else "ITEM").upper()
    slug = re.sub(r"[^A-Z0-9]+", "-", (name or "").upper()).strip("-")[:_KEY_SLUG_MAX].strip("-")
    return f"{prefix}-{slug or 'UNNAMED'}"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class RevisionPlan:
    """Where a definition write lands: which item, which revision."""

    item_type: str
    key: str
    project_id: UUID | None
    revision: int
    author: str
    item: Item | None = None
    prior_node_id: UUID | None = None
    #: Legacy nodes (oldest first) adopted as revisions 1..k of a new item.
    adopt_chain: list[UUID] = field(default_factory=list)
    change_reason: str | None = None
    run_id: str | None = None
    resolved_by: str = "new"
    #: The head the caller pinned with ``KEY@n``; a write that loses a race
    #: against another writer is then refused rather than renumbered.
    expected_revision: int | None = None
    #: Set by :func:`commit_revision` when another write moved the head first
    #: and this one was renumbered: the revision it had planned.
    planned_revision: int | None = None

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.revision}"

    def stamp(self) -> dict[str, Any]:
        """Metadata the revision node is created with (never edited later)."""
        out: dict[str, Any] = {
            "item_key": self.key,
            "item_revision": self.revision,
            "item_type": self.item_type,
        }
        if self.change_reason:
            out["change_reason"] = self.change_reason
        if self.run_id:
            out["run_id"] = self.run_id
        if self.author:
            out["revision_author"] = self.author
        return out

    def result_fields(self) -> dict[str, Any]:
        """Fields every write tool adds to its result."""
        return {"item_key": self.key, "revision": self.revision, "item_ref": self.ref}


def supports_items(twin: Any) -> bool:
    """True when ``twin`` exposes the graph operations items need.

    Real twins (``InMemoryTwinAPI`` over either graph engine) always do. A
    unit-test double that only implements ``create_work_product`` does not,
    and its writes simply carry no item.
    """
    graph = getattr(twin, "graph", None)
    return all(
        callable(getattr(graph, name, None))
        for name in ("add_node", "get_node", "update_node", "list_nodes", "get_edges")
    ) and all(callable(getattr(twin, name, None)) for name in ("add_edge", "remove_edge"))


def _to_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


async def _items_with_key(twin: Any, key: str) -> list[Item]:
    nodes = await twin.graph.list_nodes(NodeType.ITEM, filters={"key": key})
    return [n for n in nodes if isinstance(n, Item)]


async def find_item(
    twin: Any, key: str, project_id: UUID | str | None = None, *, any_project: bool = False
) -> Item | None:
    """The item with ``key`` in ``project_id``.

    ``any_project=True`` (reads only) searches every project when no
    ``project_id`` is given, and raises :class:`AmbiguousItemKeyError` if the
    key exists in more than one.
    """
    pid = _to_uuid(project_id)
    matches = await _items_with_key(twin, key)
    if pid is not None or not any_project:
        scoped = [m for m in matches if m.project_id == pid]
        return scoped[0] if scoped else None
    if len(matches) > 1:
        raise AmbiguousItemKeyError(
            f"item key {key!r} exists in {len(matches)} projects; pass project_id"
        )
    return matches[0] if matches else None


async def list_items(
    twin: Any, project_id: UUID | str | None = None, item_type: str | None = None
) -> list[Item]:
    """Items, optionally scoped to a project and/or type, ordered by key."""
    pid = _to_uuid(project_id)
    filters: dict[str, Any] = {}
    if pid is not None:
        filters["project_id"] = pid
    if item_type:
        filters["item_type"] = item_type
    nodes = await twin.graph.list_nodes(NodeType.ITEM, filters=filters or None)
    items = [n for n in nodes if isinstance(n, Item)]
    return sorted(items, key=lambda i: i.key)


async def item_for_node(twin: Any, node_id: UUID) -> tuple[Item, int] | None:
    """The item ``node_id`` is a revision of, and its revision number."""
    edges = await twin.graph.get_edges(
        node_id, direction="outgoing", edge_type=EdgeType.REVISION_OF
    )
    for edge in edges:
        item = await twin.graph.get_node(edge.target_id)
        if isinstance(item, Item):
            return item, int((edge.metadata or {}).get("revision") or 0)
    return None


async def _find_by_name(twin: Any, item_type: str, name: str, pid: UUID) -> Item | None:
    family = family_of(item_type)
    derived = {derive_key(t, name) for t in family}
    candidates = [
        i
        for i in await list_items(twin, project_id=pid)
        if i.item_type in family and (i.name == name or i.key in derived)
    ]
    if not candidates:
        return None
    # An exact name match beats a slug match; the most recently moved head wins a tie.
    candidates.sort(key=lambda i: (i.name != name, -i.updated_at.timestamp()))
    return candidates[0]


async def _legacy_chain(twin: Any, head_id: UUID) -> list[UUID]:
    """``head_id`` and its SUPERSEDES predecessors that have no item, oldest first."""
    chain = [head_id]
    seen = {head_id}
    current = head_id
    while len(chain) < _MAX_ADOPTED_CHAIN:
        edges = await twin.graph.get_edges(
            current, direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        if not edges:
            break
        nxt = edges[0].target_id
        if nxt in seen or await item_for_node(twin, nxt) is not None:
            break
        chain.append(nxt)
        seen.add(nxt)
        current = nxt
    chain.reverse()
    return chain


async def _unique_key(twin: Any, base: str, pid: UUID | None) -> str:
    key = base
    n = 2
    while await find_item(twin, key, pid) is not None:
        key = f"{base}-{n}"
        n += 1
    return key


async def plan_revision(
    twin: Any,
    *,
    item_type: str,
    name: str,
    project_id: UUID | str | None,
    author: str,
    item_key: str | None = None,
    supersedes: str | UUID | None = None,
    change_reason: str | None = None,
    run_id: str | None = None,
    legacy_lookup: LegacyLookup | None = None,
) -> RevisionPlan | None:
    """Resolve the item a definition write belongs to. See the module docstring.

    Returns ``None`` when ``item_type`` is not a definition or ``twin`` cannot
    hold items (see :func:`supports_items`). Raises :class:`ItemError` for a
    reference that cannot be honoured, before anything is written.
    """
    if not is_definition(item_type) or not supports_items(twin):
        return None
    pid = _to_uuid(project_id)
    if pid is None and not item_key and not supersedes:
        return None
    with tracer.start_as_current_span("twin.items.plan_revision") as span:
        span.set_attribute("item.type", item_type)
        item: Item | None = None
        key: str | None = None
        expected: int | None = None
        adopt: list[UUID] = []
        resolved_by = "new"

        if item_key:
            key, expected = parse_item_ref(item_key)
            item = await find_item(twin, key, pid)
            if item is None and expected is not None:
                raise UnknownItemError(
                    f"no item {key} in this project, so there is no {key}@{expected} to revise. "
                    f"Pass the bare key '{key}' to start a new item with that key."
                )
            resolved_by = "item_key" if item is not None else "item_key_new"
        elif supersedes:
            try:
                prior_id = _to_uuid(supersedes)
            except ValueError as exc:
                raise ItemError(f"supersedes {supersedes!r} is not a node id (UUID)") from exc
            if prior_id is None:
                raise ItemError("supersedes must be a node id (UUID)")
            found = await item_for_node(twin, prior_id)
            if found is not None:
                item = found[0]
                resolved_by = "supersedes"
            else:
                if await twin.graph.get_node(prior_id) is None:
                    raise UnknownItemError(f"supersedes {prior_id}: no such node in the twin")
                adopt = await _legacy_chain(twin, prior_id)
                resolved_by = "supersedes_adopted"
        elif pid is not None:
            item = await _find_by_name(twin, item_type, name, pid)
            if item is not None:
                resolved_by = "name"
            elif legacy_lookup is not None:
                legacy = await legacy_lookup()
                legacy_id = getattr(legacy, "id", None)
                if isinstance(legacy_id, UUID):
                    found = await item_for_node(twin, legacy_id)
                    if found is not None:
                        item, resolved_by = found[0], "name"
                    else:
                        adopt = await _legacy_chain(twin, legacy_id)
                        resolved_by = "name_adopted"

        if item is not None:
            if item.item_type not in family_of(item_type):
                raise ItemError(
                    f"{item.key} is a {item.item_type} item; it cannot take a {item_type} revision"
                )
            if pid is not None and item.project_id is not None and item.project_id != pid:
                raise ItemError(f"{item.key} belongs to a different project")
            if expected is not None and expected != item.head_revision:
                raise ItemRevisionConflictError(
                    f"{item.key} is at @{item.head_revision}, not @{expected}; "
                    f"re-read {item.key} and revise the current head"
                )
            plan = RevisionPlan(
                item_type=item_type,
                key=item.key,
                project_id=item.project_id if item.project_id is not None else pid,
                revision=item.head_revision + 1,
                author=author,
                item=item,
                prior_node_id=item.head_node_id,
                change_reason=change_reason,
                run_id=run_id,
                resolved_by=resolved_by,
                expected_revision=expected,
            )
        else:
            if key is None:
                key = await _unique_key(twin, derive_key(item_type, name), pid)
            plan = RevisionPlan(
                item_type=item_type,
                key=key,
                project_id=pid,
                revision=len(adopt) + 1,
                author=author,
                prior_node_id=adopt[-1] if adopt else None,
                adopt_chain=adopt,
                change_reason=change_reason,
                run_id=run_id,
                resolved_by=resolved_by,
            )
        span.set_attribute("item.key", plan.key)
        span.set_attribute("item.revision", plan.revision)
        span.set_attribute("item.resolved_by", plan.resolved_by)
        return plan


async def _rebase_on_race(twin: Any, plan: RevisionPlan, node_id: UUID, current: Item) -> None:
    """Another write moved ``current``'s head after ``plan`` was made.

    A pinned write (``KEY@n``) is refused with :class:`ItemRevisionConflictError`,
    exactly as :func:`plan_revision` refuses a stale pin. Otherwise the write is
    renumbered onto the actual head: ``plan.revision`` becomes
    ``head_revision + 1``, ``plan.prior_node_id`` the actual head, the
    SUPERSEDES edge a recorder may already have added to the stale head is
    moved, and the node's own ``item_revision`` stamp is corrected (the node was
    created moments ago by this same write and nothing has read it as a
    revision yet).
    """
    if plan.expected_revision is not None:
        logger.warning(
            "item_revision_conflict",
            item_key=plan.key,
            pinned=plan.expected_revision,
            head=current.head_revision,
            node_id=str(node_id),
        )
        _collector().record_twin_item_revision(plan.item_type, "refused", plan.resolved_by)
        raise ItemRevisionConflictError(
            f"{plan.key} moved to @{current.head_revision} while this write was in progress, "
            f"so it is no longer at @{plan.expected_revision}. Node {node_id} was saved but is "
            f"not a revision of {plan.key}; re-read {plan.key} and revise the current head"
        )
    planned, stale_prior = plan.revision, plan.prior_node_id
    plan.planned_revision = planned
    plan.revision = current.head_revision + 1
    plan.prior_node_id = current.head_node_id
    plan.item = current
    plan.adopt_chain = []
    if stale_prior is not None and stale_prior != plan.prior_node_id:
        await twin.remove_edge(node_id, stale_prior, EdgeType.SUPERSEDES)
    node = await twin.graph.get_node(node_id)
    meta = getattr(node, "metadata", None)
    if isinstance(meta, dict) and meta.get("item_revision") == planned:
        await twin.graph.update_node(
            node_id, {"metadata": {**meta, "item_revision": plan.revision}}
        )
    logger.warning(
        "item_revision_race",
        item_key=plan.key,
        node_id=str(node_id),
        planned=planned,
        assigned=plan.revision,
        supersedes=str(plan.prior_node_id),
    )


async def commit_revision(
    twin: Any,
    plan: RevisionPlan,
    node_id: UUID,
    *,
    name: str,
    link_supersedes: bool = True,
) -> Item | None:
    """Attach the just-created ``node_id`` to its item as ``plan.revision``.

    If another write moved the item's head (or created the item) after
    ``plan`` was made, the write is renumbered onto the actual head, or, for a
    pinned ``KEY@n`` write, refused with :class:`ItemRevisionConflictError`
    (see :func:`_rebase_on_race`). ``plan`` is updated in place either way, so
    the caller reports the revision actually assigned.

    Otherwise never raises: the node already exists, so failing the write here
    would invite a retry that creates a duplicate. A failure is logged as
    ``item_revision_failed`` and counted (``outcome="failed"``), and the
    caller gets ``None`` so its result can say the link is missing.

    The check-then-write is not atomic across processes; run-scoped change
    sets (FORGE-525) are where writes to one item get serialised.
    """
    now = datetime.now(UTC)
    with tracer.start_as_current_span("twin.items.commit_revision") as span:
        span.set_attribute("item.type", plan.item_type)
        span.set_attribute("item.key", plan.key)
        span.set_attribute("item.planned_revision", plan.revision)
        try:
            graph = twin.graph
            target = plan.item
            if target is None:
                # Another write may have created this item since we planned.
                target = await find_item(twin, plan.key, plan.project_id)
            if target is None:
                item = Item(
                    key=plan.key,
                    item_type=plan.item_type,
                    name=name,
                    project_id=plan.project_id,
                    head_revision=plan.revision,
                    head_node_id=node_id,
                    created_at=now,
                    updated_at=now,
                    created_by=plan.author,
                )
                await graph.add_node(item)
                for number, legacy_id in enumerate(plan.adopt_chain, start=1):
                    await twin.add_edge(
                        legacy_id,
                        item.id,
                        EdgeType.REVISION_OF,
                        metadata={"revision": number, "adopted": True},
                    )
            else:
                current = await graph.get_node(target.id)
                if not isinstance(current, Item):
                    current = target
                if plan.item is None or current.head_node_id != plan.prior_node_id:
                    await _rebase_on_race(twin, plan, node_id, current)
                updated = await graph.update_node(
                    current.id,
                    {
                        "head_revision": plan.revision,
                        "head_node_id": node_id,
                        "name": name,
                        "item_type": plan.item_type,
                        "updated_at": now,
                    },
                )
                item = updated if isinstance(updated, Item) else current
                await twin.remove_edge(item.id, current.head_node_id, EdgeType.HEAD)
            await twin.add_edge(
                node_id,
                item.id,
                EdgeType.REVISION_OF,
                metadata={
                    "revision": plan.revision,
                    "change_reason": plan.change_reason,
                    "run_id": plan.run_id,
                    "author": plan.author,
                    "created_at": now.isoformat(),
                },
            )
            await twin.add_edge(item.id, node_id, EdgeType.HEAD)
            # A renumbered write's caller linked SUPERSEDES to the stale head
            # (and _rebase_on_race removed it), so link the actual head here.
            if (
                (link_supersedes or plan.planned_revision is not None)
                and plan.prior_node_id is not None
                and plan.prior_node_id != node_id
            ):
                existing = await graph.get_edges(
                    node_id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
                )
                if all(e.target_id != plan.prior_node_id for e in existing):
                    await twin.add_edge(node_id, plan.prior_node_id, EdgeType.SUPERSEDES)
        except ItemRevisionConflictError:
            raise
        except Exception as exc:  # noqa: BLE001 -- see docstring
            span.record_exception(exc)
            logger.error(
                "item_revision_failed",
                item_key=plan.key,
                item_type=plan.item_type,
                revision=plan.revision,
                node_id=str(node_id),
                error=str(exc),
                consequence="node written without its item link; it looks like a new sibling",
            )
            _collector().record_twin_item_revision(plan.item_type, "failed", plan.resolved_by)
            return None
        span.set_attribute("item.revision", plan.revision)
        logger.info(
            "item_revision_created",
            item_key=plan.key,
            item_type=plan.item_type,
            revision=plan.revision,
            planned_revision=plan.planned_revision,
            node_id=str(node_id),
            project_id=str(plan.project_id) if plan.project_id else None,
            resolved_by=plan.resolved_by,
            adopted=len(plan.adopt_chain),
            run_id=plan.run_id,
        )
        _collector().record_twin_item_revision(plan.item_type, "created", plan.resolved_by)
        return item


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _node_name(node: Any) -> str | None:
    for attr in ("name", "title", "part_number", "statement"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


async def item_history(twin: Any, item: Item) -> list[ItemRevision]:
    """Every revision of ``item``, oldest first."""
    with tracer.start_as_current_span("twin.items.history") as span:
        span.set_attribute("item.key", item.key)
        edges = await twin.graph.get_edges(
            item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
        )
        out: list[ItemRevision] = []
        for edge in edges:
            meta = edge.metadata or {}
            node = await twin.graph.get_node(edge.source_id)
            created = meta.get("created_at")
            created_at: datetime | None
            if isinstance(created, str):
                created_at = datetime.fromisoformat(created)
            else:
                created_at = getattr(node, "created_at", None) or edge.created_at
            out.append(
                ItemRevision(
                    revision=int(meta.get("revision") or 0),
                    node_id=edge.source_id,
                    name=_node_name(node),
                    change_reason=meta.get("change_reason"),
                    run_id=meta.get("run_id"),
                    author=meta.get("author"),
                    created_at=created_at,
                    is_head=edge.source_id == item.head_node_id,
                    adopted=bool(meta.get("adopted")),
                )
            )
        out.sort(key=lambda r: r.revision)
        span.set_attribute("item.revision_count", len(out))
        return out


async def resolve_item_ref(
    twin: Any, ref: str, project_id: UUID | str | None = None
) -> tuple[Item, ItemRevision]:
    """``KEY`` -> the head revision; ``KEY@n`` -> revision ``n``. Reads only."""
    key, revision = parse_item_ref(ref)
    item = await find_item(twin, key, project_id, any_project=True)
    if item is None:
        raise UnknownItemError(f"no item {key}")
    history = await item_history(twin, item)
    wanted = item.head_revision if revision is None else revision
    for entry in history:
        if entry.revision == wanted:
            return item, entry
    raise UnknownItemError(f"{key} has no revision @{wanted} (head is @{item.head_revision})")
