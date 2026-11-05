"""The manufacturing context record in the twin, and how every generator reads it.

One ``manufacturing_context`` item per project (``MFC-PROJECT``) and,
optionally, per component (``MFC-<component>``). Each change is the item's
next revision, so a generated concept can say exactly which context it was
made with (``manufacturing_context_ref: MFC-PROJECT@3``), or, with no record
yet, that it was made with an explicit ``unresolved`` set (3D-RC-011).

**One source.** The three places manufacturing context used to live all feed
this record:

* the Create brief and route (:func:`sync_from_creation`): every brief or
  route save writes a new revision when a mapped value changed;
* a design flow's stated context (:func:`seed_from_flow`) at proposal, and a
  proposal without one reads its confirmed values back from the record
  (:func:`flow_body_from_record`);
* a ``manufacturing_route`` / ``process_ranking`` design decision
  (:func:`sync_from_decision`): ``authority: decision`` when it was recorded
  outside a design-flow run, or an assumption pending the run's gate, made a
  decision when that gate is approved (:func:`promote_run_decisions`).

Conflicts follow :func:`twin_core.manufacturing.context.apply_updates`: the
most recent explicit human statement or approved decision wins, and an
assumption never overrides either.

**Injection.** :func:`manufacturing_context_block` is the one helper every
generator calls. It returns the prompt-safe block, the ref it used, the
unresolved set, the truthful note and the provenance fields each output
records. When no process is known it proposes candidates as assumptions
(never stored), so a first concept is still informed without a questionnaire.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.manufacturing import (
    DFM_STATUS_LABELS,
    UNRESOLVED_REF,
    Authority,
    ContextField,
    DfmStatus,
    FieldConflict,
    ManufacturingContextRecord,
    Provenance,
    apply_updates,
    illustrative_dfm_status,
    manufacturing_note,
    parse_envelope_mm,
    propose_candidates,
    render_prompt_block,
)
from twin_core.manufacturing.context import CONTEXT_FIELDS, NEVER_ASSUMED

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.manufacturing_context")

ITEM_TYPE = "manufacturing_context"
PROJECT_SCOPE_KEY = "MFC-PROJECT"
#: The generators that read the context, as the injection metric names them.
GENERATORS: tuple[str, ...] = (
    "sketch_generation",
    "concept_image",
    "concept_mesh",
    "concept_sketch",
    "project_brief",
    "flow_phase",
)

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _count_write(authority: str, outcome: str) -> None:
    try:
        _collector().record_manufacturing_context_write(authority, outcome)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a write
        logger.debug("manufacturing_context_metric_failed", error=str(exc))


def _count_injection(generator: str, resolution: str) -> None:
    try:
        _collector().record_manufacturing_context_injection(generator, resolution)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a generation
        logger.debug("manufacturing_context_metric_failed", error=str(exc))


class ManufacturingContextError(ValueError):
    """A request that cannot be honoured. ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Keys and reading
# ---------------------------------------------------------------------------


def item_key_for(component_key: str | None) -> str:
    """``MFC-PROJECT`` for the product, ``MFC-<COMPONENT>`` for one component."""
    if not component_key or not component_key.strip():
        return PROJECT_SCOPE_KEY
    slug = re.sub(r"[^A-Z0-9]+", "-", component_key.strip().upper()).strip("-")[:48].strip("-")
    if not slug:
        raise ManufacturingContextError(f"component {component_key!r} has no usable name")
    if slug == "PROJECT":
        slug = "PROJECT-COMPONENT"
    return f"MFC-{slug}"


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ManufacturingContextError(f"project id {project_id!r} is not a UUID") from exc


@dataclass
class StoredContext:
    """One stored revision of a context item."""

    record: ManufacturingContextRecord
    item_key: str
    revision: int
    node_id: str
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.item_key}@{self.revision}"


def _record_from_node(wp: Any) -> ManufacturingContextRecord | None:
    raw = (getattr(wp, "metadata", None) or {}).get("manufacturing_context")
    if not isinstance(raw, dict):
        return None
    try:
        return ManufacturingContextRecord.model_validate(raw)
    except ValueError as exc:
        logger.warning("manufacturing_context_unreadable", node_id=str(wp.id), error=str(exc))
        return None


async def read_context(
    twin: Any, project_id: str | UUID, component_key: str | None = None
) -> StoredContext | None:
    """The current revision of the context, or ``None`` when none was recorded."""
    from twin_core.items import find_item, supports_items

    if twin is None or not supports_items(twin):
        return None
    pid = _pid(project_id)
    key = item_key_for(component_key)
    item = await find_item(twin, key, pid)
    if item is None or item.head_node_id is None:
        return None
    wp = await twin.get_work_product(item.head_node_id)
    if wp is None:
        return None
    record = _record_from_node(wp)
    if record is None:
        return None
    created = getattr(wp, "created_at", None)
    return StoredContext(
        record=record,
        item_key=key,
        revision=int(item.head_revision),
        node_id=str(item.head_node_id),
        created_at=created.isoformat() if created else None,
        metadata=dict(wp.metadata or {}),
    )


async def effective_context(
    twin: Any, project_id: str | UUID, component_key: str | None = None
) -> StoredContext | None:
    """The component's context when it has one, else the product's."""
    if component_key:
        own = await read_context(twin, project_id, component_key)
        if own is not None:
            return own
    return await read_context(twin, project_id, None)


async def context_history(
    twin: Any, project_id: str | UUID, component_key: str | None = None
) -> list[dict[str, Any]]:
    """Every revision of the context, oldest first, with what each one changed."""
    from twin_core.items import find_item, item_history

    pid = _pid(project_id)
    key = item_key_for(component_key)
    item = await find_item(twin, key, pid)
    if item is None:
        raise ManufacturingContextError(f"no manufacturing context {key} in this project", 404)
    out: list[dict[str, Any]] = []
    for rev in await item_history(twin, item):
        wp = await twin.get_work_product(rev.node_id)
        meta = dict(getattr(wp, "metadata", None) or {}) if wp is not None else {}
        record = _record_from_node(wp) if wp is not None else None
        out.append(
            {
                "revision": rev.revision,
                "ref": f"{key}@{rev.revision}",
                "node_id": str(rev.node_id),
                "is_head": rev.is_head,
                "status": rev.status,
                "author": rev.author,
                "change_reason": rev.change_reason,
                "created_at": rev.created_at.isoformat() if rev.created_at else None,
                "source": meta.get("source"),
                "changed_fields": list(meta.get("changed_fields") or []),
                "refused_fields": list(meta.get("refused_fields") or []),
                "stale_fields": list(meta.get("stale_fields") or []),
                "process_change": meta.get("process_change"),
                "record": record.model_dump(mode="json") if record is not None else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _lock(project_id: UUID, key: str) -> asyncio.Lock:
    lock = _locks.get((str(project_id), key))
    if lock is None:
        lock = _locks[(str(project_id), key)] = asyncio.Lock()
    return lock


@dataclass
class WriteOutcome:
    """What one write did."""

    stored: StoredContext | None
    changed: list[str] = field(default_factory=list)
    refused: list[FieldConflict] = field(default_factory=list)
    staled: list[str] = field(default_factory=list)
    process_change: tuple[Any, Any] | None = None
    revision_created: bool = False


def _publish(project_id: UUID, stored: StoredContext, source: str, changed: list[str]) -> None:
    from api_gateway.operations import publisher as operations

    op = f"job:mfc-{str(project_id)[:8]}-{stored.item_key.lower()}-{stored.revision}"
    words = ", ".join(changed[:6]) + ("..." if len(changed) > 6 else "")
    for event, state in (("accepted", "running"), ("completed", "completed")):
        operations.publish(
            str(project_id),
            op,
            event,
            state,
            event_id=f"{op}-{event}",
            domain="manufacturing",
            title="Manufacturing context",
            message=(
                f"Manufacturing context {stored.ref} recorded from {source}: {words}"
                if event == "completed"
                else "Recording the manufacturing context"
            )[:500],
            target_revision=stored.ref if event == "completed" else None,
        )


async def write_context(
    twin: Any,
    project_id: str | UUID,
    updates: dict[str, ContextField],
    *,
    source: str,
    actor: str = "",
    change_reason: str = "",
    component_key: str | None = None,
    allow_clear: bool = False,
    expected_revision: int | None = None,
    dfm_status: DfmStatus | None = None,
    project_backend: Any = None,
    extra_metadata: dict[str, Any] | None = None,
) -> WriteOutcome:
    """Apply ``updates`` under the conflict rule; a new revision when anything changed.

    Always a head write, never a design-flow draft: the record is shared by
    every generator, and an in-run decision is held as an assumption pending
    its gate instead (:func:`sync_from_decision`).
    """
    from api_gateway.twin.item_revisions import finish_definition_revision
    from twin_core.items import ItemRevisionConflictError, plan_revision, supports_items
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    if twin is None or not supports_items(twin):
        raise ManufacturingContextError("this twin cannot keep a manufacturing context", 503)
    pid = _pid(project_id)
    key = item_key_for(component_key)
    async with _lock(pid, key):
        with tracer.start_as_current_span("twin.manufacturing_context.write") as span:
            span.set_attribute("project_id", str(pid))
            span.set_attribute("mfc.item_key", key)
            span.set_attribute("mfc.source", source)
            stored = await read_context(twin, pid, component_key)
            if expected_revision is not None:
                current = stored.revision if stored is not None else 0
                if current != expected_revision:
                    raise ItemRevisionConflictError(
                        f"{key} is at @{current}, not @{expected_revision}; "
                        "re-read the manufacturing context and try again"
                    )
            if stored is not None:
                base = stored.record
            elif component_key:
                # A component starts from the product's context, then differs.
                product = await read_context(twin, pid, None)
                base = (
                    product.record.model_copy(update={"component_key": component_key})
                    if product is not None
                    else ManufacturingContextRecord(
                        project_id=str(pid), component_key=component_key
                    )
                )
            else:
                base = ManufacturingContextRecord(project_id=str(pid))
            merge = apply_updates(base, updates, allow_clear=allow_clear)
            record = merge.record
            if dfm_status is not None and dfm_status is not record.dfm_status:
                record = record.model_copy(update={"dfm_status": dfm_status})
                merge.changed.append("dfm_status")
            for name in merge.changed:
                if name in CONTEXT_FIELDS:
                    _count_write(record.get(name).authority.value, "written")
            for conflict in merge.refused:
                _count_write(conflict.refused_authority.value, "refused")
            span.set_attribute("mfc.changed", len(merge.changed))
            span.set_attribute("mfc.refused", len(merge.refused))
            if not merge.changed:
                for name in updates:
                    if name not in {c.field for c in merge.refused}:
                        _count_write(updates[name].authority.value, "unchanged")
                if merge.refused and stored is not None:
                    logger.info(
                        "manufacturing_context_unchanged_refused",
                        project_id=str(pid),
                        item_ref=stored.ref,
                        refused=[c.field for c in merge.refused],
                    )
                return WriteOutcome(stored=stored, refused=merge.refused)

            name = "Manufacturing context" + (f": {component_key}" if component_key else "")
            plan = await plan_revision(
                twin,
                item_type=ITEM_TYPE,
                name=name,
                project_id=pid,
                author=actor or source,
                item_key=key,
                change_reason=change_reason or f"manufacturing context from {source}",
                run_id=None,
            )
            if plan is None:
                raise ManufacturingContextError(
                    "this twin cannot keep a manufacturing context", 503
                )
            record_json = record.model_dump(mode="json")
            content = json.dumps(record_json, sort_keys=True, separators=(",", ":")).encode()
            content_hash = hashlib.sha256(content).hexdigest()
            metadata: dict[str, Any] = {
                "manufacturing_context": record_json,
                "component_key": component_key,
                "source": source,
                "changed_fields": list(merge.changed),
                "refused_fields": [c.field for c in merge.refused],
                "stale_fields": list(merge.staled),
                "content_sha256": content_hash,
                **(extra_metadata or {}),
            }
            if merge.process_change is not None:
                metadata["process_change"] = {
                    "from": merge.process_change[0],
                    "to": merge.process_change[1],
                }
            metadata.update(plan.stamp())
            now = datetime.now(UTC)
            wp_id = uuid4()
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=name,
                    type=WorkProductType.MANUFACTURING_CONTEXT,
                    domain="manufacturing",
                    file_path="",
                    content_hash=content_hash,
                    format="json",
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by=source,
                    project_id=pid,
                )
            )
            node_id = getattr(created, "id", wp_id)
            result: dict[str, Any] = {}
            await finish_definition_revision(twin, plan, node_id, name=name, result=result)
            if project_backend is not None:
                try:
                    await project_backend.link_work_product(str(pid), str(node_id), name, ITEM_TYPE)
                except Exception as exc:  # noqa: BLE001 - the link is best-effort
                    logger.warning("manufacturing_context_project_link_failed", error=str(exc))
            out = StoredContext(
                record=record,
                item_key=key,
                revision=int(result.get("revision") or plan.revision),
                node_id=str(node_id),
                created_at=now.isoformat(),
                metadata=metadata,
            )
            span.set_attribute("mfc.item_ref", out.ref)
            logger.info(
                "manufacturing_context_written",
                project_id=str(pid),
                item_ref=out.ref,
                source=source,
                changed=merge.changed,
                refused=[c.field for c in merge.refused],
                staled=merge.staled,
                process_change=metadata.get("process_change"),
                unknowns=record.unknowns,
            )
            _publish(pid, out, source, merge.changed)
            return WriteOutcome(
                stored=out,
                changed=merge.changed,
                refused=merge.refused,
                staled=merge.staled,
                process_change=merge.process_change,
                revision_created=True,
            )


def stated(value: Any, *, source: str, actor: str = "", ref: str | None = None) -> ContextField:
    """A ``user_stated`` field."""
    return ContextField(
        value=value,
        authority=Authority.USER_STATED,
        provenance=Provenance(source=source, actor=actor, ref=ref),
    )


def cleared(*, source: str, actor: str = "") -> ContextField:
    """A person cleared it: ``unknown``."""
    return ContextField(
        authority=Authority.UNKNOWN, provenance=Provenance(source=source, actor=actor)
    )


# ---------------------------------------------------------------------------
# One source: Create
# ---------------------------------------------------------------------------

_CURRENCY = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY"}
_CODES = ("GBP", "USD", "EUR", "JPY", "CAD", "AUD", "CHF", "CNY", "INR", "SEK", "NOK", "DKK")


def parse_cost(text: str) -> dict[str, Any]:
    """``{"amount", "currency", "text"}`` from what a person typed. Never guesses a currency."""
    raw = (text or "").strip()
    currency = ""
    for symbol, code in _CURRENCY.items():
        if symbol in raw:
            currency = code
            break
    if not currency:
        upper = raw.upper()
        currency = next((c for c in _CODES if re.search(rf"\b{c}\b", upper)), "")
    match = re.search(r"(\d+(?:[.,]\d+)?)", raw.replace(",", ""))
    amount = float(match.group(1)) if match and currency else None
    return {"amount": amount, "currency": currency, "text": raw[:200]}


def parse_quantity(text: str) -> int | str:
    raw = (text or "").strip()
    match = re.fullmatch(r"\D{0,12}?(\d{1,7})\D{0,20}", raw)
    if match:
        value = int(match.group(1))
        if value > 0:
            return value
    return raw[:200]


def _split_list(text: str) -> list[str]:
    parts = re.split(r",|;|/|\bor\b|\band\b", text or "")
    return [p.strip()[:120] for p in parts if p.strip()][:10]


#: Create brief keys -> context fields.
_BRIEF_FIELDS: dict[str, str] = {
    "intended_use": "intended_use",
    "size_and_fit": "dimensional_envelope",
    "appearance": "finish",
    "materials": "material_candidates",
    "manufacturing_budget": "target_cost",
    "quantity": "quantity",
}


def updates_from_creation(
    creation: Any, current: ManufacturingContextRecord | None, *, surface: str, actor: str = ""
) -> dict[str, ContextField]:
    """The context fields a Create record states, with their authority.

    A brief item a person confirmed is ``user_stated``; one that is only
    assumed, or written by an agent surface, is an ``assumption`` (and is
    dropped for a field that is never assumed). A Create value that went
    missing clears the field only if the field came from Create.
    """
    person = surface != "agent"
    updates: dict[str, ContextField] = {}

    def from_create(name: str, source: str) -> bool:
        if current is None:
            return False
        prov = current.get(name).provenance
        return prov is not None and prov.source == source and current.get(name).known

    items = getattr(getattr(creation, "brief", None), "items", {}) or {}
    for brief_key, name in _BRIEF_FIELDS.items():
        item = items.get(brief_key)
        text = str(getattr(item, "value", "") or "").strip() if item is not None else ""
        status = str(getattr(item, "status", "missing") or "missing") if item is not None else ""
        if not text or status == "missing":
            if from_create(name, "create_brief"):
                updates[name] = cleared(source="create_brief", actor=actor)
            continue
        authority = (
            Authority.USER_STATED
            if status == "confirmed" and person and getattr(item, "source", "user") == "user"
            else Authority.ASSUMPTION
        )
        if authority is Authority.ASSUMPTION and name in NEVER_ASSUMED:
            continue
        value: Any = text[:500]
        if name == "material_candidates":
            value = _split_list(text)
        elif name == "target_cost":
            value = parse_cost(text)
        elif name == "quantity":
            value = parse_quantity(text)
        updates[name] = ContextField(
            value=value,
            authority=authority,
            provenance=Provenance(source="create_brief", actor=actor, ref=brief_key),
        )

    route = getattr(creation, "route", None)
    route_authority = Authority.USER_STATED if person else Authority.ASSUMPTION
    for name, attr in (
        ("route", "route"),
        ("selected_process", "process"),
        ("selected_material", "material"),
    ):
        text = str(getattr(route, attr, "") or "").strip() if route is not None else ""
        if not text:
            if from_create(name, "create_route"):
                updates[name] = cleared(source="create_route", actor=actor)
            continue
        updates[name] = ContextField(
            value=text[:200],
            authority=route_authority,
            provenance=Provenance(source="create_route", actor=actor, ref=f"route.{attr}"),
        )

    # Machine, supplier and the person's making context: stated only (DFM-014).
    profile: dict[str, str] = {}
    if route is not None and person:
        if getattr(route, "machine_id", None):
            profile["machine_id"] = str(route.machine_id)[:200]
        if getattr(route, "supplier", ""):
            profile["supplier"] = str(route.supplier)[:200]
    making = items.get("making_context")
    if (
        making is not None
        and person
        and str(getattr(making, "status", "")) == "confirmed"
        and str(getattr(making, "value", "") or "").strip()
    ):
        profile["description"] = str(making.value).strip()[:300]
    if profile:
        updates["machine_or_supplier_profile"] = ContextField(
            value=profile,
            authority=Authority.USER_STATED,
            provenance=Provenance(source="create_route", actor=actor),
        )
    elif from_create("machine_or_supplier_profile", "create_route"):
        updates["machine_or_supplier_profile"] = cleared(source="create_route", actor=actor)
    return updates


async def sync_from_creation(
    twin: Any, creation: Any, *, surface: str, actor: str = "", project_backend: Any = None
) -> WriteOutcome | None:
    """Write a new context revision when a Create brief or route save changed it. Never raises."""
    try:
        pid = creation.project_id
        current = await read_context(twin, pid)
        updates = updates_from_creation(
            creation, current.record if current else None, surface=surface, actor=actor
        )
        if not updates:
            return None
        clears = any(u.authority is Authority.UNKNOWN for u in updates.values())
        return await write_context(
            twin,
            pid,
            updates,
            source="create",
            actor=actor,
            change_reason="Create brief or route changed",
            allow_clear=clears,
            project_backend=project_backend,
        )
    except Exception as exc:  # noqa: BLE001 - a Create save never fails on the context
        _count_write("none", "failed")
        logger.warning(
            "manufacturing_context_sync_failed",
            source="create",
            project_id=str(getattr(creation, "project_id", "")),
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# One source: decisions
# ---------------------------------------------------------------------------

#: Decision metadata key naming the run a route decision was recorded in.
DECISION_RUN_KEY = "recorded_in_run"


def updates_from_decision(
    route: dict[str, Any] | None,
    ranking: dict[str, Any] | None,
    *,
    decision_node_id: str,
    run_id: str | None,
    actor: str = "",
) -> dict[str, ContextField]:
    """Context fields a ``manufacturing_route`` / ``process_ranking`` decision sets."""
    authority = Authority.ASSUMPTION if run_id else Authority.DECISION
    pending = run_id or None
    prov = Provenance(source="decision", actor=actor, ref=decision_node_id)
    updates: dict[str, ContextField] = {}

    def put(name: str, value: Any) -> None:
        if value in (None, "", []):
            return
        updates[name] = ContextField(
            value=value, authority=authority, provenance=prov, pending_gate=pending
        )

    if isinstance(route, dict) and not route.get("deferred"):
        put("route", route.get("route"))
        put("selected_process", route.get("process"))
        put("selected_material", route.get("material_class"))
    if isinstance(ranking, dict):
        names = [
            str(c.get("process"))
            for c in ranking.get("candidates") or []
            if isinstance(c, dict) and c.get("process")
        ]
        put("process_candidates", names[:10])
    return updates


async def sync_from_decision(
    twin: Any,
    project_id: str | None,
    *,
    decision_node_id: str,
    route: dict[str, Any] | None,
    ranking: dict[str, Any] | None,
    run_id: str | None,
    actor: str = "",
) -> WriteOutcome | None:
    """Record a route decision in the context. Never raises.

    Outside a run, the decision is the decision (``authority: decision``).
    Inside one, it is the run's proposal until its gate is approved: an
    assumption with ``pending_gate``, which cannot override a stated value.
    """
    if not project_id:
        return None
    try:
        updates = updates_from_decision(
            route, ranking, decision_node_id=decision_node_id, run_id=run_id, actor=actor
        )
        if not updates:
            return None
        return await write_context(
            twin,
            project_id,
            updates,
            source="decision",
            actor=actor,
            change_reason=(
                f"route decision {decision_node_id}"
                + (f", pending the gate of run {run_id}" if run_id else "")
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a decision is recorded whatever the context does
        _count_write("none", "failed")
        logger.warning(
            "manufacturing_context_sync_failed",
            source="decision",
            project_id=project_id,
            error=str(exc),
        )
        return None


#: MFC revision metadata naming the decision a gate approval promoted.
PROMOTED_KEY = "promoted_decision"


async def _already_promoted(twin: Any, project_id: str, decision_id: str) -> bool:
    from twin_core.items import find_item, item_history

    item = await find_item(twin, PROJECT_SCOPE_KEY, _pid(project_id))
    if item is None:
        return False
    for rev in await item_history(twin, item):
        wp = await twin.get_work_product(rev.node_id)
        if wp is not None and (wp.metadata or {}).get(PROMOTED_KEY) == decision_id:
            return True
    return False


async def promote_run_decisions(
    twin: Any, project_id: str | None, run_id: str, *, decided_by: str = ""
) -> WriteOutcome | None:
    """A run's gate was approved: its route decision becomes a decision. Never raises."""
    if not project_id or not run_id:
        return None
    try:
        from twin_core.models.enums import WorkProductType

        decisions = [
            wp
            for wp in await twin.list_work_products(
                work_product_type=WorkProductType.DESIGN_DECISION, project_id=_pid(project_id)
            )
            if (wp.metadata or {}).get(DECISION_RUN_KEY) == run_id
            and (wp.metadata or {}).get("manufacturing_route") is not None
        ]
        if not decisions:
            return None
        newest = max(decisions, key=lambda wp: wp.created_at)
        if await _already_promoted(twin, project_id, str(newest.id)):
            # Promoted at an earlier gate of this run. Promoting again would
            # override whatever a person has stated since.
            return None
        meta = newest.metadata or {}
        updates = updates_from_decision(
            meta.get("manufacturing_route"),
            meta.get("process_ranking"),
            decision_node_id=str(newest.id),
            run_id=None,
            actor=decided_by,
        )
        if not updates:
            return None
        return await write_context(
            twin,
            project_id,
            updates,
            source="decision",
            actor=decided_by,
            change_reason=f"route decision {newest.id} approved at the gate of run {run_id}",
            extra_metadata={PROMOTED_KEY: str(newest.id)},
        )
    except Exception as exc:  # noqa: BLE001 - an approval never fails on the context
        _count_write("none", "failed")
        logger.warning(
            "manufacturing_context_promote_failed",
            project_id=project_id,
            run_id=run_id,
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# One source: design flows
# ---------------------------------------------------------------------------


def updates_from_flow(body: Any, *, actor: str = "") -> dict[str, ContextField]:
    """Context fields a flow proposal's stated manufacturing context sets (``user_stated``)."""
    updates: dict[str, ContextField] = {}
    if body is None:
        return updates
    src = "flow_proposal"
    route = getattr(body, "route", None)
    route_value = getattr(route, "value", route)
    if route_value and route_value != "undecided":
        updates["route"] = stated(str(route_value), source=src, actor=actor)
    processes = [p.strip() for p in getattr(body, "processes", []) or [] if p.strip()]
    if len(processes) == 1:
        updates["selected_process"] = stated(processes[0], source=src, actor=actor)
    elif processes:
        updates["process_candidates"] = stated(processes[:10], source=src, actor=actor)
    stock = [s.strip() for s in getattr(body, "stockMaterials", []) or [] if s.strip()]
    if stock:
        updates["material_candidates"] = stated(stock[:10], source=src, actor=actor)
    quantity = getattr(body, "productionQuantity", None)
    if isinstance(quantity, int) and quantity > 0:
        updates["quantity"] = stated(quantity, source=src, actor=actor)
    machines = [m.strip() for m in getattr(body, "machines", []) or [] if m.strip()]
    machine_ids = [m.strip() for m in getattr(body, "machineIds", []) or [] if m.strip()]
    profile: dict[str, str] = {}
    if machines:
        profile["machine"] = "; ".join(machines)[:300]
    if machine_ids:
        profile["machine_id"] = ", ".join(machine_ids)[:200]
    if profile:
        updates["machine_or_supplier_profile"] = stated(profile, source=src, actor=actor)
    return updates


async def seed_from_flow(
    twin: Any, project_id: str | None, body: Any, *, actor: str = ""
) -> WriteOutcome | None:
    """A flow proposal's stated context, recorded as ``user_stated``. Never raises."""
    if not project_id or body is None:
        return None
    try:
        updates = updates_from_flow(body, actor=actor)
        if not updates:
            return None
        return await write_context(
            twin,
            project_id,
            updates,
            source="flow_proposal",
            actor=actor,
            change_reason="manufacturing context stated with a design flow proposal",
        )
    except Exception as exc:  # noqa: BLE001 - a proposal never fails on the context
        _count_write("none", "failed")
        logger.warning(
            "manufacturing_context_sync_failed",
            source="flow_proposal",
            project_id=project_id,
            error=str(exc),
        )
        return None


_ROUTE_TO_FLOW = {
    "home": "in_house",
    "workshop": "in_house",
    "in_house": "in_house",
    "external": "vendor",
    "vendor": "vendor",
}


def flow_body_from_record(record: ManufacturingContextRecord) -> dict[str, Any] | None:
    """A flow's ``manufacturingContext`` from the record's confirmed values only.

    A flow treats its context as stated by the requester, so an assumption
    never goes in here; the phases see assumptions, labelled, in the context
    block instead. ``None`` when nothing is confirmed.
    """

    def confirmed(name: str) -> Any:
        entry = record.get(name)
        return entry.value if entry.confirmed else None

    out: dict[str, Any] = {}
    route = confirmed("route")
    if isinstance(route, str) and route.strip().lower() in _ROUTE_TO_FLOW:
        out["route"] = _ROUTE_TO_FLOW[route.strip().lower()]
    process = confirmed("selected_process")
    candidates = confirmed("process_candidates")
    if process:
        out["processes"] = [str(process)]
    elif isinstance(candidates, list) and candidates:
        out["processes"] = [str(c) for c in candidates]
    material = confirmed("selected_material")
    materials = confirmed("material_candidates")
    if material:
        out["stockMaterials"] = [str(material)]
    elif isinstance(materials, list) and materials:
        out["stockMaterials"] = [str(m) for m in materials]
    quantity = confirmed("quantity")
    if isinstance(quantity, int) and quantity > 0:
        out["productionQuantity"] = quantity
    profile = confirmed("machine_or_supplier_profile")
    if isinstance(profile, dict) and profile.get("machine"):
        out["machines"] = [str(profile["machine"])]
    return out or None


# ---------------------------------------------------------------------------
# Proposals (written nowhere until accepted)
# ---------------------------------------------------------------------------


class CandidateView(BaseModel):
    process: str
    why: str
    materials: list[str] = Field(default_factory=list)
    route_hint: str | None = None
    authority: str = Authority.ASSUMPTION.value
    #: The ``rank_manufacturing_processes`` verdict, when it ran.
    verdict: str | None = None
    reasons: list[str] = Field(default_factory=list)
    #: Available machine-library instances that offer this process.
    library_machines: list[str] = Field(default_factory=list)
    #: Material-library entries matching its typical materials.
    library_materials: list[str] = Field(default_factory=list)


class ProposalView(BaseModel):
    """Candidate processes and materials. Proposals only: nothing is written."""

    project_id: str
    component_key: str | None = None
    based_on: str
    process_known: bool
    candidates: list[CandidateView] = Field(default_factory=list)
    unknown_inputs: list[str] = Field(default_factory=list)
    note: str = (
        "Assumptions proposed by MetaForge. Nothing is recorded until a person states a "
        "process or material (PUT the manufacturing context)."
    )


def _library_machines_for(process: str) -> list[str]:
    try:
        from api_gateway.library.routes import get_machine_library
        from twin_core.library.machines import MachineInstance

        library = get_machine_library()
        words = {w for w in re.findall(r"[a-z]+", process.lower()) if len(w) > 2}
        out = []
        for inst in library.list(layer="instance"):
            if not isinstance(inst, MachineInstance) or inst.status != "available":
                continue
            offered = " ".join(inst.processes).lower()
            if any(w in offered for w in words):
                out.append(inst.name or inst.id)
        return out[:5]
    except Exception as exc:  # noqa: BLE001 - the library is optional here
        logger.debug("manufacturing_context_machine_library_unavailable", error=str(exc))
        return []


def _library_materials_for(materials: tuple[str, ...]) -> list[str]:
    try:
        from api_gateway.library.catalog_routes import get_catalog

        entries = get_catalog().list("material")
        out: list[str] = []
        for wanted in materials:
            needle = wanted.lower().split(" (")[0]
            for entry in entries:
                names = [entry.name.lower(), entry.title.lower(), entry.designation.lower()]
                if any(needle and n and (needle in n or n in needle) for n in names):
                    out.append(entry.id)
                    break
        return out
    except Exception as exc:  # noqa: BLE001 - the catalog is optional here
        logger.debug("manufacturing_context_catalog_unavailable", error=str(exc))
        return []


async def _rank(
    candidates: list[CandidateView], record: ManufacturingContextRecord | None
) -> list[str]:
    """Annotate ``candidates`` with the rank skill's verdicts; returns unknown inputs."""
    try:
        from uuid import uuid4 as _uuid4

        from domain_agents.mechanical.skills.rank_manufacturing_processes.handler import (
            RankManufacturingProcessesHandler,
        )
        from domain_agents.mechanical.skills.rank_manufacturing_processes.schema import (
            CandidateProcess,
            RankManufacturingProcessesInput,
        )
        from skill_registry.skill_base import SkillContext

        quantity = record.quantity.value if record and record.quantity.confirmed else None
        material = (
            str(record.selected_material.value)
            if record and record.selected_material.confirmed
            else None
        )
        envelope = (
            parse_envelope_mm(record.dimensional_envelope.value)
            if record and record.dimensional_envelope.known
            else None
        )
        data = RankManufacturingProcessesInput(
            quantity=quantity if isinstance(quantity, int) and quantity > 0 else None,
            material_class=material,
            part_envelope_mm=envelope,
            candidates=[
                CandidateProcess(
                    process=c.process,
                    available=True if c.library_machines else None,
                    materials=list(c.materials),
                )
                for c in candidates
            ],
        )
        handler = RankManufacturingProcessesHandler(
            SkillContext(
                twin=None,
                mcp=None,  # type: ignore[arg-type]
                logger=structlog.get_logger("rank_manufacturing_processes"),
                session_id=_uuid4(),
                domain="mechanical",
            )
        )
        output = await handler.execute(data)
        by_process = {r.process: r for r in output.ranked}
        for c in candidates:
            ranked = by_process.get(c.process)
            if ranked is not None:
                c.verdict = ranked.verdict
                c.reasons = list(ranked.reasons)
        # A candidate the rank skill shows does not fit goes last, never silently dropped.
        candidates.sort(key=lambda c: c.verdict == "does_not_fit")
        return list(output.unknown_inputs)
    except Exception as exc:  # noqa: BLE001 - proposals stand without the ranking
        logger.warning("manufacturing_context_rank_failed", error=str(exc))
        return []


async def propose(
    twin: Any,
    project_id: str | UUID,
    component_key: str | None = None,
    *,
    text: str = "",
) -> ProposalView:
    """Candidate processes and materials from what is known. Writes nothing."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.manufacturing_context.propose") as span:
        stored = await effective_context(twin, pid, component_key)
        record = stored.record if stored is not None else None
        words = [text]
        if record is not None:
            for name in ("intended_use", "finish", "dimensional_envelope", "assembly"):
                if record.get(name).known:
                    words.append(str(record.get(name).value))
        if component_key:
            words.append(component_key)
        envelope = (
            parse_envelope_mm(record.dimensional_envelope.value)
            if record is not None and record.dimensional_envelope.known
            else None
        )
        quantity = record.quantity.value if record is not None else None
        picks = propose_candidates(
            text=" ".join(w for w in words if w),
            envelope_mm=envelope,
            quantity=quantity if isinstance(quantity, int) else None,
        )
        candidates = [
            CandidateView(
                process=p.process,
                why=p.why,
                materials=list(p.materials),
                route_hint=p.route_hint,
                library_machines=_library_machines_for(p.process),
                library_materials=_library_materials_for(p.materials),
            )
            for p in picks
        ]
        unknown_inputs = await _rank(candidates, record)
        span.set_attribute("mfc.candidates", len(candidates))
        logger.info(
            "manufacturing_context_proposed",
            project_id=str(pid),
            component_key=component_key,
            candidates=[c.process for c in candidates],
        )
        return ProposalView(
            project_id=str(pid),
            component_key=component_key,
            based_on=stored.ref if stored is not None else UNRESOLVED_REF,
            process_known=bool(record is not None and record.selected_process.known),
            candidates=candidates,
            unknown_inputs=unknown_inputs,
        )


def assumed_from_proposals(
    record: ManufacturingContextRecord | None, project_id: str, picks: list[Any]
) -> ManufacturingContextRecord:
    """``record`` with proposed candidates filled in as assumptions where nothing is known.

    For the prompt block only; never stored. Candidates are process-specific
    assumptions, so they read as stale the moment a process is stated.
    """
    base = record or ManufacturingContextRecord(project_id=project_id)
    if not picks:
        return base
    prov = Provenance(source="proposal", actor="metaforge")
    updates: dict[str, ContextField] = {}
    if not base.selected_process.known and not base.process_candidates.known:
        updates["process_candidates"] = ContextField(
            value=[p.process for p in picks[:3]],
            authority=Authority.ASSUMPTION,
            provenance=prov,
        )
    if not base.selected_material.known and not base.material_candidates.known:
        materials = list(dict.fromkeys(m for p in picks[:2] for m in p.materials))[:3]
        if materials:
            updates["material_candidates"] = ContextField(
                value=materials,
                authority=Authority.ASSUMPTION,
                provenance=prov,
                process_specific=True,
            )
    return apply_updates(base, updates).record if updates else base


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------


@dataclass
class ContextBlock:
    """What a generator is given and what its output records."""

    text: str
    ref: str
    unresolved: list[str]
    speculative: bool
    note: str
    dfm_status: str
    informed_by: str = ""
    record: ManufacturingContextRecord | None = None

    def provenance(self) -> dict[str, Any]:
        """The fields every generated output carries (3D-RC-011, DFM-004, DFM-013)."""
        return {
            "manufacturing_context_ref": self.ref,
            "manufacturing_context_unresolved": list(self.unresolved),
            "manufacturing_context_applied": not self.speculative,
            "speculative": self.speculative,
            "manufacturing_note": self.note,
            "dfm_status": self.dfm_status,
            "dfm_status_label": DFM_STATUS_LABELS[DfmStatus(self.dfm_status)],
        }


def _seed_text(record: ManufacturingContextRecord | None, extra: str) -> str:
    words = [extra]
    if record is not None:
        for name in ("intended_use", "finish", "dimensional_envelope"):
            if record.get(name).known:
                words.append(str(record.get(name).value))
    return " ".join(w for w in words if w)


async def manufacturing_context_block(
    project_id: str | UUID | None,
    component_key: str | None = None,
    *,
    twin: Any = None,
    generator: str = "unknown",
    speculative: bool = False,
    hint: str = "",
    dfm_status: DfmStatus | str | None = None,
) -> ContextBlock:
    """The manufacturing context block for one generation. Never raises.

    With a recorded context the block names its revision (``MFC-...@n``);
    without one the ref is ``unresolved`` and the unresolved set is recorded.
    Where no process is known, candidates proposed from ``hint`` and the
    record are added as assumptions (not stored). ``speculative`` records the
    context but tells the model it does not constrain the output. The
    output's DFM status is ``not_assessed`` unless a heuristic check ran.
    """
    status = illustrative_dfm_status(dfm_status).value
    with tracer.start_as_current_span("twin.manufacturing_context.block") as span:
        span.set_attribute("mfc.generator", generator)
        span.set_attribute("mfc.speculative", speculative)
        stored: StoredContext | None = None
        if project_id is not None:
            try:
                if twin is None:
                    from api_gateway.twin.routes import get_twin

                    twin = get_twin()
                stored = await effective_context(twin, project_id, component_key)
            except Exception as exc:  # noqa: BLE001 - an unreadable record reads as unresolved
                span.record_exception(exc)
                logger.warning(
                    "manufacturing_context_read_failed",
                    project_id=str(project_id),
                    generator=generator,
                    error=str(exc),
                )
        record = stored.record if stored is not None else None
        ref = stored.ref if stored is not None else UNRESOLVED_REF
        effective = record
        if project_id is not None and (record is None or not record.selected_process.known):
            picks = propose_candidates(text=_seed_text(record, hint))
            effective = assumed_from_proposals(record, str(project_id), picks)
        if effective is not None:
            unresolved = effective.unresolved()
        else:
            unresolved = [
                "process",
                "material",
                "machine",
                "supplier",
                "tolerance",
                "quantity",
                "target_cost",
                "lead_time",
            ]
        text = render_prompt_block(effective, ref, speculative=speculative)
        note = manufacturing_note(effective, speculative=speculative, dfm_status=status)
        resolution = (
            "speculative" if speculative else ("record" if stored is not None else "unresolved")
        )
        span.set_attribute("mfc.ref", ref)
        span.set_attribute("mfc.resolution", resolution)
        _count_injection(generator, resolution)
        logger.info(
            "manufacturing_context_injected",
            project_id=str(project_id) if project_id else None,
            generator=generator,
            ref=ref,
            resolution=resolution,
            unresolved=len(unresolved),
        )
        return ContextBlock(
            text=text,
            ref=ref,
            unresolved=unresolved,
            speculative=speculative,
            note=note,
            dfm_status=status,
            record=effective,
        )


# ---------------------------------------------------------------------------
# The API view
# ---------------------------------------------------------------------------


class ContextView(BaseModel):
    """The context as the API returns it."""

    project_id: str
    component_key: str | None = None
    item_key: str
    #: ``None`` when nothing is recorded yet.
    revision: int | None = None
    #: ``MFC-...@n``, or ``unresolved`` when nothing is recorded yet.
    ref: str
    node_id: str | None = None
    recorded: bool
    #: True when this component has no record of its own and shows the product's.
    inherited: bool = False
    record: ManufacturingContextRecord
    dfm_status: str
    dfm_status_label: str
    #: What a concept made now would say (section 10).
    manufacturing_note: str
    #: The block a generator would be given now.
    prompt_block: str
    created_at: str | None = None
    #: Set on a write: what changed, what the conflict rule refused, what went stale.
    changed: list[str] = Field(default_factory=list)
    refused: list[FieldConflict] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)


def view_of(
    project_id: str | UUID,
    component_key: str | None,
    stored: StoredContext | None,
    *,
    outcome: WriteOutcome | None = None,
) -> ContextView:
    pid = str(_pid(project_id))
    record = stored.record if stored is not None else ManufacturingContextRecord(project_id=pid)
    ref = stored.ref if stored is not None else UNRESOLVED_REF
    status = record.dfm_status
    return ContextView(
        project_id=pid,
        component_key=component_key,
        item_key=item_key_for(component_key),
        revision=stored.revision if stored is not None else None,
        ref=ref,
        node_id=stored.node_id if stored is not None else None,
        recorded=stored is not None,
        inherited=bool(
            component_key and stored is not None and stored.item_key != item_key_for(component_key)
        ),
        record=record,
        dfm_status=status.value,
        dfm_status_label=DFM_STATUS_LABELS[status],
        manufacturing_note=manufacturing_note(record, dfm_status=illustrative_dfm_status(status)),
        prompt_block=render_prompt_block(record if stored else None, ref),
        created_at=stored.created_at if stored is not None else None,
        changed=list(outcome.changed) if outcome else [],
        refused=list(outcome.refused) if outcome else [],
        stale=list(outcome.staled) if outcome else [],
    )
