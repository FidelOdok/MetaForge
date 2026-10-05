"""Records pinned to item revisions, and stale when a revision moves (FORGE-527).

Spec sections 20 (Staleness and Invalidation), 21 (Dependency-Directed
Invalidation) and 54 (Evidence Validity): a record (a ``simulation_result``,
a ``design_decision``, an ``evidence`` entity) is only valid against the
definition revisions it was produced from, so it declares them as
``KEY@n`` refs. When one of those items gets a newer head, the record is
``STALE``; it is never deleted.

Storage, deliberately both (the same choice FORGE-523 made for items):

* ``metadata.depends_on_items`` on the record: ``[{item_key, revision,
  item_ref, item_type, node_id}]`` plus ``metadata.staleness`` (and, once it
  changes, ``staleness_reason`` / ``stale_for`` / ``superseded_by``). The
  record is born carrying its pins, so a node read on its own (``GET
  /v1/twin/nodes/{id}``, ``twin.get_node``, a gate) answers "what was this
  for, and is it still current" with no extra lookup.
* one ``DEPENDS_ON`` edge per pin, record -> the pinned revision node, with
  ``metadata.kind = "revision_pin"``. This is what makes invalidation
  dependency-directed: when an item's head moves, the walk starts at that
  item's older revision nodes and follows their incoming pin edges, so only
  records downstream of the changed item are touched (section 21), and the
  dependency is traversable in Cypher.

The status vocabulary is FORGE-59's :class:`StalenessStatus`: ``CURRENT``,
``STALE`` (a pinned item moved on), ``INVALID`` (pinned to a draft its gate
rejected or abandoned, so it was never about the design), ``SUPERSEDED`` (a
re-run on the newer revision replaced it) and ``REVALIDATED`` (set by a
caller that re-checked it and found it still holds).

A status change rewrites one metadata field on the record through
``graph.update_node``, not through the entity's revisioning update: a stale
tag is not new content, and bumping an evidence entity's own revision would
make anything pinned to it look stale in turn (the over-marking FORGE-59's
docstring accepts for its own path).

Everything that runs after the record or the head write already succeeded is
best-effort: a failure is logged and counted, never raised, because the write
it follows is already saved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.consistency.staleness import StalenessStatus
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.consistency.record_pins")

#: Metadata key carrying a record's revision pins.
PINS_KEY = "depends_on_items"
#: Metadata key carrying a record's staleness status (FORGE-59's key).
STALENESS_KEY = "staleness"
#: ``DEPENDS_ON`` edge ``metadata.kind`` for a revision pin.
PIN_EDGE_KIND = "revision_pin"
#: Every pin edge kind the staleness walk follows: FORGE-528's decision
#: basis edge is the same pin (``item_ref`` + the exact revision node), so a
#: decision written with ``depends_on`` goes stale like any other record.
PIN_EDGE_KINDS = frozenset({PIN_EDGE_KIND, "decision_basis"})

#: Statuses that still count as valid evidence.
VALID_STATUSES = frozenset({StalenessStatus.CURRENT.value, StalenessStatus.REVALIDATED.value})

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-records")
    return _metrics


def _count(record_type: str, status: str) -> None:
    try:
        _collector().record_twin_record_staleness(record_type, status)
    except Exception:  # noqa: BLE001 - metrics never break a write  # pragma: no cover
        pass


@dataclass(frozen=True)
class ItemPin:
    """One ``KEY@n`` a record depends on."""

    item_key: str
    revision: int
    node_id: UUID
    item_type: str = ""

    @property
    def ref(self) -> str:
        return f"{self.item_key}@{self.revision}"

    def to_json(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key,
            "revision": self.revision,
            "item_ref": self.ref,
            "item_type": self.item_type,
            "node_id": str(self.node_id),
        }


@dataclass
class StaleRecord:
    """One record whose status a head move (or a closed draft) changed."""

    record_id: UUID
    record_type: str
    name: str
    status: str
    reason: str
    pinned: str
    current: str | None = None


@dataclass
class PinResult:
    """What :func:`link_record` did, for the recorder's result."""

    depends_on: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_result(self) -> dict[str, Any]:
        """The keys a record tool adds to its result."""
        out: dict[str, Any] = {
            "depends_on": list(self.depends_on),
            "staleness": StalenessStatus.CURRENT.value,
            "superseded_records": list(self.superseded),
        }
        if self.warnings:
            out["pin_warnings"] = list(self.warnings)
        return out


# ---------------------------------------------------------------------------
# Reading a record's pins and status
# ---------------------------------------------------------------------------


def record_pins(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The pin entries a record's metadata carries (possibly none)."""
    raw = (metadata or {}).get(PINS_KEY) or []
    return [p for p in raw if isinstance(p, dict) and p.get("item_key")]


def record_staleness(metadata: dict[str, Any] | None) -> str:
    """The record's status; a record written before FORGE-527 reads as current."""
    value = (metadata or {}).get(STALENESS_KEY) or StalenessStatus.CURRENT.value
    return str(value)


def is_valid_evidence(metadata: dict[str, Any] | None) -> bool:
    """True when a record may still satisfy a gate criterion."""
    return record_staleness(metadata) in VALID_STATUSES


def describe_staleness(name: str, metadata: dict[str, Any] | None) -> str | None:
    """A gate finding for an invalid record, or ``None`` when it is still valid.

    Names the record and the revision it was for, e.g. "'Bracket FEA' is
    stale: it was for CAD-BRACKET@1, and CAD-BRACKET is now @2; re-run it on
    the current revision".
    """
    meta = metadata or {}
    status = record_staleness(meta)
    if status in VALID_STATUSES:
        return None
    reason = str(meta.get("staleness_reason") or "")
    if not reason:
        pins = ", ".join(p.get("item_ref") or "" for p in record_pins(meta))
        reason = f"it was for {pins}" if pins else "its inputs changed"
    advice = {
        StalenessStatus.STALE.value: "re-run it on the current revision",
        StalenessStatus.SUPERSEDED.value: "use the record that replaced it",
        StalenessStatus.INVALID.value: "it was for revisions that never became current",
    }.get(status, "re-check it")
    return f"'{name}' is {status}: {reason}; {advice}"


# ---------------------------------------------------------------------------
# Resolving pins at record time
# ---------------------------------------------------------------------------


def _as_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except (TypeError, ValueError):
        return None


async def resolve_pins(
    twin: Any,
    *,
    refs: list[str] | None = None,
    node_ids: list[Any] | None = None,
    project_id: UUID | str | None = None,
    run_id: str | None = None,
    include_constraint_sets: bool = False,
) -> list[ItemPin]:
    """The ``KEY@n`` pins for a record about to be written.

    * ``refs``: explicit pins from the caller, ``KEY@n``, a bare ``KEY`` (its
      current revision as this run sees it) or a node id. A bad explicit ref
      raises :class:`~twin_core.items.ItemError`, before anything is written.
    * ``node_ids``: what the record already names (a simulation's source
      cad_model, a decision's parents). Each is pinned to the revision that
      node is; a node that is not an item revision is skipped.
    * ``include_constraint_sets``: also pin the current head of every
      constraint set item in the project (what an analysis or a verification
      was judged against). Inside a run, the run's own draft counts as current.

    One pin per item; an explicit ref wins over an inferred one.
    """
    from twin_core.items import (
        item_for_node,
        list_items,
        resolve_item_ref,
        supports_items,
        visible_head,
    )

    if not supports_items(twin):
        return []
    pins: dict[str, ItemPin] = {}

    def _add(pin: ItemPin) -> None:
        pins.setdefault(pin.item_key, pin)

    async def _from_node(node_id: UUID) -> ItemPin | None:
        found = await item_for_node(twin, node_id)
        if found is None:
            return None
        item, revision = found
        return ItemPin(item.key, revision, node_id, item.item_type)

    for ref in refs or []:
        if not isinstance(ref, str) or not ref.strip():
            continue
        as_id = _as_uuid(ref)
        if as_id is not None:
            pin = await _from_node(as_id)
            if pin is None:
                from twin_core.items import UnknownItemError

                raise UnknownItemError(
                    f"depends_on {ref!r}: that node is not a revision of any item; "
                    "pass an item reference such as 'CAD-BRACKET@2'"
                )
            _add(pin)
            continue
        item, rev = await resolve_item_ref(twin, ref, project_id, run_id=run_id)
        _add(ItemPin(item.key, rev.revision, rev.node_id, item.item_type))

    for raw in node_ids or []:
        node_id = _as_uuid(raw)
        if node_id is None:
            continue
        try:
            pin = await _from_node(node_id)
        except Exception as exc:  # noqa: BLE001 - an inferred pin is best-effort
            logger.warning("record_pin_lookup_failed", node_id=str(node_id), error=str(exc))
            continue
        if pin is not None:
            _add(pin)

    if include_constraint_sets and project_id:
        try:
            for item in await list_items(
                twin, project_id=project_id, item_type="constraint_set", include_unheaded=True
            ):
                seen = visible_head(item, run_id)
                if seen is not None:
                    _add(ItemPin(item.key, seen[0], seen[1], item.item_type))
        except Exception as exc:  # noqa: BLE001 - an inferred pin is best-effort
            logger.warning("record_pin_constraint_sets_failed", error=str(exc))

    return list(pins.values())


def pin_metadata(pins: list[ItemPin]) -> dict[str, Any]:
    """The metadata a record is born with: its pins and ``staleness: current``."""
    out: dict[str, Any] = {STALENESS_KEY: StalenessStatus.CURRENT.value}
    if pins:
        out[PINS_KEY] = [p.to_json() for p in pins]
    return out


# ---------------------------------------------------------------------------
# After the record exists: pin edges and superseding older runs
# ---------------------------------------------------------------------------


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def record_subject(record_type: str, name: str, metadata: dict[str, Any] | None) -> str:
    """What makes two records "the same check, run again".

    A simulation is the same analysis when its ``analysis_type`` (else its
    ``load_case``) matches; evidence when its ``evidence_type`` and producing
    tool match; any other record (a decision) when its name matches.
    """
    meta = metadata or {}
    if record_type == "simulation_result":
        return _norm(meta.get("analysis_type") or meta.get("load_case") or "")
    if record_type == "evidence":
        producer = meta.get("producer") if isinstance(meta.get("producer"), dict) else {}
        return f"{_norm(meta.get('evidence_type'))}|{_norm(producer.get('tool'))}"
    return _norm(name)


def _node_metadata(node: Any) -> dict[str, Any]:
    return dict(getattr(node, "metadata", None) or {})


def _node_name(node: Any) -> str:
    for attr in ("name", "title", "statement"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(getattr(node, "id", ""))


def _node_record_type(node: Any) -> str:
    entity_type = getattr(node, "entity_type", None)
    if isinstance(entity_type, str) and entity_type:
        return entity_type
    wp_type = getattr(node, "type", None)
    return str(getattr(wp_type, "value", wp_type) or "")


async def _set_status(
    twin: Any, node: Any, status: StalenessStatus, **fields: Any
) -> dict[str, Any]:
    meta = _node_metadata(node)
    meta[STALENESS_KEY] = status.value
    for key, value in fields.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    updates: dict[str, Any] = {"metadata": meta}
    # A status tag is not new content: keep updated_at, so a stale record
    # never looks like the newest result of a phase window.
    if getattr(node, "updated_at", None) is not None:
        updates["updated_at"] = node.updated_at
    await twin.graph.update_node(node.id, updates)
    return meta


async def _pinned_records(twin: Any, revision_node_id: UUID) -> list[tuple[Any, Any]]:
    """``(edge, record node)`` for every record pinned to ``revision_node_id``."""
    edges = await twin.graph.get_edges(
        revision_node_id, direction="incoming", edge_type=EdgeType.DEPENDS_ON
    )
    out: list[tuple[Any, Any]] = []
    for edge in edges:
        if (edge.metadata or {}).get("kind") not in PIN_EDGE_KINDS:
            continue
        node = await twin.graph.get_node(edge.source_id)
        if node is not None:
            out.append((edge, node))
    return out


async def link_record(
    twin: Any,
    record_id: UUID,
    pins: list[ItemPin],
    *,
    record_type: str,
    name: str,
    metadata: dict[str, Any] | None = None,
    edge_kind: str = PIN_EDGE_KIND,
) -> PinResult:
    """Add the pin edges for a just-written record and supersede older runs of it.

    An older record of the same type and subject (:func:`record_subject`)
    pinned to an earlier revision of an item this one pins is ``SUPERSEDED``
    by it, with a ``SUPERSEDES`` edge new -> old. ``edge_kind`` is
    ``revision_pin``, or ``decision_basis`` for a decision (FORGE-528's name for
    the same edge). Never raises.
    """
    result = PinResult(depends_on=[p.ref for p in pins])
    if not pins:
        return result
    with tracer.start_as_current_span("twin.records.link_pins") as span:
        span.set_attribute("record.type", record_type)
        span.set_attribute("record.pins", len(pins))
        for pin in pins:
            try:
                await twin.add_edge(
                    record_id,
                    pin.node_id,
                    EdgeType.DEPENDS_ON,
                    metadata={
                        "kind": edge_kind,
                        "item_key": pin.item_key,
                        "revision": pin.revision,
                        "item_ref": pin.ref,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - the record is already saved
                span.record_exception(exc)
                result.warnings.append(f"pin edge to {pin.ref} not written: {exc}")
                _count(record_type, "pin_failed")
                logger.warning(
                    "record_pin_edge_failed",
                    record_id=str(record_id),
                    item_ref=pin.ref,
                    error=str(exc),
                )
        try:
            result.superseded = await _supersede_older(
                twin, record_id, pins, record_type=record_type, name=name, metadata=metadata
            )
        except Exception as exc:  # noqa: BLE001 - the record is already saved
            span.record_exception(exc)
            result.warnings.append(f"older runs not checked for superseding: {exc}")
            logger.warning("record_supersede_failed", record_id=str(record_id), error=str(exc))
        logger.info(
            "record_pinned",
            record_id=str(record_id),
            record_type=record_type,
            depends_on=result.depends_on,
            superseded=result.superseded,
        )
        return result


async def _supersede_older(
    twin: Any,
    record_id: UUID,
    pins: list[ItemPin],
    *,
    record_type: str,
    name: str,
    metadata: dict[str, Any] | None,
) -> list[str]:
    from twin_core.items import item_for_node, item_history
    from twin_core.items.service import revision_status

    subject = record_subject(record_type, name, metadata)
    superseded: list[str] = []
    for pin in pins:
        # A re-run on an open draft replaces nothing yet: the draft may be
        # rejected. on_head_moved supersedes when the gate approves it.
        if await revision_status(twin, pin.node_id) == "draft":
            continue
        found = await item_for_node(twin, pin.node_id)
        if found is None:
            continue
        item, _ = found
        for rev in await item_history(twin, item, include_drafts=True):
            if rev.revision >= pin.revision:
                continue
            for _edge, node in await _pinned_records(twin, rev.node_id):
                if node.id == record_id or str(node.id) in superseded:
                    continue
                meta = _node_metadata(node)
                if record_staleness(meta) in (
                    StalenessStatus.SUPERSEDED.value,
                    StalenessStatus.INVALID.value,
                ):
                    continue
                if _node_record_type(node) != record_type:
                    continue
                if record_subject(record_type, _node_name(node), meta) != subject:
                    continue
                await _set_status(
                    twin,
                    node,
                    StalenessStatus.SUPERSEDED,
                    superseded_by=str(record_id),
                    staleness_reason=(
                        f"re-run on {pin.ref} as {record_id}; this one was for "
                        f"{pin.item_key}@{rev.revision}"
                    ),
                )
                await twin.add_edge(
                    record_id, node.id, EdgeType.SUPERSEDES, metadata={"kind": "record_rerun"}
                )
                superseded.append(str(node.id))
                _count(record_type, StalenessStatus.SUPERSEDED.value)
                logger.info(
                    "record_superseded",
                    record_id=str(node.id),
                    superseded_by=str(record_id),
                    record_type=record_type,
                    item_ref=pin.ref,
                )
    return superseded


# ---------------------------------------------------------------------------
# When an item's head moves, or a run's drafts are closed
# ---------------------------------------------------------------------------


def _pins_from_metadata(metadata: dict[str, Any]) -> list[ItemPin]:
    out: list[ItemPin] = []
    for raw in record_pins(metadata):
        node_id = _as_uuid(raw.get("node_id"))
        if node_id is None:
            continue
        out.append(
            ItemPin(
                str(raw["item_key"]),
                int(raw.get("revision") or 0),
                node_id,
                str(raw.get("item_type") or ""),
            )
        )
    return out


async def on_head_moved(
    twin: Any, item: Any, head_revision: int, head_node_id: UUID | None
) -> list[StaleRecord]:
    """``item``'s head just moved to ``@head_revision`` (a write outside a run,
    or a gate approving a run's draft). Never raises.

    1. A record pinned to the new head (a re-run recorded on the draft while
       the run was open) now supersedes its older runs on earlier revisions.
    2. Every other record pinned to an older revision becomes ``STALE``.
    """
    if head_node_id is not None:
        try:
            for _edge, node in await _pinned_records(twin, head_node_id):
                meta = _node_metadata(node)
                if record_staleness(meta) not in VALID_STATUSES:
                    continue
                record_type = _node_record_type(node)
                await _supersede_older(
                    twin,
                    node.id,
                    _pins_from_metadata(meta),
                    record_type=record_type,
                    name=_node_name(node),
                    metadata=meta,
                )
        except Exception as exc:  # noqa: BLE001 - never undo the head move
            logger.warning("record_supersede_on_approval_failed", item_key=item.key, error=str(exc))
    return await mark_dependents_stale(twin, item, head_revision)


async def mark_dependents_stale(twin: Any, item: Any, head_revision: int) -> list[StaleRecord]:
    """``item``'s head is now ``@head_revision``: every record pinned to an older
    revision of it, still valid, becomes ``STALE``. Never raises.

    Dependency-directed (spec section 21): only records with a pin edge to one
    of this item's own revisions are visited. A record pinned to the new head
    (an in-run record on the draft that was just approved) stays current.
    """
    from twin_core.items import item_history

    marked: list[StaleRecord] = []
    with tracer.start_as_current_span("twin.records.mark_stale") as span:
        span.set_attribute("item.key", item.key)
        span.set_attribute("item.head_revision", head_revision)
        try:
            for rev in await item_history(twin, item, include_drafts=True):
                if rev.revision >= head_revision:
                    continue
                for edge, node in await _pinned_records(twin, rev.node_id):
                    meta = _node_metadata(node)
                    if record_staleness(meta) not in VALID_STATUSES:
                        continue
                    pinned = int((edge.metadata or {}).get("revision") or rev.revision)
                    if pinned >= head_revision:
                        continue
                    pinned_ref = f"{item.key}@{pinned}"
                    current_ref = f"{item.key}@{head_revision}"
                    reason = f"it was for {pinned_ref}, and {item.key} is now @{head_revision}"
                    stale_for = dict(meta.get("stale_for") or {})
                    stale_for[item.key] = {"pinned": pinned, "current": head_revision}
                    await _set_status(
                        twin,
                        node,
                        StalenessStatus.STALE,
                        staleness_reason=reason,
                        stale_for=stale_for,
                    )
                    record_type = _node_record_type(node)
                    marked.append(
                        StaleRecord(
                            record_id=node.id,
                            record_type=record_type,
                            name=_node_name(node),
                            status=StalenessStatus.STALE.value,
                            reason=reason,
                            pinned=pinned_ref,
                            current=current_ref,
                        )
                    )
                    _count(record_type, StalenessStatus.STALE.value)
        except Exception as exc:  # noqa: BLE001 - never undo the head move
            span.record_exception(exc)
            logger.warning(
                "record_staleness_failed",
                item_key=item.key,
                head_revision=head_revision,
                error=str(exc),
                consequence="records pinned to older revisions may still read as current",
            )
            _count(item.item_type or "unknown", "propagation_failed")
        span.set_attribute("records.marked_stale", len(marked))
        if marked:
            logger.info(
                "records_marked_stale",
                item_key=item.key,
                head_revision=head_revision,
                records=[f"{m.record_type}:{m.record_id}" for m in marked],
            )
        return marked


async def invalidate_closed_draft(
    twin: Any, item: Any, revision: int, node_id: UUID, status: str
) -> list[StaleRecord]:
    """Records pinned to a draft its gate rejected (or the run abandoned) are
    ``INVALID``: that revision never became the design. Never raises."""
    marked: list[StaleRecord] = []
    ref = f"{item.key}@{revision}"
    try:
        for _edge, node in await _pinned_records(twin, node_id):
            meta = _node_metadata(node)
            if record_staleness(meta) not in VALID_STATUSES:
                continue
            reason = f"it was for {ref}, a draft that was {status} and never became current"
            await _set_status(twin, node, StalenessStatus.INVALID, staleness_reason=reason)
            record_type = _node_record_type(node)
            marked.append(
                StaleRecord(
                    record_id=node.id,
                    record_type=record_type,
                    name=_node_name(node),
                    status=StalenessStatus.INVALID.value,
                    reason=reason,
                    pinned=ref,
                )
            )
            _count(record_type, StalenessStatus.INVALID.value)
    except Exception as exc:  # noqa: BLE001 - never undo the close
        logger.warning("record_invalidate_failed", item_ref=ref, error=str(exc))
    if marked:
        logger.info(
            "records_marked_invalid",
            item_ref=ref,
            records=[f"{m.record_type}:{m.record_id}" for m in marked],
        )
    return marked


async def set_record_status(
    twin: Any, record_id: UUID, status: StalenessStatus, reason: str = ""
) -> None:
    """Set one record's status by hand (e.g. ``REVALIDATED`` after a re-check)."""
    node = await twin.graph.get_node(record_id)
    if node is None:
        raise KeyError(f"record {record_id} not found")
    await _set_status(twin, node, status, staleness_reason=reason or None)
    _count(_node_record_type(node), status.value)
