"""Artifact geometry state of CAD revisions (CDE slice 6, FRS UI-004, DEF-009).

A geometry artifact moves through a small set of states, and every screen and
gate reads the same set:

``concept``
    Illustrative only (a concept image, an AI reference mesh, a sketch). It
    has no dimensions anyone checked and can never be ``validated``.
``preview``
    A fast procedural preview built on the client from the design IR. It is
    never sent to the twin as engineering geometry.
``exact_ready``
    An exact B-Rep exists (a STEP was committed) but no validity check has
    passed on it. Every CAD revision committed before this slice reads so.
``validation_pending``
    An exact build or a validity check is running on it.
``validated``
    The exact B-Rep passed the validity check (``BRepCheck``, closed solids,
    volume above zero, no free edges where a solid is expected).
``failed``
    An exact build or its validity check failed. Only an *attempt* is ever
    ``failed``: the item head keeps the last valid revision.
``stale``
    An artifact derived from a revision that is no longer the item's head
    (a STEP export of an older revision, for example).
``released``
    The revision was released through the release path. Nothing sets this
    automatically.

The state of a CAD revision is stamped into its node metadata
(``geometry_state``, with the validity result under ``validity``) when the
revision is born from an exact build, or rewritten as a status tag (like a
record's staleness) when the head revision is validated in place. A STEP
export is kept on the revision it was made from (``step_export``), so the gate
and the API answer "is this revision validated and exported" from the node
alone.

Only :func:`check_transition` decides which moves are allowed; recorders call
it before they write a state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.geometry_state")

#: Node metadata keys.
GEOMETRY_STATE_KEY = "geometry_state"
VALIDITY_KEY = "validity"
STEP_EXPORT_KEY = "step_export"
EXACT_SOURCE_KEY = "exact_source"
EXACT_ATTEMPTS_KEY = "exact_attempts"
#: How many failed or unvalidated attempts a revision keeps on record.
MAX_ATTEMPTS = 5


class GeometryState(StrEnum):
    CONCEPT = "concept"
    PREVIEW = "preview"
    EXACT_READY = "exact_ready"
    VALIDATION_PENDING = "validation_pending"
    VALIDATED = "validated"
    FAILED = "failed"
    STALE = "stale"
    RELEASED = "released"


class ValidityOutcome(StrEnum):
    """A validity check's verdict. Lack of evidence is never ``pass``."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class StepExportState(StrEnum):
    QUEUED = "queued"
    READY = "ready"
    FAILED = "failed"
    STALE = "stale"


class GeometryStateError(ValueError):
    """A state change the rules do not allow. The message is caller-facing."""


#: Work product types that are illustrative by nature.
ILLUSTRATIVE_TYPES = frozenset({"render", "image", "design_sketch", "sketch_strokes"})
#: Work product types whose revisions carry exact geometry.
EXACT_TYPES = frozenset({"cad_model"})
#: Item types the release and checkpoint gates hold to exact geometry.
GATED_ITEM_TYPES = ("cad_model", "assembly")

_BADGES: dict[GeometryState, str] = {
    GeometryState.CONCEPT: "Concept",
    GeometryState.PREVIEW: "Preview",
    GeometryState.EXACT_READY: "Exact",
    GeometryState.VALIDATION_PENDING: "Exact",
    GeometryState.VALIDATED: "Validated",
    GeometryState.FAILED: "Exact",
    GeometryState.STALE: "Exact",
    GeometryState.RELEASED: "Validated",
}

_AUTHORITY: dict[GeometryState, str] = {
    GeometryState.CONCEPT: "illustrative",
    GeometryState.PREVIEW: "preview",
    GeometryState.EXACT_READY: "exact",
    GeometryState.VALIDATION_PENDING: "exact",
    GeometryState.VALIDATED: "validated",
    GeometryState.FAILED: "exact",
    GeometryState.STALE: "exact",
    GeometryState.RELEASED: "validated",
}

#: Allowed moves. ``released`` is reachable only through the release path,
#: which :func:`check_transition` enforces separately.
_ALLOWED: dict[GeometryState, frozenset[GeometryState]] = {
    GeometryState.CONCEPT: frozenset({GeometryState.PREVIEW, GeometryState.STALE}),
    GeometryState.PREVIEW: frozenset(
        {GeometryState.VALIDATION_PENDING, GeometryState.EXACT_READY, GeometryState.STALE}
    ),
    GeometryState.EXACT_READY: frozenset(
        {
            GeometryState.VALIDATION_PENDING,
            GeometryState.VALIDATED,
            GeometryState.FAILED,
            GeometryState.STALE,
        }
    ),
    GeometryState.VALIDATION_PENDING: frozenset(
        {
            GeometryState.EXACT_READY,
            GeometryState.VALIDATED,
            GeometryState.FAILED,
            GeometryState.STALE,
        }
    ),
    GeometryState.VALIDATED: frozenset({GeometryState.RELEASED, GeometryState.STALE}),
    GeometryState.FAILED: frozenset({GeometryState.VALIDATION_PENDING, GeometryState.STALE}),
    GeometryState.STALE: frozenset(),
    GeometryState.RELEASED: frozenset({GeometryState.STALE}),
}


def badge(state: GeometryState | str | None) -> str | None:
    """The badge word a screen shows: Concept, Preview, Exact or Validated."""
    if state is None:
        return None
    try:
        return _BADGES[GeometryState(state)]
    except ValueError:
        return None


def authority(state: GeometryState | str | None) -> str | None:
    """The operation-event authority an artifact in ``state`` may claim."""
    if state is None:
        return None
    try:
        return _AUTHORITY[GeometryState(state)]
    except ValueError:
        return None


def is_illustrative(wp_type: str | None, metadata: dict[str, Any] | None = None) -> bool:
    """True for concept material: illustrative types, or anything labelled illustrative."""
    meta = metadata or {}
    if str(meta.get("authority") or "") == "illustrative":
        return True
    return str(wp_type or "") in ILLUSTRATIVE_TYPES


def check_transition(
    current: GeometryState | str | None,
    new: GeometryState | str,
    *,
    illustrative: bool = False,
    via_release: bool = False,
    validity: ValidityOutcome | str | None = None,
) -> GeometryState:
    """Return ``new`` as a state, or raise :class:`GeometryStateError`.

    * ``released`` only through the release path (``via_release=True``), and
      only from ``validated``.
    * Concept and illustrative artifacts are never ``validated`` or
      ``released``, and never exact.
    * ``validated`` needs a ``pass`` validity verdict.
    """
    target = GeometryState(new)
    if illustrative and target not in (
        GeometryState.CONCEPT,
        GeometryState.PREVIEW,
        GeometryState.STALE,
    ):
        raise GeometryStateError(
            f"a concept or illustrative artifact cannot be {target.value}; it has no "
            "checked dimensions"
        )
    if target is GeometryState.RELEASED and not via_release:
        raise GeometryStateError("only the release path sets a revision released")
    if target is GeometryState.VALIDATED and (
        validity is None or ValidityOutcome(validity) is not ValidityOutcome.PASS
    ):
        raise GeometryStateError("a revision is validated only by a passing validity check")
    if current is None:
        return target
    source = GeometryState(current)
    if source is target:
        return target
    if target not in _ALLOWED[source]:
        raise GeometryStateError(f"geometry cannot move from {source.value} to {target.value}")
    return target


def geometry_state_of(wp_type: str | None, metadata: dict[str, Any] | None) -> GeometryState | None:
    """The stored or implied state of a work product revision; ``None`` if not geometry.

    A ``cad_model`` with no stamped state is ``exact_ready``: a STEP exists
    but nothing checked it. Illustrative types read ``concept``.
    """
    meta = metadata or {}
    raw = meta.get(GEOMETRY_STATE_KEY)
    if is_illustrative(wp_type, meta):
        return GeometryState.CONCEPT
    if raw:
        try:
            return GeometryState(str(raw))
        except ValueError:
            logger.warning("geometry_state_unknown", value=str(raw))
    if str(wp_type or "") in EXACT_TYPES:
        return GeometryState.EXACT_READY
    return None


def validated_states() -> frozenset[GeometryState]:
    return frozenset({GeometryState.VALIDATED, GeometryState.RELEASED})


def step_export_view(
    metadata: dict[str, Any] | None,
    *,
    revision: int | None,
    head_revision: int | None,
    content_hash: str | None = None,
) -> dict[str, Any] | None:
    """The STEP export recorded on a revision, with its effective state.

    A ready export of a revision that is no longer the head reads ``stale``;
    so does one whose geometry hash no longer matches the revision.
    """
    raw = (metadata or {}).get(STEP_EXPORT_KEY)
    if not isinstance(raw, dict):
        return None
    out = dict(raw)
    state = str(out.get("state") or "")
    moved = revision is not None and head_revision is not None and revision != head_revision
    changed = bool(content_hash) and bool(out.get("content_hash")) and (
        out.get("content_hash") != content_hash
    )
    if state == StepExportState.READY.value and (moved or changed):
        out["state"] = StepExportState.STALE.value
        out.setdefault(
            "stale_reason",
            "the item moved on to a newer revision" if moved else "the geometry changed",
        )
    return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Readiness for the gates (shared by the release gate and the flow gate)
# ---------------------------------------------------------------------------


@dataclass
class CadReadiness:
    """Whether one CAD item's head is validated and exported for the gate."""

    item_key: str
    name: str
    head_revision: int
    node_id: str
    state: str
    ready: bool
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        ref = f"{self.item_key}@{self.head_revision}"
        if self.ready:
            return f"{ref} validated with a round-trip-passed STEP"
        return f"{ref}: " + "; ".join(self.reasons)


def readiness_of(
    *, item_key: str, name: str, head_revision: int, node: Any
) -> CadReadiness | None:
    """One head revision's readiness; ``None`` for an analysis model (not released)."""
    meta = dict(getattr(node, "metadata", None) or {})
    if str(meta.get("model_role") or "") == "analysis":
        return None
    wp_type = getattr(getattr(node, "type", None), "value", getattr(node, "type", None))
    state = geometry_state_of(str(wp_type or "cad_model"), meta) or GeometryState.EXACT_READY
    reasons: list[str] = []
    if state not in validated_states():
        reasons.append(f"its exact geometry is {state.value}, not validated")
    step = step_export_view(
        meta,
        revision=head_revision,
        head_revision=head_revision,
        content_hash=getattr(node, "content_hash", None),
    )
    if step is None:
        reasons.append("no STEP has been exported for this revision")
    else:
        step_state = str(step.get("state") or "")
        round_trip = step.get("round_trip") if isinstance(step.get("round_trip"), dict) else {}
        outcome = str((round_trip or {}).get("outcome") or ValidityOutcome.NOT_EVALUATED.value)
        if step_state != StepExportState.READY.value:
            reasons.append(f"its STEP export is {step_state or 'missing'}")
        if outcome != ValidityOutcome.PASS.value:
            reasons.append(f"its STEP round trip is {outcome}, not pass")
    return CadReadiness(
        item_key=item_key,
        name=name,
        head_revision=head_revision,
        node_id=str(getattr(node, "id", "")),
        state=state.value,
        ready=not reasons,
        reasons=reasons,
    )


async def exact_cad_readiness(twin: Any, project_id: UUID | str | None) -> list[CadReadiness]:
    """Every CAD item in the project, at its head, checked for the gate.

    Analysis models are left out (they are idealised, never released). An
    empty list means the project has no CAD items to hold to this rule.
    """
    from twin_core.items import list_items, supports_items

    if twin is None or project_id is None or not supports_items(twin):
        return []
    with tracer.start_as_current_span("twin.geometry.readiness") as span:
        out: list[CadReadiness] = []
        for item_type in GATED_ITEM_TYPES:
            for item in await list_items(twin, project_id=project_id, item_type=item_type):
                if item.head_node_id is None:
                    continue
                node = await twin.graph.get_node(item.head_node_id)
                if node is None:
                    continue
                entry = readiness_of(
                    item_key=item.key,
                    name=item.name,
                    head_revision=item.head_revision,
                    node=node,
                )
                if entry is not None:
                    out.append(entry)
        span.set_attribute("cad.items", len(out))
        span.set_attribute("cad.ready", sum(1 for r in out if r.ready))
        return out


async def set_node_metadata(twin: Any, node: Any, **fields: Any) -> dict[str, Any]:
    """Rewrite metadata fields on a node as a status tag (keeps ``updated_at``).

    Like a record's staleness tag, a geometry state or export status is not
    new content, so the revision's own content and timestamps stay put.
    """
    meta = dict(getattr(node, "metadata", None) or {})
    for key, value in fields.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    updates: dict[str, Any] = {"metadata": meta}
    if getattr(node, "updated_at", None) is not None:
        updates["updated_at"] = node.updated_at
    await twin.graph.update_node(node.id, updates)
    return meta


async def mark_step_exports_stale(twin: Any, item: Any, head_revision: int) -> list[str]:
    """Mark the ready STEP exports of ``item``'s older revisions ``stale``. Never raises.

    The export stays on its revision and stays downloadable; only its state
    says it no longer describes the current design. Returns the node ids
    whose export was marked.
    """
    from twin_core.items import item_history

    marked: list[str] = []
    try:
        for rev in await item_history(twin, item, include_drafts=False):
            if rev.revision >= head_revision:
                continue
            node = await twin.graph.get_node(rev.node_id)
            raw = (getattr(node, "metadata", None) or {}).get(STEP_EXPORT_KEY)
            if not isinstance(raw, dict) or raw.get("state") != StepExportState.READY.value:
                continue
            export = dict(raw)
            export["state"] = StepExportState.STALE.value
            export["stale_reason"] = f"{item.key} is now @{head_revision}"
            export["stale_at"] = now_iso()
            await set_node_metadata(twin, node, **{STEP_EXPORT_KEY: export})
            marked.append(str(rev.node_id))
    except Exception as exc:  # noqa: BLE001 - never undo the head move it follows
        logger.warning(
            "step_exports_stale_marking_failed",
            item_key=getattr(item, "key", None),
            error=str(exc),
        )
    if marked:
        logger.info(
            "step_exports_marked_stale",
            item_key=item.key,
            head_revision=head_revision,
            nodes=marked,
        )
    return marked


async def mark_released(twin: Any, node: Any) -> bool:
    """Set a validated revision ``released``. Only the release path calls this.

    Returns False (and changes nothing) for a revision that is not validated.
    """
    meta = dict(getattr(node, "metadata", None) or {})
    wp_type = getattr(getattr(node, "type", None), "value", getattr(node, "type", None))
    current = geometry_state_of(str(wp_type or ""), meta)
    if current is not GeometryState.VALIDATED:
        return False
    check_transition(
        current, GeometryState.RELEASED, via_release=True, illustrative=is_illustrative(wp_type)
    )
    await set_node_metadata(
        twin, node, **{GEOMETRY_STATE_KEY: GeometryState.RELEASED.value, "released_at": now_iso()}
    )
    return True


__all__ = [
    "EXACT_ATTEMPTS_KEY",
    "EXACT_SOURCE_KEY",
    "GATED_ITEM_TYPES",
    "GEOMETRY_STATE_KEY",
    "MAX_ATTEMPTS",
    "STEP_EXPORT_KEY",
    "VALIDITY_KEY",
    "CadReadiness",
    "GeometryState",
    "GeometryStateError",
    "StepExportState",
    "ValidityOutcome",
    "authority",
    "badge",
    "check_transition",
    "exact_cad_readiness",
    "geometry_state_of",
    "is_illustrative",
    "mark_released",
    "mark_step_exports_stale",
    "readiness_of",
    "set_node_metadata",
    "step_export_view",
    "validated_states",
]
