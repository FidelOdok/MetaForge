"""Recorder-side glue for item revisions (FORGE-523).

Every definition recorder (geometry, constraint set, engineering entity,
BOM, component selection) does the same three things around its own node
creation; this module is that shared shape so each recorder adds a few lines,
not a copy of the resolution rules (which live in ``twin_core.items``).

The author and run come from the MCP call context when there is one
(``actor_id``, and ``run_id`` from a design-flow worker call), so no tool
grows a required argument for them.

FORGE-525: the same ``run_id`` makes a write a draft in the run's change set
(HEAD moves only when the run's gate approves), and makes the run's own reads
of ``twin.item_history`` and ``twin.get_node`` by ``item_key`` see its drafts.

FORGE-524: so do the phase's deliverable slots (``item_slots``). A definition
write with no explicit ``item_key``/``supersedes`` inside a phase that declares
slots of its type lands on the matching slot's item, whatever the model named
it. A write that matches none of them is still recorded, as a new item, but
flagged ``undeclared_item`` (node metadata, tool result, structlog, counter)
so the gate lists it for the reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.items import (
    RevisionPlan,
    commit_revision,
    family_of,
    find_item,
    own_draft,
    plan_revision,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.item_revisions")

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-flow-slots")
    return _metrics


def _call_context() -> Any:
    try:
        from mcp_core.context import current_context
    except ImportError:  # pragma: no cover -- mcp_core ships with the gateway
        return None
    return current_context()


def revision_author(default: str) -> str:
    """The calling actor, or ``default`` (the recording tool) when unattributed."""
    ctx = _call_context()
    actor = getattr(ctx, "actor_id", None)
    if isinstance(actor, str) and actor and actor != "system:unattributed":
        return actor
    return default


def revision_run_id(explicit: str | None = None) -> str | None:
    """An explicit run id, else the design-flow run of the current MCP call."""
    if explicit:
        return explicit
    ctx = _call_context()
    run_id = getattr(ctx, "run_id", None)
    return run_id if isinstance(run_id, str) and run_id else None


def revision_phase() -> str | None:
    """The design-flow phase of the current MCP call, if any (FORGE-525)."""
    ctx = _call_context()
    phase = getattr(ctx, "phase", None)
    return phase if isinstance(phase, str) and phase else None


def phase_slots() -> tuple[Any, ...]:
    """The current design-flow phase's deliverable slots, from the call context."""
    ctx = _call_context()
    slots = getattr(ctx, "item_slots", None)
    return tuple(slots) if slots else ()


@dataclass
class SlottedRevisionPlan(RevisionPlan):
    """A :class:`RevisionPlan` made inside a phase that declares slots (FORGE-524)."""

    #: The slot the write landed on, or ``None`` when it matched none.
    slot_key: str | None = None
    #: How the slot was chosen (``slots.match_slot``'s ``how``), or why not.
    slot_match: str = ""
    undeclared: bool = False
    declared_keys: tuple[str, ...] = ()

    @classmethod
    def wrap(cls, plan: RevisionPlan, **extra: Any) -> SlottedRevisionPlan:
        base = {f.name: getattr(plan, f.name) for f in fields(RevisionPlan)}
        return cls(**base, **extra)

    def stamp(self) -> dict[str, Any]:
        out = super().stamp()
        if self.undeclared:
            out["undeclared_item"] = True
            out["undeclared_phase"] = self.phase or revision_phase()
            out["declared_item_keys"] = list(self.declared_keys)
        elif self.slot_key:
            out["flow_slot_key"] = self.slot_key
        return out

    def result_fields(self) -> dict[str, Any]:
        out = super().result_fields()
        if self.undeclared:
            out["undeclared_item"] = True
            out["undeclared_item_note"] = (
                f"{self.key} is not one of this phase's declared items "
                f"({', '.join(self.declared_keys)}). It was recorded as a separate item "
                "and is listed for the gate reviewer. If it is one of those items, "
                "write it again with that item_key."
            )
        elif self.slot_key:
            out["flow_slot_key"] = self.slot_key
        return out


async def _slot_taken_this_run(
    twin: Any, key: str, project_id: Any, name: str, run_id: str | None
) -> bool:
    """True when this run already wrote the slot's item under a different name.

    The one-slot fallback assumes a differently named write is the same part
    renamed. Within a single run that assumption is wrong more often than
    not: a second name in the same run is a second part. A run's writes are
    drafts (FORGE-525) and HEAD does not move until the gate, so the run's own
    draft is what this compares against.
    """
    if not run_id or project_id is None:
        return False
    try:
        item = await find_item(twin, key, project_id)
        entry = own_draft(item, run_id) if item is not None else None
        if entry is None or not entry.get("node_id"):
            return False
        node = await twin.graph.get_node(UUID(str(entry["node_id"])))
    except Exception as exc:  # noqa: BLE001 -- the guard is advisory
        logger.warning("flow_slot_run_check_failed", item_key=key, error=str(exc))
        return False
    drafted = next(
        (
            v
            for v in (getattr(node, a, None) for a in ("name", "title", "part_number"))
            if isinstance(v, str) and v
        ),
        "",
    )
    return drafted.strip().lower() != (name or "").strip().lower()


async def _resolve_slot(
    twin: Any,
    slots: tuple[Any, ...],
    *,
    item_type: str,
    name: str,
    project_id: Any,
    run_id: str | None,
) -> tuple[str | None, str]:
    """``(item_key, how)`` for a write with no explicit key, or ``(None, why_not)``."""
    from orchestrator.design_flow.slots import match_slot

    match = match_slot(slots, item_type, name)
    if match.slot is None:
        return None, match.how
    key = str(match.slot.item_key)
    if match.how == "only" and await _slot_taken_this_run(twin, key, project_id, name, run_id):
        return None, "taken_this_run"
    return key, match.how


async def plan_definition_revision(
    twin: Any,
    *,
    item_type: str,
    name: str,
    project_id: str | UUID | None,
    default_author: str,
    item_key: str | None = None,
    supersedes: str | None = None,
    change_reason: str | None = None,
    run_id: str | None = None,
    legacy_lookup: Any = None,
) -> RevisionPlan | None:
    """``twin_core.items.plan_revision`` with author, run and slot filled from context.

    An explicit ``item_key`` or ``supersedes`` always wins. Otherwise, inside
    a phase that declares slots of this type, the write is pointed at the
    matching slot's item (FORGE-524).
    """
    run = revision_run_id(run_id)
    slots = phase_slots()
    family_slots = tuple(s for s in slots if s.item_type in family_of(item_type))
    slot_key: str | None = None
    how = ""
    if family_slots and not item_key and not supersedes and project_id is not None:
        with tracer.start_as_current_span("twin.items.resolve_slot") as span:
            span.set_attribute("item.type", item_type)
            slot_key, how = await _resolve_slot(
                twin,
                family_slots,
                item_type=item_type,
                name=name,
                project_id=project_id,
                run_id=run,
            )
            span.set_attribute("flow.slot_match", how)
            if slot_key:
                span.set_attribute("item.key", slot_key)
                item_key = slot_key
    plan = await plan_revision(
        twin,
        item_type=item_type,
        name=name,
        project_id=project_id,
        author=revision_author(default_author),
        item_key=item_key,
        supersedes=supersedes,
        change_reason=change_reason,
        run_id=run,
        legacy_lookup=legacy_lookup,
        phase=revision_phase(),
    )
    if plan is None or not family_slots:
        return plan
    declared = tuple(dict.fromkeys(str(s.item_key) for s in family_slots))
    undeclared = plan.key not in declared
    phase = getattr(_call_context(), "phase", None)
    if undeclared:
        logger.warning(
            "flow_undeclared_item",
            item_key=plan.key,
            item_type=item_type,
            name=name,
            phase=phase,
            run_id=run,
            declared=list(declared),
            why=how or "explicit item_key/supersedes outside the declared items",
        )
    else:
        logger.info(
            "flow_slot_resolved",
            item_key=plan.key,
            item_type=item_type,
            name=name,
            phase=phase,
            run_id=run,
            how=how or "explicit",
        )
    _collector().record_flow_item_slot(item_type, "undeclared" if undeclared else "slot")
    return SlottedRevisionPlan.wrap(
        plan,
        slot_key=None if undeclared else plan.key,
        slot_match=how or "explicit",
        undeclared=undeclared,
        declared_keys=declared,
    )


async def finish_definition_revision(
    twin: Any,
    plan: RevisionPlan | None,
    node_id: UUID,
    *,
    name: str,
    result: dict[str, Any],
    link_supersedes: bool = True,
) -> None:
    """Attach ``node_id`` to its item and add ``item_key``/``revision`` to ``result``.

    Raises ``ItemRevisionConflictError`` when a pinned ``KEY@n`` write lost a
    race to another writer (see ``twin_core.items.commit_revision``).
    """
    if plan is None:
        return
    item = await commit_revision(twin, plan, node_id, name=name, link_supersedes=link_supersedes)
    if item is None:
        result["item_warning"] = (
            f"saved, but linking it to item {plan.key} as revision {plan.revision} failed; "
            "it may show as a separate node until repaired"
        )
        return
    result.update(plan.result_fields())
    if plan.prior_node_id is not None and (
        "supersedes_node_id" not in result or plan.planned_revision is not None
    ):
        # After a renumber (another write moved the head first) the
        # predecessor is the actual head, not the one the caller planned on.
        result["supersedes_node_id"] = str(plan.prior_node_id)


def item_to_dict(item: Any, run_id: str | None = None) -> dict[str, Any]:
    """The JSON shape of an item, shared by the MCP tool and the REST route.

    ``head_*`` is always the approved head (``None`` for an item that only has
    drafts). With ``run_id`` and an open draft of that run, ``draft_*`` names
    the run's own draft, which is what that run reads as current (FORGE-525).
    """
    head = item.head_node_id
    out: dict[str, Any] = {
        "key": item.key,
        "item_type": item.item_type,
        "name": item.name,
        "project_id": str(item.project_id) if item.project_id else None,
        "head_revision": item.head_revision,
        "head_node_id": str(head) if head else None,
        "head_ref": f"{item.key}@{item.head_revision}" if head else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    entry = (getattr(item, "drafts", None) or {}).get(run_id) if run_id else None
    if isinstance(entry, dict):
        out["draft_revision"] = entry.get("revision")
        out["draft_node_id"] = entry.get("node_id")
        out["draft_ref"] = f"{item.key}@{entry.get('revision')}"
    return out


def revision_to_dict(revision: Any) -> dict[str, Any]:
    out: dict[str, Any] = revision.model_dump(mode="json")
    return out


def make_item_history_reader(twin: Any) -> Any:
    """Return the async ``read(...)`` behind ``twin.item_history`` (FORGE-523)."""
    from twin_core.items import (
        UnknownItemError,
        find_item,
        item_for_node,
        item_history,
        parse_item_ref,
        visible_head,
    )

    async def read(
        *,
        item_key: str | None = None,
        node_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """``run_id`` defaults to the calling run: its drafts are listed, others' are not."""
        run_id = revision_run_id(run_id)
        if item_key:
            key, _ = parse_item_ref(item_key)
            item = await find_item(twin, key, project_id, any_project=True)
            if item is None:
                raise UnknownItemError(f"twin.item_history: no item {key}")
        elif node_id:
            try:
                found = await item_for_node(twin, UUID(str(node_id)))
            except ValueError as exc:
                raise ValueError(f"twin.item_history: node_id {node_id!r} is not a UUID") from exc
            if found is None:
                raise UnknownItemError(
                    f"twin.item_history: node {node_id} is not a revision of any item "
                    "(records and unclassified types have no item)"
                )
            item = found[0]
        else:
            raise ValueError("twin.item_history: pass 'item_key' or 'node_id'")
        revisions = await item_history(twin, item, run_id=run_id)
        seen = visible_head(item, run_id)
        logger.info("item_history_read", item_key=item.key, revisions=len(revisions), run_id=run_id)
        return {
            "item": item_to_dict(item, run_id),
            "revisions": [revision_to_dict(r) for r in revisions],
            # What this caller reads as current: its run's draft, else the head.
            "current": (
                {"revision": seen[0], "node_id": str(seen[1]), "ref": f"{item.key}@{seen[0]}"}
                if seen is not None
                else None
            ),
        }

    return read
