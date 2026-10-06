"""What a reworked or retried phase is told about its failed revisions (FORGE-530).

A rework (FORGE-500) or retry (FORGE-495) used to hand the phase brain the
gate's findings and the reviewer's reason as prose. The brain then had to work
out on its own which part it had changed, to what, and whether the change
went the wrong way. With items (FORGE-523) that is a lookup: the revision the
gate turned down has a ref, and the revision before it is one edge away.

Two halves, so the Temporal workflow stays deterministic:

- :func:`revision_note_lines` is pure. It renders :class:`RevisionNote`
  values into prompt lines and is what ``rework.build_rework_feedback`` calls
  (the workflow imports that module, so no I/O may happen there).
- :func:`collect_revision_notes` and :func:`rework_context_lines` read the
  twin. They run where I/O is allowed (an activity, the in-process executor,
  the phase brain), given the phase's rejected or failed revisions.

For each revision the lines say: the ref (``CAD-BRACKET@4``), the gate's
reason, and a short diff against the previous revision (bounding box, volume
and mass deltas for a part, from the injected geometry diff when there is one
and from the revision's own metadata otherwise; limit changes for a
requirement set). Reads only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

__all__ = [
    "MAX_NOTES",
    "RevisionNote",
    "RevisionNotesProvider",
    "cad_delta",
    "closed_phase_revisions",
    "collect_revision_notes",
    "notes_from_dicts",
    "notes_to_dicts",
    "phase_revision_notes",
    "requirement_delta",
    "revision_note_lines",
    "rework_context_lines",
]

#: Most revisions described in one rework block; the prompt stays short.
MAX_NOTES = 6
#: Most change lines per revision.
MAX_CHANGES = 6

GeometryDiff = Callable[..., Awaitable[dict[str, Any]]]

#: ``(run_id, phase_id, project_id, gate_reason) -> notes``: what the executor
#: and the Temporal activity are injected with (the twin lives in the gateway).
RevisionNotesProvider = Callable[[str, str, str | None, str], Awaitable[list["RevisionNote"]]]

#: Statuses a run's draft is closed with when its gate does not approve it.
_CLOSED = ("rejected", "abandoned")


@dataclass(frozen=True)
class RevisionNote:
    """One rejected or failed revision, ready to render."""

    ref: str
    item_type: str
    name: str = ""
    status: str = "rejected"
    reason: str | None = None
    previous_ref: str | None = None
    changes: tuple[str, ...] = field(default_factory=tuple)


def notes_to_dicts(notes: Sequence[RevisionNote]) -> list[dict[str, Any]]:
    """Plain data for a Temporal activity result. Pure."""
    return [
        {
            "ref": n.ref,
            "item_type": n.item_type,
            "name": n.name,
            "status": n.status,
            "reason": n.reason,
            "previous_ref": n.previous_ref,
            "changes": list(n.changes),
        }
        for n in notes
    ]


def notes_from_dicts(rows: Sequence[Any]) -> list[RevisionNote]:
    """The inverse of :func:`notes_to_dicts`; skips anything malformed. Pure."""
    out: list[RevisionNote] = []
    for row in rows or ():
        if not isinstance(row, dict) or not row.get("ref"):
            continue
        out.append(
            RevisionNote(
                ref=str(row["ref"]),
                item_type=str(row.get("item_type") or ""),
                name=str(row.get("name") or ""),
                status=str(row.get("status") or "rejected"),
                reason=row.get("reason") or None,
                previous_ref=row.get("previous_ref") or None,
                changes=tuple(str(c) for c in row.get("changes") or ()),
            )
        )
    return out


def revision_note_lines(notes: Sequence[RevisionNote]) -> list[str]:
    """Prompt lines for ``notes``; empty when there are none. Pure."""
    if not notes:
        return []
    lines = ["Revisions this gate turned down (do not repeat them unchanged):"]
    for note in list(notes)[:MAX_NOTES]:
        label = f" '{note.name}'" if note.name else ""
        lines.append(f"  - {note.ref} {note.item_type}{label} ({note.status})")
        if note.reason:
            lines.append(f"    Gate's reason: {note.reason}")
        if note.previous_ref:
            if note.changes:
                lines.append(f"    Changed from {note.previous_ref}:")
                lines.extend(f"      {c}" for c in note.changes[:MAX_CHANGES])
            else:
                lines.append(f"    No recorded change in key facts from {note.previous_ref}.")
        else:
            lines.append("    First revision of this item; nothing earlier to compare.")
    extra = len(notes) - MAX_NOTES
    if extra > 0:
        lines.append(f"  - and {extra} more; `twin.item_history` lists them.")
    return lines


def _fmt(value: float) -> str:
    from twin_core.items.facts import fmt_number

    return fmt_number(value)


def _delta(label: str, before: float | None, after: float | None, unit: str) -> str | None:
    if before is None or after is None or before == after:
        return None
    diff = after - before
    pct = f", {diff / before * 100:+.1f}%" if before else ""
    return f"{label} {_fmt(before)} -> {_fmt(after)} {unit} ({diff:+.4g}{pct})"


def cad_delta(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Bbox, material, volume, mass and part-count changes between two cad revisions."""
    from twin_core.items.facts import cad_facts, format_bbox

    before, after = cad_facts(previous), cad_facts(current)
    out: list[str] = []
    bb_before, bb_after = format_bbox(before.get("bbox")), format_bbox(after.get("bbox"))
    if bb_before != bb_after and (bb_before or bb_after):
        out.append(f"bbox {bb_before or 'unknown'} -> {bb_after or 'unknown'}")
    if before.get("material") != after.get("material") and (
        before.get("material") or after.get("material")
    ):
        out.append(
            f"material {before.get('material') or 'unset'} -> {after.get('material') or 'unset'}"
        )
    for line in (
        _delta("volume", before.get("volume_mm3"), after.get("volume_mm3"), "mm3"),
        _delta("mass", before.get("mass_kg"), after.get("mass_kg"), "kg"),
    ):
        if line:
            out.append(line)
    if before.get("parts") != after.get("parts") and (before.get("parts") or after.get("parts")):
        out.append(f"parts {before.get('parts') or 0} -> {after.get('parts') or 0}")
    return out


def requirement_delta(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    """Added, removed and changed constraints, keyed by constraint name."""
    out: list[str] = []
    for name in sorted(set(previous) | set(current)):
        before, after = previous.get(name), current.get(name)
        if before == after:
            continue
        if before is None:
            out.append(f"added {after}")
        elif after is None:
            out.append(f"removed {before}")
        else:
            out.append(f"{before} -> {after}")
    return out


async def _constraint_map(twin: Any, node_id: UUID) -> dict[str, str]:
    from twin_core.items.facts import constraint_nodes, constraint_value

    out: dict[str, str] = {}
    for node in await constraint_nodes(twin, node_id):
        key = str(getattr(node, "metric", None) or getattr(node, "name", None) or node.id)
        value = constraint_value(node)
        if value:
            out[key] = value
    return out


def _geometry_lines(diff: dict[str, Any]) -> list[str]:
    from twin_core.items.facts import format_bbox, num

    out: list[str] = []
    before = format_bbox(diff.get("previous_bounding_box"))
    after = format_bbox(diff.get("current_bounding_box"))
    if before != after and (before or after):
        out.append(f"bbox {before or 'unknown'} -> {after or 'unknown'} (measured from STEP)")
    line = _delta(
        "volume",
        num(diff.get("previous_volume_mm3")),
        num(diff.get("current_volume_mm3")),
        "mm3",
    )
    if line:
        out.append(f"{line} (measured from STEP)")
    return out


async def _resolve(twin: Any, ref: Any, project_id: Any) -> Any:
    """A :class:`~twin_core.items.state.RevisionView` for a node id or ``KEY@n``."""
    from twin_core.items import find_item, item_for_node, parse_item_ref
    from twin_core.items.state import revision_views

    item = None
    node_id: UUID | None = None
    revision: int | None = None
    if isinstance(ref, UUID):
        node_id = ref
    else:
        text = str(ref)
        try:
            node_id = UUID(text)
        except ValueError:
            key, revision = parse_item_ref(text)
            item = await find_item(twin, key, project_id, any_project=project_id is None)
    if node_id is not None:
        found = await item_for_node(twin, node_id)
        if found is None:
            return None
        item = found[0]
    if item is None:
        return None
    views = await revision_views(twin, item)
    if node_id is not None:
        return next((v for v in views if v.node_id == node_id), None), views
    wanted = item.head_revision if revision is None else revision
    return next((v for v in views if v.revision == wanted), None), views


async def collect_revision_notes(
    twin: Any,
    revisions: Sequence[Any],
    *,
    reason: str = "",
    project_id: Any = None,
    geometry_diff: GeometryDiff | None = None,
) -> list[RevisionNote]:
    """Describe ``revisions`` (node ids or ``KEY@n`` refs) against their predecessors.

    ``reason`` is the gate's reason. A ``rejected`` revision keeps its own
    verdict (``status_reason``) when it has one; an ``abandoned`` one (closed
    because the phase was retried or the run sent back) shows the gate's
    reason, since its own only says it was closed. ``geometry_diff`` is the
    gateway's STEP-measured diff
    (``make_geometry_diff``), optional: without it, or when it fails, a part
    is compared by the facts recorded on each revision. Anything that cannot
    be resolved is skipped, never raised.
    """
    import structlog

    logger = structlog.get_logger(__name__)
    notes: list[RevisionNote] = []
    for ref in revisions:
        try:
            resolved = await _resolve(twin, ref, project_id)
        except Exception as exc:  # noqa: BLE001 -- a bad ref costs one note, not the block
            logger.debug("rework_context_ref_failed", ref=str(ref), error=str(exc))
            continue
        if not resolved or resolved[0] is None:
            continue
        view, views = resolved
        previous = next((v for v in reversed(views) if v.revision < view.revision), None)
        changes: list[str] = []
        if previous is not None:
            try:
                if view.item.item_type in {"cad_model", "assembly"}:
                    if geometry_diff is not None:
                        try:
                            changes = _geometry_lines(
                                await geometry_diff(work_product_id=str(view.node_id))
                            )
                        except Exception as exc:  # noqa: BLE001 -- fall back to metadata
                            logger.debug("rework_context_geometry_diff_failed", error=str(exc))
                    if not changes:
                        changes = cad_delta(previous.metadata, view.metadata)
                elif view.item.item_type == "constraint_set":
                    changes = requirement_delta(
                        await _constraint_map(twin, previous.node_id),
                        await _constraint_map(twin, view.node_id),
                    )
            except Exception as exc:  # noqa: BLE001 -- the ref and reason still help
                logger.debug("rework_context_diff_failed", ref=view.ref, error=str(exc))
        name = getattr(view.node, "name", None) or view.item.name
        notes.append(
            RevisionNote(
                ref=view.ref,
                item_type=view.item.item_type,
                name=str(name or ""),
                status=view.status if view.status != "approved" else "failed",
                reason=(
                    (reason.strip() or view.reason)
                    if view.status != "rejected"
                    else (view.reason or reason.strip() or None)
                )
                or None,
                previous_ref=previous.ref if previous is not None else None,
                changes=tuple(changes),
            )
        )
    logger.info("rework_context_collected", revisions=len(revisions), notes=len(notes))
    return notes


async def rework_context_lines(
    twin: Any,
    *,
    phase_id: str,
    revisions: Sequence[Any],
    reason: str = "",
    project_id: Any = None,
    geometry_diff: GeometryDiff | None = None,
) -> list[str]:
    """Prompt lines for ``phase_id``'s rejected or failed ``revisions``."""
    from observability.tracing import get_tracer

    with get_tracer("orchestrator.design_flow.rework_context").start_as_current_span(
        "design_flow.rework_context"
    ) as span:
        span.set_attribute("design_flow.phase", phase_id)
        span.set_attribute("design_flow.revisions", len(revisions))
        notes = await collect_revision_notes(
            twin,
            revisions,
            reason=reason,
            project_id=project_id,
            geometry_diff=geometry_diff,
        )
        return revision_note_lines(notes)


async def closed_phase_revisions(
    twin: Any, *, run_id: str, phase_id: str, project_id: Any = None
) -> list[UUID]:
    """The drafts ``phase_id`` wrote in ``run_id``'s change set that its gate just closed.

    FORGE-525 closes a run's open drafts (``rejected`` on a reject,
    ``abandoned`` on a retry or rework) before the decision reaches the
    flow, stamping every edge of one close with the same ``closed_at``. Only
    the latest close is returned, so a third attempt is told about the second
    attempt's work, not the first's as well.
    """
    from twin_core.items import list_items
    from twin_core.models.enums import EdgeType

    pid: UUID | None = None
    if project_id:
        try:
            pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        except ValueError:
            pid = None
    found: list[tuple[str, str, int, UUID]] = []
    # include_unheaded: an item first written in this run has no head until a
    # gate approves it, so its closed drafts are only reachable this way (live:
    # a retried needs phase was told nothing about the two new items it lost).
    for item in await list_items(twin, project_id=pid, include_unheaded=True):
        edges = await twin.graph.get_edges(
            item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
        )
        for edge in edges:
            meta = edge.metadata or {}
            if (
                str(meta.get("change_set") or "") == run_id
                and str(meta.get("phase") or "") == phase_id
                and str(meta.get("status") or "") in _CLOSED
            ):
                found.append(
                    (
                        str(meta.get("closed_at") or ""),
                        item.key,
                        int(meta.get("revision") or 0),
                        edge.source_id,
                    )
                )
    if not found:
        return []
    latest = max(f[0] for f in found)
    batch = sorted((f for f in found if f[0] == latest), key=lambda f: (f[1], f[2]))
    return [f[3] for f in batch]


async def phase_revision_notes(
    twin: Any,
    *,
    run_id: str,
    phase_id: str,
    project_id: Any = None,
    reason: str = "",
    geometry_diff: GeometryDiff | None = None,
) -> list[RevisionNote]:
    """Notes for the drafts ``phase_id``'s gate just turned down. Never raises."""
    import structlog

    logger = structlog.get_logger(__name__)
    try:
        revisions = await closed_phase_revisions(
            twin, run_id=run_id, phase_id=phase_id, project_id=project_id
        )
        notes = await collect_revision_notes(
            twin,
            revisions,
            reason=reason,
            project_id=project_id,
            geometry_diff=geometry_diff,
        )
    except Exception as exc:  # noqa: BLE001 -- feedback without notes beats no feedback
        logger.warning(
            "rework_context_phase_notes_failed", run_id=run_id, phase=phase_id, error=str(exc)
        )
        return []
    logger.info(
        "rework_context_phase_notes",
        run_id=run_id,
        phase=phase_id,
        revisions=len(revisions),
        notes=len(notes),
    )
    return notes
