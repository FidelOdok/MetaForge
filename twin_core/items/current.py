"""The current view of a project: one row per item at its current revision (FORGE-526).

FORGE-523 gave every definition an item with immutable revisions. This module
answers "what is the project right now" from those items, so a reader never
has to wade through every node a run ever wrote:

- :func:`revision_states` reads one item's revisions with their status, from
  the ``REVISION_OF`` edge (FORGE-525): ``committed`` (written outside a run),
  ``approved`` (committed by a gate), ``draft`` (in an open run change set),
  ``rejected`` or ``abandoned`` (closed without reaching the head). A missing
  status is ``committed``.
- The **current** revision of an item is its head (FORGE-525 moves HEAD only
  on a gate approval or a write outside a run). Open drafts belong to a run
  and are shown only on request (the dashboard's Working toggle); an item
  whose every revision is still a draft has no current revision at all.
- :func:`build_current_view` assembles the project page: items grouped by
  type, records (decisions, simulation results, evidence) listed as they are,
  with an ``out_of_date`` flag on a record whose analysed revision is no longer
  current, and counts and readiness computed over current items only.

Which revisions count as current follows ``twin_core.items.state``
(FORGE-530), the read the project brief and staleness scorer use, so the
dashboard, the brief and a baseline always agree: ``committed`` and
``approved`` are current, anything else (including a status this code does
not know) is not. This module keeps the raw status, gate and author for
display, which the brief does not need.

Read-only: nothing here writes to the twin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.items.registry import classify, is_definition
from twin_core.items.service import list_items
from twin_core.items.state import APPROVED
from twin_core.items.state import revision_status as _classify
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.item import Item

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.items.current")


#: Edges that are item bookkeeping, not a record's reference to what it analysed.
STRUCTURAL_EDGES = frozenset(
    {
        EdgeType.REVISION_OF,
        EdgeType.HEAD,
        EdgeType.SUPERSEDES,
        EdgeType.INCLUDED_IN_BASELINE,
        EdgeType.PRODUCED_BY,
        EdgeType.VERSIONED_BY,
    }
)

#: Metadata fields a record uses to name the node it was produced against.
_ANALYSED_META_KEYS = (
    "analysed_geometry_node_id",
    "source_cad_model_id",
    "analysed_node_id",
    "work_product_id",
    "source_work_product_id",
    "target_node_id",
)

#: FORGE-527 record statuses that mean "no longer about the current design".
_OUT_OF_DATE_STALENESS = frozenset({"stale", "invalid", "superseded"})

#: Record types the current view lists, in display order.
RECORD_TYPES = ("design_decision", "simulation_result", "evidence")


def revision_status(edge_metadata: dict[str, Any] | None) -> str:
    """The revision's status from its ``REVISION_OF`` edge; missing is ``committed``."""
    status = (edge_metadata or {}).get("status")
    return str(status).lower() if status else "committed"


def is_current_status(status: str) -> bool:
    """``committed`` and ``approved`` are current; see ``twin_core.items.state``."""
    return _classify({"status": status}) == APPROVED


@dataclass
class RevisionState:
    """One revision of an item, with the status the current view needs."""

    revision: int
    node_id: UUID
    status: str
    name: str | None = None
    run_id: str | None = None
    gate_id: str | None = None
    author: str | None = None
    change_reason: str | None = None
    created_at: datetime | None = None
    adopted: bool = False
    change_set: str | None = None


def _node_name(node: Any) -> str | None:
    for attr in ("name", "title", "part_number", "statement"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


async def revision_states(twin: Any, item: Item) -> list[RevisionState]:
    """Every revision of ``item`` with its status, oldest first."""
    edges = await twin.graph.get_edges(
        item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
    )
    out: list[RevisionState] = []
    for edge in edges:
        meta = edge.metadata or {}
        node = await twin.graph.get_node(edge.source_id)
        node_meta = getattr(node, "metadata", None) or {}
        out.append(
            RevisionState(
                revision=int(meta.get("revision") or 0),
                node_id=edge.source_id,
                status=revision_status(meta),
                name=_node_name(node),
                run_id=meta.get("run_id") or node_meta.get("run_id"),
                gate_id=meta.get("gate") or meta.get("gate_id"),
                change_set=meta.get("change_set"),
                author=meta.get("author"),
                change_reason=meta.get("change_reason"),
                created_at=_parse_time(meta.get("created_at"))
                or getattr(node, "created_at", None)
                or edge.created_at,
                adopted=bool(meta.get("adopted")),
            )
        )
    out.sort(key=lambda r: r.revision)
    return out


def current_revision(
    states: list[RevisionState], head_node_id: UUID | None = None
) -> RevisionState | None:
    """The head revision, else the highest committed or approved one.

    ``None`` when every revision is a draft or was closed.
    """
    if head_node_id is not None:
        for state in states:
            if state.node_id == head_node_id and is_current_status(state.status):
                return state
    for state in reversed(states):
        if is_current_status(state.status):
            return state
    return None


@dataclass
class CurrentItem:
    """One item as the current view shows it."""

    item: Item
    current: RevisionState | None
    revisions: list[RevisionState]

    @property
    def drafts(self) -> list[RevisionState]:
        floor = self.current.revision if self.current else 0
        return [r for r in self.revisions if r.revision > floor and r.status == "draft"]


async def current_items(
    twin: Any, project_id: UUID | str | None, item_type: str | None = None
) -> list[CurrentItem]:
    """Every item of the project with its current revision and full history.

    Items whose every revision is still a draft are included (``current`` is
    ``None``) so the Working view can show them.
    """
    out: list[CurrentItem] = []
    items = await list_items(
        twin, project_id=project_id, item_type=item_type, include_unheaded=True
    )
    for item in items:
        states = await revision_states(twin, item)
        out.append(
            CurrentItem(
                item=item,
                current=current_revision(states, item.head_node_id),
                revisions=states,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Records pinned to revisions
# ---------------------------------------------------------------------------


@dataclass
class RecordRow:
    """A decision, simulation result or evidence record, and what it analysed."""

    node_id: UUID
    record_type: str
    name: str
    created_at: datetime | None
    analysed: list[dict[str, Any]] = field(default_factory=list)
    out_of_date: bool = False
    staleness: str | None = None


def record_type_of(node: Any) -> str | None:
    node_type = getattr(node, "node_type", None)
    if node_type == NodeType.WORK_PRODUCT:
        wp_type = str(getattr(getattr(node, "type", None), "value", getattr(node, "type", "")))
        return wp_type if wp_type in ("design_decision", "simulation_result") else None
    if node_type == NodeType.ENGINEERING_ENTITY and getattr(node, "entity_type", "") == "evidence":
        return "evidence"
    return None


async def analysed_node_ids(twin: Any, node: Any) -> set[UUID]:
    ids: set[UUID] = set()
    meta = getattr(node, "metadata", None) or {}
    # FORGE-532: a simulation_result pins the geometry it analysed as
    # ``analysed_geometry`` ({node_id, revision, content_hash}).
    pinned = meta.get("analysed_geometry")
    candidates = [pinned.get("node_id")] if isinstance(pinned, dict) else []
    # FORGE-527: a record's KEY@n pins (also DEPENDS_ON kind=revision_pin edges).
    for pin in meta.get("depends_on_items") or []:
        if isinstance(pin, dict):
            candidates.append(pin.get("node_id"))
    for key in _ANALYSED_META_KEYS:
        candidates.append(meta.get(key))
    for raw in candidates:
        if raw:
            try:
                ids.add(UUID(str(raw)))
            except ValueError:
                continue
    for edge in await twin.graph.get_edges(node.id, direction="outgoing"):
        if edge.edge_type not in STRUCTURAL_EDGES:
            ids.add(edge.target_id)
    return ids


async def project_records(twin: Any, pid: UUID | None) -> list[Any]:
    from twin_core.models.enums import WorkProductType

    nodes: dict[UUID, Any] = {}
    if pid is None:
        return []
    for wp_type in (WorkProductType.DESIGN_DECISION, WorkProductType.SIMULATION_RESULT):
        try:
            for wp in await twin.list_work_products(work_product_type=wp_type, project_id=pid):
                nodes[wp.id] = wp
        except Exception as exc:  # noqa: BLE001 -- a missing list API is an empty list
            logger.warning("current_view_records_unavailable", wp_type=str(wp_type), error=str(exc))
    list_entities = getattr(twin, "list_engineering_entities", None)
    if callable(list_entities):
        for entity in await list_entities(project_id=pid, entity_type="evidence"):
            nodes[entity.id] = entity
    return list(nodes.values())


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


@dataclass
class CurrentView:
    project_id: UUID | None
    items: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    records: list[dict[str, Any]]
    other: list[dict[str, Any]]
    counts: dict[str, Any]
    readiness: int


def _state_dict(state: RevisionState) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "node_id": str(state.node_id),
        "status": state.status,
        "name": state.name,
        "run_id": state.run_id,
        "gate_id": state.gate_id,
        "author": state.author,
        "change_reason": state.change_reason,
        "created_at": state.created_at.isoformat() if state.created_at else None,
        "adopted": state.adopted,
        "change_set": state.change_set,
    }


async def build_current_view(
    twin: Any,
    project_id: UUID | str | None,
    *,
    project_work_products: list[dict[str, Any]] | None = None,
    baselines: list[Any] | None = None,
) -> CurrentView:
    """The project page's default view. See the module docstring.

    ``project_work_products`` are the project's linked work products (id,
    name, type, status, updated_at) from the project store: they supply each
    current revision's validation status (``valid``/``warning``/``error``)
    and the unclassified work products the view still lists under ``other``.
    ``baselines`` (the project's baselines) attribute each current revision
    to the gate whose baseline first pinned it, when the revision itself does
    not carry a ``gate_id``.
    """
    pid = UUID(str(project_id)) if project_id else None
    with tracer.start_as_current_span("twin.items.current_view") as span:
        span.set_attribute("project.id", str(pid) if pid else "")
        linked = {str(w["id"]): w for w in (project_work_products or []) if w.get("id")}

        entries = await current_items(twin, pid)
        # node id -> (key, revision, is_current) for every revision of every item.
        revision_of: dict[UUID, tuple[str, int, bool]] = {}
        for entry in entries:
            current_rev = entry.current.revision if entry.current else None
            for state in entry.revisions:
                revision_of[state.node_id] = (
                    entry.item.key,
                    state.revision,
                    state.revision == current_rev,
                )

        gate_for_ref: dict[str, str] = {}
        for baseline in sorted(baselines or [], key=lambda b: b.created_at):
            if not getattr(baseline, "gate_id", None):
                continue
            for pin in getattr(baseline, "items", []):
                gate_for_ref.setdefault(pin.ref, baseline.gate_id)

        # Records, and which item revisions each one analysed.
        records: list[RecordRow] = []
        evidence_by_item: dict[str, dict[str, int]] = {}
        for node in await project_records(twin, pid):
            rtype = record_type_of(node)
            if rtype is None:
                continue
            analysed: list[dict[str, Any]] = []
            stale_keys: set[str] = set()
            fresh_keys: set[str] = set()
            for target in await analysed_node_ids(twin, node):
                pin = revision_of.get(target)
                if pin is None:
                    continue
                key, rev, is_current = pin
                analysed.append(
                    {"key": key, "revision": rev, "ref": f"{key}@{rev}", "current": is_current}
                )
                (fresh_keys if is_current else stale_keys).add(key)
            out_of_date = bool(stale_keys - fresh_keys)
            # FORGE-527's recorded status wins when it is set: a re-check
            # (revalidated) makes an old pin current again; stale, invalid
            # (pinned to a rejected draft) and superseded are out of date.
            staleness = str((getattr(node, "metadata", None) or {}).get("staleness") or "") or None
            if staleness == "revalidated":
                out_of_date = False
                stale_keys = set()
                fresh_keys |= {a["key"] for a in analysed}
            elif staleness in _OUT_OF_DATE_STALENESS:
                out_of_date = True
                stale_keys |= {a["key"] for a in analysed} - fresh_keys
            if rtype != "design_decision":
                for key in fresh_keys:
                    evidence_by_item.setdefault(key, {"current": 0, "out_of_date": 0})[
                        "current"
                    ] += 1
                for key in stale_keys - fresh_keys:
                    evidence_by_item.setdefault(key, {"current": 0, "out_of_date": 0})[
                        "out_of_date"
                    ] += 1
            records.append(
                RecordRow(
                    node_id=node.id,
                    record_type=rtype,
                    name=_node_name(node) or rtype,
                    created_at=getattr(node, "created_at", None),
                    analysed=sorted(analysed, key=lambda a: a["ref"]),
                    out_of_date=out_of_date and rtype != "design_decision",
                    staleness=staleness,
                )
            )

        rows: list[dict[str, Any]] = []
        for entry in entries:
            item, cur = entry.item, entry.current
            evidence = evidence_by_item.get(item.key, {"current": 0, "out_of_date": 0})
            if evidence["current"]:
                evidence_state = "current"
            elif evidence["out_of_date"]:
                evidence_state = "out_of_date"
            else:
                evidence_state = "none"
            validation = "unknown"
            gate_id = None
            if cur is not None:
                link = linked.get(str(cur.node_id))
                if link and link.get("status"):
                    validation = str(link["status"])
                gate_id = cur.gate_id or gate_for_ref.get(f"{item.key}@{cur.revision}")
            rows.append(
                {
                    "key": item.key,
                    "item_type": item.item_type,
                    "name": (cur.name if cur and cur.name else item.name),
                    "revision": cur.revision if cur else None,
                    "ref": f"{item.key}@{cur.revision}" if cur else None,
                    "node_id": str(cur.node_id) if cur else None,
                    "revision_status": cur.status if cur else "draft",
                    "validation_status": validation,
                    "run_id": cur.run_id if cur else None,
                    "gate_id": gate_id,
                    "change_reason": cur.change_reason if cur else None,
                    "author": cur.author if cur else None,
                    "updated_at": cur.created_at.isoformat() if cur and cur.created_at else None,
                    "revision_count": len(entry.revisions),
                    "evidence_state": evidence_state,
                    "evidence_count": evidence["current"] + evidence["out_of_date"],
                    "drafts": [_state_dict(d) for d in entry.drafts],
                }
            )

        current_rows = [r for r in rows if r["revision"] is not None]
        groups: list[dict[str, Any]] = []
        for item_type in sorted({r["item_type"] for r in current_rows}):
            members = [r for r in current_rows if r["item_type"] == item_type]
            groups.append(
                {"item_type": item_type, "count": len(members), "keys": [r["key"] for r in members]}
            )

        # Work products the project lists that are neither a revision of an
        # item nor a record: unclassified types (pinmap, gerber, ...), derived
        # views (prd) and definitions written before items existed. A superseded one is
        # history, not current, so it is left out.
        record_ids = {str(r.node_id) for r in records}
        other: list[dict[str, Any]] = []
        for wid, w in linked.items():
            try:
                uid = UUID(wid)
            except ValueError:
                continue
            if uid in revision_of or wid in record_ids:
                continue
            wtype = str(w.get("type") or "")
            spec = classify(wtype)
            if spec is not None and spec.kind == "record":
                continue
            if is_definition(wtype) and await _is_superseded(twin, uid):
                continue
            other.append(
                {
                    "node_id": wid,
                    "name": w.get("name") or wid,
                    "type": wtype,
                    "validation_status": str(w.get("status") or "unknown"),
                    "updated_at": w.get("updated_at"),
                }
            )

        status_pool = [r["validation_status"] for r in current_rows] + [
            o["validation_status"] for o in other
        ]
        valid = status_pool.count("valid")
        total = len(status_pool)
        readiness = round(valid / total * 100) if total else 0
        record_dicts = [
            {
                "node_id": str(r.node_id),
                "record_type": r.record_type,
                "name": r.name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "analysed": r.analysed,
                "out_of_date": r.out_of_date,
                "staleness": r.staleness,
            }
            for r in sorted(
                records, key=lambda r: (r.created_at is None, r.created_at), reverse=True
            )
        ]
        counts = {
            "items": len(current_rows),
            "other": len(other),
            "total": total,
            "valid": valid,
            "warning": status_pool.count("warning"),
            "error": status_pool.count("error"),
            "unknown": status_pool.count("unknown"),
            "drafts": sum(len(r["drafts"]) for r in rows),
            "superseded_revisions": sum(max(r["revision_count"] - 1, 0) for r in rows),
            "decisions": sum(1 for r in records if r.record_type == "design_decision"),
            "simulation_results": sum(1 for r in records if r.record_type == "simulation_result"),
            "evidence": sum(1 for r in records if r.record_type == "evidence"),
            "out_of_date_results": sum(1 for r in records if r.out_of_date),
            "by_type": {g["item_type"]: g["count"] for g in groups},
        }
        span.set_attribute("current_view.items", counts["items"])
        span.set_attribute("current_view.readiness", readiness)
        logger.info(
            "current_view_built",
            project_id=str(pid) if pid else None,
            items=counts["items"],
            other=counts["other"],
            drafts=counts["drafts"],
            out_of_date_results=counts["out_of_date_results"],
            readiness=readiness,
        )
        return CurrentView(
            project_id=pid,
            items=rows,
            groups=groups,
            records=record_dicts,
            other=other,
            counts=counts,
            readiness=readiness,
        )


async def _is_superseded(twin: Any, node_id: UUID) -> bool:
    edges = await twin.graph.get_edges(node_id, direction="incoming", edge_type=EdgeType.SUPERSEDES)
    return bool(edges)


async def revision_index(twin: Any, project_id: UUID | str | None) -> dict[str, dict[str, Any]]:
    """Node id -> the item revision it is, for every revision of every project item.

    A constraint set's ``Constraint`` nodes map to the set's revision too
    (``via: constraint_set``), so a requirement row can show ``CS-...@n``.
    Open drafts are left out: outside their run they are not part of the design.
    """
    out: dict[str, dict[str, Any]] = {}
    with tracer.start_as_current_span("twin.items.revision_index") as span:
        for entry in await current_items(twin, project_id):
            current_rev = entry.current.revision if entry.current else None
            for state in entry.revisions:
                if state.status == "draft":
                    continue
                row = {
                    "key": entry.item.key,
                    "item_type": entry.item.item_type,
                    "revision": state.revision,
                    "ref": f"{entry.item.key}@{state.revision}",
                    "status": state.status,
                    "current": state.revision == current_rev,
                    "revision_count": len(entry.revisions),
                    "via": "revision",
                }
                out[str(state.node_id)] = row
                if entry.item.item_type == "constraint_set":
                    for edge in await twin.graph.get_edges(
                        state.node_id, direction="outgoing", edge_type=EdgeType.CONSTRAINED_BY
                    ):
                        out.setdefault(str(edge.target_id), {**row, "via": "constraint_set"})
        span.set_attribute("revision_index.size", len(out))
    return out
