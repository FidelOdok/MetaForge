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

Drafts (FORGE-525): a write whose call carries a design-flow ``run_id`` is
a *draft* in that run's change set. :func:`commit_revision` records it
(REVISION_OF with ``status: draft``, ``change_set`` = the run id, ``phase``)
and an entry in ``Item.drafts``, and does **not** move HEAD or add
SUPERSEDES. The run's own later writes and reads (:func:`resolve_item_ref`,
:func:`item_history` with ``run_id``) see its drafts over the head; every
other reader sees the head only. The gate decides what happens next, in
:mod:`twin_core.items.change_sets`: approve moves the heads, reject / retry /
rework closes the drafts. Writes with no run are unchanged: immediate head.

Revision numbers are ``max(last_revision, head_revision) + 1``, so a closed
draft's number is never handed out again and two runs drafting the same item
get distinct numbers.

Concurrent writes: two writes to one item can both plan ``n+1``.
:func:`commit_revision` re-reads the head before linking; a head write that
finds the head moved is renumbered to the next free number and supersedes the
actual head (``item_revision_race`` logs planned vs assigned), or, if it
pinned ``KEY@n``, is refused. The re-read and the write are not one
transaction, so this narrows the window rather than closing it. Run writes
are protected at the gate instead: the change set records each item's base
head and an approval whose base moved is refused (``ChangeSetConflictError``).
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
    #: FORGE-525: the design-flow phase that wrote it (drafts only).
    phase: str | None = None
    #: The item's head when this run first drafted it (drafts only). The
    #: approval is refused if the head moved away from it in the meantime.
    base_revision: int = 0
    base_node_id: UUID | None = None

    @property
    def draft(self) -> bool:
        """A write inside a run: a draft in the run's change set, not a new head."""
        return bool(self.run_id)

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
            # Born in this change set. Whether it reached the head is on its
            # REVISION_OF edge (status), never on the node.
            out["change_set"] = self.run_id
        if self.author:
            out["revision_author"] = self.author
        return out

    def result_fields(self) -> dict[str, Any]:
        """Fields every write tool adds to its result."""
        out: dict[str, Any] = {
            "item_key": self.key,
            "revision": self.revision,
            "item_ref": self.ref,
        }
        if self.draft:
            out["revision_status"] = "draft"
            out["draft_note"] = (
                f"{self.ref} is a draft in this run; it becomes the current revision "
                "when the run's gate is approved"
            )
        return out


def next_revision(item: Item) -> int:
    """The number the next revision of ``item`` gets, drafts included."""
    return max(item.last_revision, item.head_revision) + 1


def own_draft(item: Item, run_id: str | None) -> dict[str, Any] | None:
    """``run_id``'s open draft entry on ``item``, if any."""
    if not run_id:
        return None
    entry = item.drafts.get(run_id)
    return entry if isinstance(entry, dict) else None


def visible_head(item: Item, run_id: str | None = None) -> tuple[int, UUID] | None:
    """What a reader sees as the current revision: its run's draft, else the head.

    ``None`` when there is nothing to see (every revision is another run's draft).
    """
    entry = own_draft(item, run_id)
    if entry is not None and entry.get("node_id"):
        return int(entry.get("revision") or 0), UUID(str(entry["node_id"]))
    if item.head_node_id is None:
        return None
    return item.head_revision, item.head_node_id


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
    twin: Any,
    project_id: UUID | str | None = None,
    item_type: str | None = None,
    *,
    include_unheaded: bool = False,
) -> list[Item]:
    """Items, optionally scoped to a project and/or type, ordered by key.

    An item with no head yet (only drafts, FORGE-525) is left out unless
    ``include_unheaded``: outside its run there is nothing current to show.
    """
    pid = _to_uuid(project_id)
    filters: dict[str, Any] = {}
    if pid is not None:
        filters["project_id"] = pid
    if item_type:
        filters["item_type"] = item_type
    nodes = await twin.graph.list_nodes(NodeType.ITEM, filters=filters or None)
    items = [
        n for n in nodes if isinstance(n, Item) and (include_unheaded or n.head_node_id is not None)
    ]
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


async def revision_status(twin: Any, node_id: UUID) -> str | None:
    """The status of the revision ``node_id`` is (FORGE-525), ``None`` if it is not one."""
    edges = await twin.graph.get_edges(
        node_id, direction="outgoing", edge_type=EdgeType.REVISION_OF
    )
    for edge in edges:
        return str((edge.metadata or {}).get("status") or "committed")
    return None


async def is_unapproved_draft(twin: Any, node: Any) -> bool:
    """True for a node born in a run's change set that has not been approved.

    Cheap for every other node: only a node stamped ``change_set`` at creation
    costs an edge read. Open, rejected and abandoned drafts all answer True:
    none of them is part of the current design outside its run.
    """
    meta = getattr(node, "metadata", None) or {}
    if not meta.get("change_set"):
        return False
    node_id = getattr(node, "id", None)
    if not isinstance(node_id, UUID):
        return False
    return await revision_status(twin, node_id) != "approved"


async def _find_by_name(twin: Any, item_type: str, name: str, pid: UUID) -> Item | None:
    family = family_of(item_type)
    derived = {derive_key(t, name) for t in family}
    candidates = [
        i
        for i in await list_items(twin, project_id=pid, include_unheaded=True)
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
    phase: str | None = None,
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
            # FORGE-525: inside a run, the run's own draft is what it revises.
            mine = own_draft(item, run_id)
            seen = visible_head(item, run_id)
            seen_rev = seen[0] if seen is not None else 0
            if expected is not None and expected != seen_rev:
                raise ItemRevisionConflictError(
                    f"{item.key} is at @{seen_rev}, not @{expected}; "
                    f"re-read {item.key} and revise the current head"
                )
            plan = RevisionPlan(
                item_type=item_type,
                key=item.key,
                project_id=item.project_id if item.project_id is not None else pid,
                revision=next_revision(item),
                author=author,
                item=item,
                prior_node_id=seen[1] if seen is not None else None,
                change_reason=change_reason,
                run_id=run_id,
                resolved_by=resolved_by,
                phase=phase if run_id else None,
                expected_revision=expected,
            )
            if mine is not None:
                plan.base_revision = int(mine.get("base_revision") or 0)
                plan.base_node_id = _to_uuid(mine.get("base_node_id"))
            else:
                plan.base_revision = item.head_revision if item.head_node_id else 0
                plan.base_node_id = item.head_node_id
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
                phase=phase if run_id else None,
                # Adopted legacy nodes are already "current": the newest is the base.
                base_revision=len(adopt),
                base_node_id=adopt[-1] if adopt else None,
            )
        span.set_attribute("item.key", plan.key)
        span.set_attribute("item.revision", plan.revision)
        span.set_attribute("item.resolved_by", plan.resolved_by)
        span.set_attribute("item.draft", plan.draft)
        return plan


async def _rebase_on_race(twin: Any, plan: RevisionPlan, node_id: UUID, current: Item) -> None:
    """Another write moved ``current``'s head after ``plan`` was made (FORGE-523).

    A pinned write (``KEY@n``) is refused with :class:`ItemRevisionConflictError`,
    exactly as :func:`plan_revision` refuses a stale pin. Otherwise the write is
    renumbered onto the actual head: ``plan.revision`` becomes the next free
    number, ``plan.prior_node_id`` the actual head, the SUPERSEDES edge a
    recorder may already have added to the stale head is moved, and the node's
    own ``item_revision`` stamp is corrected (the node was created moments ago by
    this same write and nothing has read it as a revision yet).
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
    plan.revision = next_revision(current)
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

    Outside a run this creates or advances the item and moves HEAD. Inside a
    run (``plan.draft``, FORGE-525) it records a draft in the run's change set
    and leaves HEAD and SUPERSEDES alone; the gate decides the rest.

    If another write moved the item's head (or created the item) after
    ``plan`` was made, a head write is renumbered onto the actual head, or,
    for a pinned ``KEY@n`` write, refused with :class:`ItemRevisionConflictError`
    (see :func:`_rebase_on_race`). ``plan`` is updated in place either way, so
    the caller reports the revision actually assigned.

    Otherwise never raises: the node already exists, so failing the write here would
    invite a retry that creates a duplicate. A failure is logged as
    ``item_revision_failed`` and counted (``outcome="failed"``), and the
    caller gets ``None`` so its result can say the link is missing.
    """
    now = datetime.now(UTC)
    with tracer.start_as_current_span("twin.items.commit_revision") as span:
        span.set_attribute("item.type", plan.item_type)
        span.set_attribute("item.key", plan.key)
        span.set_attribute("item.planned_revision", plan.revision)
        span.set_attribute("item.draft", plan.draft)
        try:
            if plan.item is None:
                # Another write may have created this item since we planned.
                appeared = await find_item(twin, plan.key, plan.project_id)
                if appeared is not None:
                    await _adopt_appeared_item(twin, plan, node_id, appeared)
            if plan.draft:
                item = await _record_draft(twin, plan, node_id, name=name, now=now)
            else:
                item = await _record_head(
                    twin, plan, node_id, name=name, now=now, link_supersedes=link_supersedes
                )
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
            "item_revision_drafted" if plan.draft else "item_revision_created",
            item_key=plan.key,
            item_type=plan.item_type,
            revision=plan.revision,
            planned_revision=plan.planned_revision,
            node_id=str(node_id),
            project_id=str(plan.project_id) if plan.project_id else None,
            resolved_by=plan.resolved_by,
            adopted=len(plan.adopt_chain),
            run_id=plan.run_id,
            phase=plan.phase,
        )
        _collector().record_twin_item_revision(
            plan.item_type, "drafted" if plan.draft else "created", plan.resolved_by
        )
    if not plan.draft and plan.prior_node_id is not None and item is not None:
        # FORGE-527: records pinned to the replaced revision are now stale.
        from twin_core.consistency.record_pins import on_head_moved

        await on_head_moved(twin, item, plan.revision, node_id)
    return item


async def _adopt_appeared_item(
    twin: Any, plan: RevisionPlan, node_id: UUID, appeared: Item
) -> None:
    """The item ``plan`` meant to create was created by another write meanwhile.

    A head write joins it through the race path (renumbered, or refused when
    pinned). A draft simply joins it: its base becomes the item's head, so the
    gate's base check is against what is really there.
    """
    if not plan.draft:
        await _rebase_on_race(twin, plan, node_id, appeared)
        return
    mine = own_draft(appeared, plan.run_id)
    seen = visible_head(appeared, plan.run_id)
    plan.planned_revision = plan.revision
    plan.item = appeared
    plan.adopt_chain = []
    plan.revision = next_revision(appeared)
    plan.prior_node_id = seen[1] if seen is not None else None
    if mine is not None:
        plan.base_revision = int(mine.get("base_revision") or 0)
        plan.base_node_id = _to_uuid(mine.get("base_node_id"))
    else:
        plan.base_revision = appeared.head_revision if appeared.head_node_id else 0
        plan.base_node_id = appeared.head_node_id
    node = await twin.graph.get_node(node_id)
    meta = getattr(node, "metadata", None)
    if isinstance(meta, dict) and meta.get("item_revision") == plan.planned_revision:
        await twin.graph.update_node(
            node_id, {"metadata": {**meta, "item_revision": plan.revision}}
        )
    logger.warning(
        "item_revision_race",
        item_key=plan.key,
        node_id=str(node_id),
        planned=plan.planned_revision,
        assigned=plan.revision,
        draft=True,
    )


def _edge_meta(plan: RevisionPlan, now: datetime) -> dict[str, Any]:
    return {
        "revision": plan.revision,
        "change_reason": plan.change_reason,
        "run_id": plan.run_id,
        "author": plan.author,
        "created_at": now.isoformat(),
    }


async def _new_item(
    twin: Any,
    plan: RevisionPlan,
    *,
    name: str,
    now: datetime,
    head_revision: int,
    head_node_id: UUID | None,
    drafts: dict[str, dict[str, Any]] | None = None,
) -> Item:
    item = Item(
        key=plan.key,
        item_type=plan.item_type,
        name=name,
        project_id=plan.project_id,
        head_revision=head_revision,
        head_node_id=head_node_id,
        last_revision=plan.revision,
        drafts=drafts or {},
        created_at=now,
        updated_at=now,
        created_by=plan.author,
    )
    await twin.graph.add_node(item)
    for number, legacy_id in enumerate(plan.adopt_chain, start=1):
        await twin.add_edge(
            legacy_id,
            item.id,
            EdgeType.REVISION_OF,
            metadata={"revision": number, "adopted": True},
        )
    return item


async def _record_head(
    twin: Any,
    plan: RevisionPlan,
    node_id: UUID,
    *,
    name: str,
    now: datetime,
    link_supersedes: bool,
) -> Item:
    """A write outside any run: ``node_id`` becomes the head (FORGE-523)."""
    graph = twin.graph
    if plan.item is None:
        item = await _new_item(
            twin, plan, name=name, now=now, head_revision=plan.revision, head_node_id=node_id
        )
    else:
        fetched = await graph.get_node(plan.item.id)
        current = fetched if isinstance(fetched, Item) else plan.item
        if current.head_node_id != plan.prior_node_id:
            await _rebase_on_race(twin, plan, node_id, current)
        old_head = current.head_node_id
        last = current.last_revision
        updated = await graph.update_node(
            plan.item.id,
            {
                "head_revision": plan.revision,
                "head_node_id": node_id,
                "last_revision": max(last, plan.revision),
                "name": name,
                "item_type": plan.item_type,
                "updated_at": now,
            },
        )
        item = updated if isinstance(updated, Item) else current
        if old_head is not None:
            await twin.remove_edge(item.id, old_head, EdgeType.HEAD)
    await twin.add_edge(node_id, item.id, EdgeType.REVISION_OF, metadata=_edge_meta(plan, now))
    await twin.add_edge(item.id, node_id, EdgeType.HEAD)
    # A renumbered write's caller linked SUPERSEDES to the stale head (and
    # _rebase_on_race removed it), so link the actual head here.
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
    return item


async def _record_draft(
    twin: Any, plan: RevisionPlan, node_id: UUID, *, name: str, now: datetime
) -> Item:
    """A write inside run ``plan.run_id``: a draft in its change set (FORGE-525).

    HEAD stays where it is and no SUPERSEDES edge is added (that would mark the
    head replaced for every other reader). The edge remembers ``prior_node_id``
    so the approval can add SUPERSEDES when the draft really becomes current.
    """
    assert plan.run_id  # plan.draft
    entry: dict[str, Any] = {
        "revision": plan.revision,
        "node_id": str(node_id),
        "base_revision": plan.base_revision,
        "base_node_id": str(plan.base_node_id) if plan.base_node_id else None,
        "phase": plan.phase,
        "name": name,
        "created_at": now.isoformat(),
    }
    graph = twin.graph
    if plan.item is None:
        item = await _new_item(
            twin,
            plan,
            name=name,
            now=now,
            head_revision=plan.base_revision,
            head_node_id=plan.base_node_id,
            drafts={plan.run_id: entry},
        )
        if plan.base_node_id is not None:
            # Adopted legacy chain: its newest node is the current head.
            await twin.add_edge(item.id, plan.base_node_id, EdgeType.HEAD)
    else:
        current = await graph.get_node(plan.item.id)
        fresh = current if isinstance(current, Item) else plan.item
        drafts = dict(fresh.drafts)
        drafts[plan.run_id] = entry
        updated = await graph.update_node(
            plan.item.id,
            {
                "drafts": drafts,
                "last_revision": max(fresh.last_revision, fresh.head_revision, plan.revision),
                "updated_at": now,
            },
        )
        item = updated if isinstance(updated, Item) else fresh
    meta = _edge_meta(plan, now)
    meta.update(
        {
            "status": "draft",
            "change_set": plan.run_id,
            "phase": plan.phase,
            "base_revision": plan.base_revision,
            "prior_node_id": str(plan.prior_node_id) if plan.prior_node_id else None,
        }
    )
    await twin.add_edge(node_id, item.id, EdgeType.REVISION_OF, metadata=meta)
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


async def item_history(
    twin: Any, item: Item, *, run_id: str | None = None, include_drafts: bool = False
) -> list[ItemRevision]:
    """Every revision of ``item``, oldest first.

    FORGE-525: an *open* draft is listed only for its own run (``run_id``),
    or for everyone with ``include_drafts``; another run's work in progress is
    not history yet. Closed drafts (rejected, abandoned) are history and are
    always listed, with their status, so nothing a run did disappears.
    """
    with tracer.start_as_current_span("twin.items.history") as span:
        span.set_attribute("item.key", item.key)
        edges = await twin.graph.get_edges(
            item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
        )
        out: list[ItemRevision] = []
        hidden = 0
        for edge in edges:
            meta = edge.metadata or {}
            status = str(meta.get("status") or "committed")
            change_set = meta.get("change_set")
            if status == "draft" and not include_drafts and change_set != run_id:
                hidden += 1
                continue
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
                    status=status,
                    change_set=str(change_set) if change_set else None,
                    phase=meta.get("phase"),
                    gate=meta.get("gate"),
                    status_reason=meta.get("status_reason"),
                )
            )
        out.sort(key=lambda r: r.revision)
        span.set_attribute("item.revision_count", len(out))
        span.set_attribute("item.drafts_hidden", hidden)
        return out


async def resolve_item_ref(
    twin: Any,
    ref: str,
    project_id: UUID | str | None = None,
    *,
    run_id: str | None = None,
) -> tuple[Item, ItemRevision]:
    """``KEY`` -> the current revision; ``KEY@n`` -> revision ``n``. Reads only.

    "Current" is the head, or, for a read inside run ``run_id``, that run's own
    draft (FORGE-525). Another run's open drafts are never resolved.
    """
    key, revision = parse_item_ref(ref)
    item = await find_item(twin, key, project_id, any_project=True)
    if item is None:
        raise UnknownItemError(f"no item {key}")
    history = await item_history(twin, item, run_id=run_id)
    if revision is None:
        seen = visible_head(item, run_id)
        if seen is None:
            raise UnknownItemError(
                f"{key} has no current revision yet: its only revisions are drafts "
                "of a run whose gate has not approved them"
            )
        wanted = seen[0]
    else:
        wanted = revision
    for entry in history:
        if entry.revision == wanted:
            return item, entry
    raise UnknownItemError(f"{key} has no revision @{wanted} (head is @{item.head_revision})")
