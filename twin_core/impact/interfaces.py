"""Interface edges in the twin: declare, propose, confirm, remove, list (FORGE-793).

FRS XDM-001: explicit mechanical part to PCB mounting to connector to firmware
interface to simulation input relationships. Each edge is an
:class:`~twin_core.models.interface.InterfaceEdge`, stored as one
``interface_edge`` work product in the project (a record: its status changes
in place and it is never deleted, so a removed coupling still reads in
history).

Three edge types are not stored, because the twin already holds them and a
second copy could only drift:

* ``simulation_input_of``: a ``simulation_result`` record's own revision pins
  (``metadata.depends_on_items``, FORGE-527). The simulation was run on
  ``CAD-BRACKET@2``; that is the edge, recorded by the tool that ran it.
* ``feature_of``: the sketch layer's ``DEPENDS_ON`` edges from a sketch plane,
  interpretation or parametric sketch revision to the sketch revision it was
  made from (kinds ``sketch_plane`` and ``sketch_compiler``).

* ``manufactured_by``: the ``manufacturing_context_ref`` a generated item's
  head records (``MFC-PROJECT@3``), coupled on the selected process and
  material, so a process change reaches what was generated for the old one.

* ``dfm_constrains``: a ``dfm_finding_set`` record's own revision pins. The
  findings constrain the component's geometry revision they were computed
  on, under the manufacturing context revision they read; the context edge
  is coupled on the fields the rules read (process, material, quantity,
  machine), so a finish edit does not reach the findings (DFM-012).

* From an **interface contract** (``IFC-<kind>``) whose links name items on
  both sides: ``clearance_to``, ``mounts_to`` and ``connects_via`` between the
  enclosure and the board of a CAD to EDA contract, and ``pin_maps_to``
  between the pin map and the firmware of an EDA to firmware contract, each
  coupled on the contract's parameters. These follow the contract's
  agreement: ``proposed`` (``declared_by: inferred``) until every owning
  domain agreed to its current revision, then ``confirmed`` by the person who
  agreed last (``declared_by: human``).

The others are listed with ``derived: true``, ``declared_by: tool`` and status
``confirmed``. A person may still declare any of them by hand.

Provenance is honest by construction: a person's declaration is confirmed at
once; an inferred proposal (``declared_by: inferred``) stays ``proposed``,
with the data it was read from in ``evidence``, until someone confirms it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.interface import (
    RECORD_REF_PREFIX,
    DeclaredBy,
    InterfaceEdge,
    InterfaceEnd,
    InterfaceStatus,
    InterfaceType,
    domain_for_item_type,
    is_record_ref,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.impact.interfaces")

#: Where an ``interface_edge`` node keeps its edge.
EDGE_META_KEY = "interface"

#: ``DEPENDS_ON`` edge kinds the sketch layer writes; each is a ``feature_of``.
FEATURE_EDGE_KINDS = frozenset({"sketch_plane", "sketch_compiler"})
#: Item types whose head may depend on a sketch revision.
FEATURE_ITEM_TYPES = ("sketch_plane", "sketch_interpretation", "parametric_sketch")
#: Item types whose head records the manufacturing context it was generated
#: under (``metadata.manufacturing_context_ref``), each a ``manufactured_by``.
MANUFACTURED_ITEM_TYPES = ("generated_sketch",)
#: What crosses a ``manufactured_by`` edge: a change elsewhere in the context
#: (the intended use, a note) does not reach what was generated.
MANUFACTURED_COUPLED = ("selected_process", "selected_material")
#: What crosses a ``dfm_constrains`` edge from a manufacturing context: the
#: fields DFM rules read. Geometry edges name none, so any change travels.
DFM_CONTEXT_COUPLED = (
    "selected_process",
    "process_candidates",
    "selected_material",
    "material_candidates",
    "quantity",
    "machine_or_supplier_profile",
    "tolerances",
)

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-impact")
    return _metrics


def count_edge(edge_type: str, provenance: str, action: str) -> None:
    """Count one interface edge action (declared, proposed, confirmed, removed)."""
    try:
        _collector().record_interface_edge(edge_type, provenance, action)
    except Exception:  # noqa: BLE001 - metrics never break a write  # pragma: no cover
        pass


class InterfaceError(ValueError):
    """An interface edge request that cannot be honoured (a 4xx, not a crash)."""


class UnknownInterfaceError(InterfaceError):
    """No such edge, item or record in this project."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pid(project_id: UUID | str) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise InterfaceError(f"invalid project id {project_id!r}") from exc


# ---------------------------------------------------------------------------
# Ends
# ---------------------------------------------------------------------------


def _record_domain(node: Any) -> tuple[str, str]:
    """``(domain, record type)`` of a result record node."""
    entity_type = getattr(node, "entity_type", None)
    if isinstance(entity_type, str) and entity_type:
        return ("general", entity_type)
    wp_type = getattr(getattr(node, "type", None), "value", getattr(node, "type", None))
    record_type = str(wp_type or "")
    return (domain_for_item_type(record_type), record_type)


def _node_label(node: Any) -> str:
    for attr in ("name", "statement", "title"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(getattr(node, "id", ""))


async def resolve_end(twin: Any, project_id: UUID | str, ref: str) -> InterfaceEnd:
    """An edge end for ``ref``: ``KEY`` (its head), ``KEY@n`` or ``record:<uuid>``."""
    from twin_core.items import ItemError, find_item, item_history, parse_item_ref

    pid = _pid(project_id)
    text = (ref or "").strip()
    if is_record_ref(text):
        try:
            record_id = UUID(text[len(RECORD_REF_PREFIX) :])
        except ValueError as exc:
            raise InterfaceError(f"{ref!r} is not record:<uuid>") from exc
        node = await twin.graph.get_node(record_id)
        if node is None or getattr(node, "project_id", None) not in (pid, None):
            raise UnknownInterfaceError(f"no record {record_id} in this project")
        domain, record_type = _record_domain(node)
        return InterfaceEnd(
            ref=f"{RECORD_REF_PREFIX}{record_id}",
            domain=domain,
            item_type=record_type,
            node_id=str(record_id),
            label=_node_label(node),
        )
    try:
        key, revision = parse_item_ref(text)
    except ItemError as exc:
        raise InterfaceError(str(exc)) from exc
    item = await find_item(twin, key, pid)
    if item is None:
        raise UnknownInterfaceError(f"no item {key} in this project")
    if item.head_node_id is None:
        raise InterfaceError(f"{key} has no current revision yet; only drafts")
    node_id: UUID | None = item.head_node_id
    wanted = revision or item.head_revision
    if revision is not None and revision != item.head_revision:
        found = next((r for r in await item_history(twin, item) if r.revision == revision), None)
        if found is None:
            raise UnknownInterfaceError(f"{key} has no revision @{revision}")
        node_id = found.node_id
    return InterfaceEnd(
        ref=item.key,
        revision=wanted,
        domain=domain_for_item_type(item.item_type),
        item_type=item.item_type,
        node_id=str(node_id) if node_id else None,
        label=item.name,
    )


# ---------------------------------------------------------------------------
# Stored edges
# ---------------------------------------------------------------------------


def edge_from_node(node: Any) -> InterfaceEdge | None:
    """The edge an ``interface_edge`` node holds, or ``None`` when unreadable."""
    raw = (getattr(node, "metadata", None) or {}).get(EDGE_META_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return InterfaceEdge.model_validate({**raw, "id": str(node.id)})
    except Exception as exc:  # noqa: BLE001 - one bad node must not hide the rest
        logger.warning("interface_edge_unreadable", node_id=str(node.id), error=str(exc))
        return None


async def list_stored(
    twin: Any, project_id: UUID | str, *, include_removed: bool = False
) -> list[InterfaceEdge]:
    """The project's stored interface edges, oldest first."""
    pid = _pid(project_id)
    nodes = await twin.list_work_products(
        work_product_type=WorkProductType.INTERFACE_EDGE, project_id=pid
    )
    edges = [e for e in (edge_from_node(n) for n in nodes) if e is not None]
    if not include_removed:
        edges = [e for e in edges if e.status is not InterfaceStatus.REMOVED]
    return sorted(edges, key=lambda e: (e.created_at or "", e.id))


def _same_link(edge: InterfaceEdge, kind: InterfaceType, source: str, target: str) -> bool:
    if edge.type is not kind or edge.status is InterfaceStatus.REMOVED:
        return False
    if (edge.source.ref, edge.target.ref) == (source, target):
        return True
    return edge.symmetric and (edge.source.ref, edge.target.ref) == (target, source)


async def _write(twin: Any, node_id: UUID, edge: InterfaceEdge) -> None:
    node = await twin.graph.get_node(node_id)
    meta = dict(getattr(node, "metadata", None) or {})
    meta[EDGE_META_KEY] = edge.model_dump(mode="json", exclude={"id"})
    await twin.graph.update_node(node_id, {"metadata": meta})


async def declare_interface(
    twin: Any,
    project_id: UUID | str,
    *,
    type: InterfaceType | str,  # noqa: A002 - the edge's own field name
    source_ref: str,
    target_ref: str,
    declared_by: DeclaredBy | str,
    actor: str,
    confidence: float = 1.0,
    coupled_parameters: list[str] | None = None,
    rationale: str = "",
    evidence: dict[str, Any] | None = None,
) -> tuple[InterfaceEdge, bool]:
    """Store an edge, or return the one already stating the same link.

    Returns ``(edge, created)``. A person declaring a link that an inference
    already proposed confirms that proposal rather than adding a second edge.
    An inference never overrides anything: proposing a link that already
    exists, or that a person removed, changes nothing.
    """
    from twin_core.models.work_product import WorkProduct

    kind = InterfaceType(type)
    by = DeclaredBy(declared_by)
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.interfaces.declare") as span:
        span.set_attribute("interface.type", kind.value)
        span.set_attribute("interface.declared_by", by.value)
        source = await resolve_end(twin, pid, source_ref)
        target = await resolve_end(twin, pid, target_ref)
        if source.ref == target.ref:
            raise InterfaceError("an interface edge needs two different ends")
        stored = await list_stored(twin, pid, include_removed=True)
        existing = next((e for e in stored if _same_link(e, kind, source.ref, target.ref)), None)
        if existing is not None:
            if by is DeclaredBy.HUMAN and existing.status is InterfaceStatus.PROPOSED:
                return await confirm_interface(twin, pid, existing.id, actor=actor), False
            return existing, False
        removed = next(
            (
                e
                for e in stored
                if e.type is kind
                and e.status is InterfaceStatus.REMOVED
                and (e.source.ref, e.target.ref)
                in ((source.ref, target.ref), (target.ref, source.ref))
            ),
            None,
        )
        if by is DeclaredBy.INFERRED and removed is not None:
            # A person removed this link; re-proposing it on every head move
            # would make their decision meaningless.
            return removed, False
        node_id = uuid4()
        now = _now()
        human = by is DeclaredBy.HUMAN
        edge = InterfaceEdge(
            id=str(node_id),
            project_id=str(pid),
            type=kind,
            source=source,
            target=target,
            declared_by=by,
            confidence=1.0 if human else max(0.0, min(1.0, confidence)),
            status=InterfaceStatus.PROPOSED
            if by is DeclaredBy.INFERRED
            else InterfaceStatus.CONFIRMED,
            coupled_parameters=sorted({str(p) for p in coupled_parameters or [] if str(p).strip()}),
            rationale=rationale[:500],
            evidence=dict(evidence or {}),
            created_at=now,
            created_by=actor,
            decided_by=actor if human else None,
            decided_at=now if human else None,
        )
        identity = f"{kind.value}|{source.ref}|{target.ref}"
        wp = WorkProduct(
            id=node_id,
            name=f"{kind.value}: {source.ref} -> {target.ref}",
            type=WorkProductType.INTERFACE_EDGE,
            domain="cross_domain",
            file_path="",
            content_hash=hashlib.sha256(identity.encode()).hexdigest(),
            format="json",
            metadata={EDGE_META_KEY: edge.model_dump(mode="json", exclude={"id"})},
            created_by=actor or by.value,
            project_id=pid,
        )
        await twin.create_work_product(wp)
        action = "proposed" if by is DeclaredBy.INFERRED else "declared"
        count_edge(kind.value, by.value, action)
        span.set_attribute("interface.id", edge.id)
        logger.info(
            "interface_edge_" + action,
            project_id=str(pid),
            edge_id=edge.id,
            type=kind.value,
            source=source.pinned_ref,
            target=target.pinned_ref,
            declared_by=by.value,
            confidence=edge.confidence,
        )
        return edge, True


async def _stored_edge(twin: Any, pid: UUID, edge_id: str) -> InterfaceEdge:
    try:
        node_id = UUID(str(edge_id))
    except ValueError as exc:
        raise UnknownInterfaceError(f"no interface edge {edge_id!r}") from exc
    node = await twin.graph.get_node(node_id)
    edge = edge_from_node(node) if node is not None else None
    if edge is None or getattr(node, "project_id", None) != pid:
        raise UnknownInterfaceError(f"no interface edge {edge_id} in this project")
    return edge


async def confirm_interface(
    twin: Any, project_id: UUID | str, edge_id: str, *, actor: str
) -> InterfaceEdge:
    """A person confirms an edge: it is re-pinned to both ends' current heads."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.interfaces.confirm") as span:
        span.set_attribute("interface.id", edge_id)
        edge = await _stored_edge(twin, pid, edge_id)
        if edge.status is InterfaceStatus.REMOVED:
            raise InterfaceError("this edge was removed; declare it again instead")
        source = await resolve_end(twin, pid, edge.source.ref)
        target = await resolve_end(twin, pid, edge.target.ref)
        now = _now()
        confirmed = edge.model_copy(
            update={
                "source": source,
                "target": target,
                "status": InterfaceStatus.CONFIRMED,
                "decided_by": actor,
                "decided_at": now,
            }
        )
        await _write(twin, UUID(edge.id), confirmed)
        count_edge(edge.type.value, edge.declared_by.value, "confirmed")
        logger.info(
            "interface_edge_confirmed",
            project_id=str(pid),
            edge_id=edge.id,
            type=edge.type.value,
            source=source.pinned_ref,
            target=target.pinned_ref,
            confirmed_by=actor,
        )
        return confirmed


async def remove_interface(
    twin: Any, project_id: UUID | str, edge_id: str, *, actor: str, reason: str = ""
) -> InterfaceEdge:
    """A person removes an edge. It is kept, marked ``removed``, never deleted."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.interfaces.remove") as span:
        span.set_attribute("interface.id", edge_id)
        edge = await _stored_edge(twin, pid, edge_id)
        if edge.status is InterfaceStatus.REMOVED:
            return edge
        removed = edge.model_copy(
            update={
                "status": InterfaceStatus.REMOVED,
                "decided_by": actor,
                "decided_at": _now(),
                "removed_reason": reason[:500] or None,
            }
        )
        await _write(twin, UUID(edge.id), removed)
        count_edge(edge.type.value, edge.declared_by.value, "removed")
        logger.info(
            "interface_edge_removed",
            project_id=str(pid),
            edge_id=edge.id,
            type=edge.type.value,
            removed_by=actor,
        )
        return removed


# ---------------------------------------------------------------------------
# Derived edges
# ---------------------------------------------------------------------------


async def _simulation_edges(twin: Any, pid: UUID) -> list[InterfaceEdge]:
    """``simulation_input_of`` from each simulation result's own revision pins."""
    from twin_core.consistency.record_pins import record_pins, record_staleness

    out: list[InterfaceEdge] = []
    sims = await twin.list_work_products(
        work_product_type=WorkProductType.SIMULATION_RESULT, project_id=pid
    )
    for sim in sims:
        meta = sim.metadata or {}
        if record_staleness(meta) in ("superseded", "invalid"):
            continue  # replaced by a re-run, or never about the design
        for pin in record_pins(meta):
            key = str(pin["item_key"])
            item_type = str(pin.get("item_type") or "")
            revision = int(pin.get("revision") or 0)
            out.append(
                InterfaceEdge(
                    id=f"derived:simulation:{sim.id}:{key}",
                    project_id=str(pid),
                    type=InterfaceType.SIMULATION_INPUT_OF,
                    source=InterfaceEnd(
                        ref=key,
                        revision=revision,
                        domain=domain_for_item_type(item_type),
                        item_type=item_type,
                        node_id=str(pin.get("node_id") or "") or None,
                        label=key,
                    ),
                    target=InterfaceEnd(
                        ref=f"{RECORD_REF_PREFIX}{sim.id}",
                        domain="simulation",
                        item_type="simulation_result",
                        node_id=str(sim.id),
                        label=sim.name,
                    ),
                    declared_by=DeclaredBy.TOOL,
                    status=InterfaceStatus.CONFIRMED,
                    rationale=f"the simulation was run on {key}@{revision}",
                    evidence={"pin": pin.get("item_ref") or f"{key}@{revision}"},
                    derived=True,
                    created_at=sim.created_at.isoformat() if sim.created_at else None,
                    created_by=sim.created_by,
                )
            )
    return out


async def _feature_edges(twin: Any, pid: UUID) -> list[InterfaceEdge]:
    """``feature_of`` from the sketch layer's ``DEPENDS_ON`` edges."""
    from twin_core.items import list_items

    out: list[InterfaceEdge] = []
    for item_type in FEATURE_ITEM_TYPES:
        for item in await list_items(twin, project_id=pid, item_type=item_type):
            if item.head_node_id is None:
                continue
            edges = await twin.graph.get_edges(
                item.head_node_id, direction="outgoing", edge_type=EdgeType.DEPENDS_ON
            )
            for dep in edges:
                meta = dep.metadata or {}
                key = meta.get("item_key")
                if meta.get("kind") not in FEATURE_EDGE_KINDS or not key or key == item.key:
                    continue
                revision = int(meta.get("revision") or 0)
                source_type = "sketch_strokes" if str(key).startswith("SKT-") else ""
                out.append(
                    InterfaceEdge(
                        id=f"derived:feature:{item.head_node_id}:{key}",
                        project_id=str(pid),
                        type=InterfaceType.FEATURE_OF,
                        source=InterfaceEnd(
                            ref=str(key),
                            revision=revision,
                            domain="sketch",
                            item_type=source_type,
                            node_id=str(dep.target_id),
                            label=str(key),
                        ),
                        target=InterfaceEnd(
                            ref=item.key,
                            revision=item.head_revision,
                            domain=domain_for_item_type(item.item_type),
                            item_type=item.item_type,
                            node_id=str(item.head_node_id),
                            label=item.name,
                        ),
                        declared_by=DeclaredBy.TOOL,
                        status=InterfaceStatus.CONFIRMED,
                        rationale=f"{item.key}@{item.head_revision} was made from {key}@{revision}",
                        evidence={"depends_on": f"{key}@{revision}", "kind": meta.get("kind")},
                        derived=True,
                    )
                )
    return out


async def _manufacturing_edges(twin: Any, pid: UUID) -> list[InterfaceEdge]:
    """``manufactured_by`` from the ``manufacturing_context_ref`` a generated item records."""
    from twin_core.items import ItemError, list_items, parse_item_ref

    contexts = {
        i.key: i for i in await list_items(twin, project_id=pid, item_type="manufacturing_context")
    }
    if not contexts:
        return []
    out: list[InterfaceEdge] = []
    for item_type in MANUFACTURED_ITEM_TYPES:
        for item in await list_items(twin, project_id=pid, item_type=item_type):
            if item.head_node_id is None:
                continue
            node = await twin.graph.get_node(item.head_node_id)
            meta = dict(getattr(node, "metadata", None) or {})
            ref = meta.get("manufacturing_context_ref")
            if not isinstance(ref, str) or "@" not in ref or meta.get("speculative"):
                # Unresolved, or speculative: nothing constrained it.
                continue
            try:
                key, revision = parse_item_ref(ref)
            except ItemError:
                continue
            context = contexts.get(key)
            if context is None:
                continue
            out.append(
                InterfaceEdge(
                    id=f"derived:manufactured:{item.head_node_id}:{key}",
                    project_id=str(pid),
                    type=InterfaceType.MANUFACTURED_BY,
                    source=InterfaceEnd(
                        ref=key,
                        revision=revision,
                        domain="manufacturing",
                        item_type="manufacturing_context",
                        node_id=str(context.head_node_id) if context.head_node_id else None,
                        label=context.name or key,
                    ),
                    target=InterfaceEnd(
                        ref=item.key,
                        revision=item.head_revision,
                        domain=domain_for_item_type(item.item_type),
                        item_type=item.item_type,
                        node_id=str(item.head_node_id),
                        label=item.name,
                    ),
                    declared_by=DeclaredBy.TOOL,
                    status=InterfaceStatus.CONFIRMED,
                    coupled_parameters=list(MANUFACTURED_COUPLED),
                    rationale=f"{item.key}@{item.head_revision} was generated under {ref}",
                    evidence={"manufacturing_context_ref": ref},
                    derived=True,
                )
            )
    return out


async def _dfm_edges(twin: Any, pid: UUID) -> list[InterfaceEdge]:
    """``dfm_constrains`` from each current or stale DFM finding set's revision pins."""
    from twin_core.consistency.record_pins import record_pins, record_staleness

    out: list[InterfaceEdge] = []
    sets = await twin.list_work_products(
        work_product_type=WorkProductType.DFM_FINDING_SET, project_id=pid
    )
    for node in sets:
        meta = node.metadata or {}
        if record_staleness(meta) in ("superseded", "invalid"):
            continue  # replaced by a recompute, or never about the design
        raw = meta.get("dfm_finding_set")
        finding_set: dict[str, Any] = raw if isinstance(raw, dict) else {}
        component = str(finding_set.get("component_id") or "")
        for pin in record_pins(meta):
            key = str(pin["item_key"])
            item_type = str(pin.get("item_type") or "")
            revision = int(pin.get("revision") or 0)
            context = item_type == "manufacturing_context"
            out.append(
                InterfaceEdge(
                    id=f"derived:dfm:{node.id}:{key}",
                    project_id=str(pid),
                    type=InterfaceType.DFM_CONSTRAINS,
                    source=InterfaceEnd(
                        ref=f"{RECORD_REF_PREFIX}{node.id}",
                        domain="manufacturing",
                        item_type="dfm_finding_set",
                        node_id=str(node.id),
                        label=node.name,
                    ),
                    target=InterfaceEnd(
                        ref=key,
                        revision=revision,
                        domain=domain_for_item_type(item_type),
                        item_type=item_type,
                        node_id=str(pin.get("node_id") or "") or None,
                        label=key,
                    ),
                    declared_by=DeclaredBy.TOOL,
                    status=InterfaceStatus.CONFIRMED,
                    coupled_parameters=list(DFM_CONTEXT_COUPLED) if context else [],
                    rationale=(
                        f"DFM findings for {component or 'the component'} were computed "
                        f"{'under' if context else 'on'} {key}@{revision}"
                    ),
                    evidence={
                        "pin": pin.get("item_ref") or f"{key}@{revision}",
                        "component_id": component,
                        "context_ref": finding_set.get("context_ref"),
                        "profile": finding_set.get("profile_id"),
                    },
                    derived=True,
                    created_at=node.created_at.isoformat() if node.created_at else None,
                    created_by=node.created_by,
                )
            )
    return out


#: The edges a CAD to EDA contract implies, each with what crosses it.
CONTRACT_CAD_EDA_EDGES: tuple[tuple[InterfaceType, str, str, tuple[str, ...]], ...] = (
    (
        InterfaceType.CLEARANCE_TO,
        "enclosure",
        "pcb",
        (
            "dimensions",
            "board_outline",
            "internal_envelope",
            "internal_clearance",
            "component_max_height",
            "pcb_outline",
            "pcb_clearance",
            "max_component_height",
            "keep_outs",
            "led_position",
            "cable_routing",
        ),
    ),
    (
        InterfaceType.MOUNTS_TO,
        "enclosure",
        "pcb",
        (
            "mounting_holes",
            "mounting_standoffs",
            "mounting_hole_positions",
            "mounting_hole_diameter",
        ),
    ),
    (
        InterfaceType.CONNECTS_VIA,
        "pcb",
        "enclosure",
        ("connectors", "cutouts", "usb_c_position", "usb_c_cutout"),
    ),
)


def _contract_pins(contract: Any) -> list[str]:
    names: list[str] = []
    for param in ("led_pwm_pin", "control_input_pins"):
        p = contract.parameter(param)
        if p is None or not p.known:
            continue
        rows = p.value if isinstance(p.value, list) else [p.value]
        for row in rows:
            if isinstance(row, dict):
                names += [str(row[k]) for k in ("pin", "net") if row.get(k)]
    return names


async def _contract_edges(twin: Any, pid: UUID) -> list[InterfaceEdge]:
    """Edges between the items an interface contract links, following its agreement."""
    from twin_core.contracts.checker import agreed_domains
    from twin_core.contracts.records import list_contracts, read_agreements
    from twin_core.items import list_items
    from twin_core.models.interface_contract import ContractKind

    contracts = await list_contracts(twin, pid)
    if not contracts:
        return []
    items = {i.key: i for i in await list_items(twin, project_id=pid)}
    agreements = await read_agreements(twin, pid)
    out: list[InterfaceEdge] = []
    for stored in contracts:
        contract = stored.contract
        mine = agreements.get(stored.key, [])
        version = stored.interface_version
        agreed = set(contract.owning_domains) <= agreed_domains(mine, version)
        last = next((a for a in reversed(mine) if a.interface_version == version), None)
        if contract.kind is ContractKind.CAD_EDA:
            specs = [(t, s, d, list(c)) for t, s, d, c in CONTRACT_CAD_EDA_EDGES]
        elif contract.kind is ContractKind.EDA_FIRMWARE:
            specs = [(InterfaceType.PIN_MAPS_TO, "pinmap", "firmware", _contract_pins(contract))]
        else:
            continue
        for kind, source_role, target_role, coupled in specs:
            source_link, target_link = contract.link(source_role), contract.link(target_role)
            source = items.get(source_link.item_key or "") if source_link else None
            target = items.get(target_link.item_key or "") if target_link else None
            if source is None or target is None:
                continue
            if source.head_node_id is None or target.head_node_id is None:
                continue
            ends = [
                InterfaceEnd(
                    ref=i.key,
                    revision=i.head_revision,
                    domain=domain_for_item_type(i.item_type),
                    item_type=i.item_type,
                    node_id=str(i.head_node_id),
                    label=i.name,
                )
                for i in (source, target)
            ]
            out.append(
                InterfaceEdge(
                    id=f"derived:contract:{stored.key}:{kind.value}",
                    project_id=str(pid),
                    type=kind,
                    source=ends[0],
                    target=ends[1],
                    declared_by=DeclaredBy.HUMAN if agreed else DeclaredBy.INFERRED,
                    confidence=1.0 if agreed else 0.8,
                    status=InterfaceStatus.CONFIRMED if agreed else InterfaceStatus.PROPOSED,
                    coupled_parameters=sorted(set(coupled)),
                    rationale=f"the interface contract {stored.ref} couples them",
                    evidence={"contract": stored.ref, "agreed": agreed},
                    derived=True,
                    decided_by=last.decided_by if agreed and last else None,
                    decided_at=last.at if agreed and last else None,
                )
            )
    return out


async def list_interfaces(
    twin: Any,
    project_id: UUID | str,
    *,
    include_removed: bool = False,
    include_derived: bool = True,
) -> list[InterfaceEdge]:
    """Every interface edge in the project: stored ones, then derived ones."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.interfaces.list") as span:
        edges = await list_stored(twin, pid, include_removed=include_removed)
        if include_derived:
            for loader in (
                _simulation_edges,
                _feature_edges,
                _manufacturing_edges,
                _dfm_edges,
                _contract_edges,
            ):
                try:
                    edges.extend(await loader(twin, pid))
                except Exception as exc:  # noqa: BLE001 - one source must not hide the rest
                    span.record_exception(exc)
                    logger.warning(
                        "interface_edges_derive_failed", source=loader.__name__, error=str(exc)
                    )
        span.set_attribute("interfaces.count", len(edges))
        return edges
