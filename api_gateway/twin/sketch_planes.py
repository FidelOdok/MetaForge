"""The sketch plane manager: propose, confirm, reassign, undo and archive (FORGE-790).

A sketch is drawn with no plane (FRS PLN-001). When it needs a physical
meaning, a person places it on a plane. This module is that step, over the
twin:

- **One ``sketch_plane`` item per sketch** (``PLN-<sketch>`` for
  ``SKT-<sketch>``). Every change (confirm, reassign, undo, archive) is the
  item's next revision, never an edit, so the plane has the same history,
  lineage and compare as any other definition. Each revision carries the
  whole :class:`~twin_core.models.sketch_plane.SketchPlane` and a
  ``DEPENDS_ON`` edge (``kind: sketch_plane``) to the sketch revision it was
  set against, so plane and sketch are linked in the graph and a later CAD
  feature can depend on both (PLN-011). The edge kind is deliberately not a
  record pin: saving new strokes must not mark the plane stale.
- **Explicit always wins** (PLN-002). :func:`confirm_plane` places the sketch
  exactly where the person said and never consults inference;
  :func:`propose_plane` only *reads*: it returns a candidate in the order
  explicit selection, existing sketch plane of the same component, the
  client's orthographic view hint, the component frame, then a simple
  heuristic, each with ``proposed_by`` and a confidence, and says when the
  top two are too close to call. A confirmed plane is returned as the
  candidate over any inference.
- **Reassign previews first** (PLN-008). :func:`preview_reassign` returns
  the transformed strokes for ``preserve_world``, ``preserve_local`` or
  ``project_to_target`` and writes nothing. :func:`reassign_plane` commits:
  a new plane revision, and, when the mode moves the strokes, a new sketch
  revision with them. :func:`undo_plane` restores the state before the last
  change as a new revision (strokes included).
- **The camera never changes the plane** (PLN-015). Nothing here takes a
  camera; only these explicit calls write, and a view hint only ever shapes
  a proposal.

Every write is reported as a ``sketch`` operation
(``job:sketch-plane-<op_id>``): accepted, then completed with the new
revision, or failed. A retried ``op_id`` returns the revision it already
made.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from api_gateway.twin.item_revisions import finish_definition_revision, plan_definition_revision
from observability.tracing import get_tracer
from twin_core.models.sketch_plane import (
    PLACED_STATES,
    PROPOSAL_ORDER,
    VIEW_NAMES,
    PlaneKind,
    PlaneState,
    PlaneUnit,
    PrincipalPlane,
    ProposedBy,
    ReassignMode,
    SketchPlane,
    Vec3,
    principal_plane,
    reassign_point,
    unassigned_plane,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_planes")

ITEM_TYPE = "sketch_plane"
#: ``DEPENDS_ON`` edge kind from a plane revision to its sketch revision.
EDGE_KIND = "sketch_plane"
PLANE_FILENAME = "plane.json"

#: Two candidates closer than this are too close to call.
AMBIGUITY_MARGIN = 0.15
#: A best guess below this is not a guess worth acting on.
LOW_CONFIDENCE = 0.3
#: Off-plane distance (sketch units) under which preserve_world is exact.
WORLD_TOLERANCE = 1e-6

CONFIDENCE: dict[ProposedBy, float] = {
    ProposedBy.USER: 1.0,
    ProposedBy.FACE: 0.9,
    ProposedBy.EXISTING_SKETCH: 0.8,
    ProposedBy.VIEW: 0.7,
    ProposedBy.COMPONENT_FRAME: 0.5,
}

ViewHint = Literal["front", "side", "top"]
VIEW_HINT_PLANES: dict[str, PrincipalPlane] = {
    "front": PrincipalPlane.XZ,
    "side": PrincipalPlane.YZ,
    "top": PrincipalPlane.XY,
}

_KEYWORDS: tuple[tuple[re.Pattern[str], PrincipalPlane, str], ...] = (
    (
        re.compile(r"\b(front|elevation|profile|facade)\b", re.I),
        PrincipalPlane.XZ,
        "front view",
    ),
    (re.compile(r"\b(side)\b", re.I), PrincipalPlane.YZ, "side view"),
    (
        re.compile(r"\b(top|plan|footprint|base|floor|layout)\b", re.I),
        PrincipalPlane.XY,
        "top view",
    ),
)

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _count(action: str, proposed_by: str | None = None) -> None:
    try:
        _collector().record_sketch_plane(action, proposed_by or "none")
    except Exception as exc:  # noqa: BLE001 - a counter never fails a change
        logger.debug("sketch_plane_metric_failed", error=str(exc))


class PlaneError(ValueError):
    """A plane change that cannot be made as asked (a 409 at the route)."""


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------

_OP_ID = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PlaneCandidate(BaseModel):
    """One source's guess, in inference order."""

    principal: PrincipalPlane
    view_name: str
    proposed_by: ProposedBy
    confidence: float
    reason: str


class PlaneProposalRequest(BaseModel):
    """What the client knows when it asks for a proposal. Nothing is written."""

    #: The plane the person picked, if any. Always the candidate when given.
    explicit: PrincipalPlane | None = None
    #: The active orthographic view in the client, if the camera is on one.
    view_hint: ViewHint | None = None
    owner_component: str | None = Field(default=None, max_length=120)
    #: Free text to read for a hint ("front elevation", "footprint").
    hint_text: str | None = Field(default=None, max_length=500)
    #: A picked model face. Face attachment is not available yet.
    face_ref: str | None = Field(default=None, max_length=200)


class PlaneProposal(BaseModel):
    sketch_key: str
    sketch_ref: str | None
    candidate: SketchPlane
    candidates: list[PlaneCandidate]
    ambiguous: bool
    #: Always true: a proposal is applied only by an explicit confirm.
    requires_confirmation: bool = True
    #: The plane already confirmed for this sketch, if any (it wins).
    current: SketchPlane | None = None
    notes: list[str] = Field(default_factory=list)


class PlaneConfirmRequest(BaseModel):
    """A person's explicit choice of plane (PLN-002)."""

    op_id: str = _OP_ID
    principal: PrincipalPlane
    origin: Vec3 = (0.0, 0.0, 0.0)
    unit: PlaneUnit = PlaneUnit.UNKNOWN
    scale: float | None = Field(default=None, gt=0)
    owner_component: str | None = Field(default=None, max_length=120)
    #: Which proposal the person accepted, if they accepted one; ``user`` otherwise.
    proposed_by: ProposedBy = ProposedBy.USER
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    #: The plane revision the client last saw (``PLN-...@n``); a newer head is a conflict.
    base_revision: str | None = Field(default=None, max_length=200)


class PlaneReassignRequest(BaseModel):
    op_id: str = _OP_ID
    target: PrincipalPlane
    origin: Vec3 | None = None
    mode: ReassignMode
    base_revision: str | None = Field(default=None, max_length=200)


class PlaneSimpleRequest(BaseModel):
    """Undo or archive."""

    op_id: str = _OP_ID
    base_revision: str | None = Field(default=None, max_length=200)


class ReassignedStroke(BaseModel):
    id: str
    points: list[tuple[float, float]]
    #: The farthest any of this stroke's points is from the target plane.
    off_plane: float


class ReassignPreview(BaseModel):
    """What a reassign would do. Nothing is written."""

    sketch_key: str
    sketch_ref: str | None
    mode: ReassignMode
    source: SketchPlane
    target: SketchPlane
    strokes: list[ReassignedStroke]
    max_off_plane: float
    #: True when every point keeps its meaning exactly in this mode.
    exact: bool
    #: True when the mode changes stroke coordinates (a new sketch revision).
    rewrites_strokes: bool
    #: False when a commit in this mode would be refused (see ``warnings``).
    committable: bool
    warnings: list[str] = Field(default_factory=list)


class SketchPlaneView(BaseModel):
    """A sketch's current plane and the revision that holds it."""

    sketch_key: str
    sketch_ref: str | None
    plane: SketchPlane
    item_key: str | None = None
    revision: int | None = None
    item_ref: str | None = None
    node_id: str | None = None
    parent_revision: str | None = None
    #: The sketch revision this plane revision was set against.
    pinned_sketch_ref: str | None = None
    action: str | None = None
    can_undo: bool = False
    mode: str | None = None


class PlaneChangeResult(SketchPlaneView):
    operation_id: str
    duplicate: bool = False
    unchanged: bool = False
    #: A new sketch revision written by this change (reassign or undo), if any.
    sketch_revision_written: str | None = None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def plane_item_key(sketch_key: str) -> str:
    """``SKT-FOO`` -> ``PLN-FOO``: one plane item per sketch item."""
    key = sketch_key.strip().upper()
    stem = key[4:] if key.startswith("SKT-") else key
    return f"PLN-{stem}"


def operation_id_for(op_id: str) -> str:
    return f"job:sketch-plane-{op_id}"


def _pid(project_id: str | UUID) -> UUID:
    try:
        return UUID(str(project_id))
    except ValueError as exc:
        raise PlaneError(f"project id {project_id!r} is not a UUID") from exc


async def _sketch_head(twin: Any, pid: UUID, sketch_key: str) -> tuple[Any, Any]:
    """``(item, head node)`` of the sketch, or :class:`PlaneError`."""
    from twin_core.items import find_item, parse_item_ref

    key, _ = parse_item_ref(sketch_key)
    item = await find_item(twin, key, pid)
    if item is None or item.item_type != "sketch_strokes" or item.head_node_id is None:
        raise PlaneError(f"no saved sketch {key} in this project")
    node = await twin.get_work_product(item.head_node_id)
    if node is None:
        raise PlaneError(f"sketch {key} has no readable head revision")
    return item, node


async def _plane_head(twin: Any, pid: UUID, sketch_key: str) -> tuple[Any, Any]:
    """``(item, head node)`` of the sketch's plane item; ``(None, None)`` when none."""
    from twin_core.items import find_item

    item = await find_item(twin, plane_item_key(sketch_key), pid)
    if item is None or item.head_node_id is None:
        return None, None
    node = await twin.get_work_product(item.head_node_id)
    return item, node


def _plane_of(node: Any) -> SketchPlane:
    meta = (node.metadata or {}) if node is not None else {}
    return SketchPlane.model_validate(meta["plane"])


def _ref(meta: dict[str, Any]) -> str | None:
    key, rev = meta.get("item_key"), meta.get("item_revision")
    return f"{key}@{rev}" if key and rev else None


def _view(sketch_key: str, sketch_ref: str | None, node: Any) -> SketchPlaneView:
    if node is None:
        return SketchPlaneView(
            sketch_key=sketch_key,
            sketch_ref=sketch_ref,
            plane=unassigned_plane(plane_id=plane_item_key(sketch_key), source_revision=sketch_ref),
        )
    meta = node.metadata or {}
    return SketchPlaneView(
        sketch_key=sketch_key,
        sketch_ref=sketch_ref,
        plane=_plane_of(node),
        item_key=meta.get("item_key"),
        revision=meta.get("item_revision"),
        item_ref=_ref(meta),
        node_id=str(node.id),
        parent_revision=meta.get("parent_revision"),
        pinned_sketch_ref=meta.get("sketch_ref"),
        action=meta.get("plane_action"),
        can_undo=meta.get("undo_to") is not None,
        mode=meta.get("reassign_mode"),
    )


async def get_plane(twin: Any, project_id: str, sketch_key: str) -> SketchPlaneView:
    """The sketch's current plane (an UNASSIGNED plane when none was ever set)."""
    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.sketch_plane.get") as span:
        span.set_attribute("project_id", str(pid))
        span.set_attribute("sketch.key", sketch_key)
        sk_item, _ = await _sketch_head(twin, pid, sketch_key)
        _, node = await _plane_head(twin, pid, sk_item.key)
        return _view(sk_item.key, f"{sk_item.key}@{sk_item.head_revision}", node)


async def plane_summary_for_sketch(
    twin: Any, project_id: UUID, sketch_key: str
) -> dict[str, Any] | None:
    """``{"state", "plane_ref", "principal"}`` of the sketch's plane, for a sketch save."""
    try:
        _, node = await _plane_head(twin, project_id, sketch_key)
        if node is None:
            return None
        plane = _plane_of(node)
    except Exception as exc:  # noqa: BLE001 - the save goes on with no plane
        logger.warning("sketch_plane_summary_failed", sketch_key=sketch_key, error=str(exc))
        return None
    return {
        "state": plane.state.value,
        "plane_ref": _ref(node.metadata or {}),
        "principal": plane.principal.value if plane.principal else None,
        # Read by the sketch compiler, which carries a placed plane's ref and
        # unit into an interpretation and its promotion.
        "unit": plane.unit.value,
        "placed": plane.placed,
    }


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


def _candidate(principal: PrincipalPlane, by: ProposedBy, conf: float, reason: str) -> Any:
    return PlaneCandidate(
        principal=principal,
        view_name=VIEW_NAMES[principal],
        proposed_by=by,
        confidence=round(conf, 3),
        reason=reason,
    )


def _bbox(strokes: list[dict[str, Any]]) -> tuple[float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for s in strokes:
        for p in s.get("points") or []:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                xs.append(float(p[0]))
                ys.append(float(p[1]))
    if not xs:
        return None
    return (max(xs) - min(xs), max(ys) - min(ys))


def heuristic_candidates(strokes: list[dict[str, Any]], text: str) -> list[PlaneCandidate]:
    """The model/heuristic guesses: keywords first, then the drawing's shape."""
    out: list[PlaneCandidate] = []
    for pattern, principal, label in _KEYWORDS:
        match = pattern.search(text or "")
        if match:
            out.append(
                _candidate(
                    principal,
                    ProposedBy.MODEL,
                    0.45,
                    f"the words '{match.group(0)}' suggest a {label}",
                )
            )
            break
    box = _bbox(strokes)
    if box is not None and box[0] > 0 and box[1] / box[0] > 1.6:
        out.append(
            _candidate(
                PrincipalPlane.XZ,
                ProposedBy.MODEL,
                0.35,
                "the drawing is much taller than it is wide, like a front elevation",
            )
        )
    else:
        out.append(
            _candidate(
                PrincipalPlane.XY,
                ProposedBy.MODEL,
                0.25,
                "no strong hint: a sketch is most often a top view (a guess)",
            )
        )
    return out


def is_ambiguous(candidates: list[PlaneCandidate]) -> bool:
    """True when the best guess is weak, or the top two disagree and are close."""
    if not candidates:
        return True
    best = candidates[0]
    if best.proposed_by is ProposedBy.USER:
        return False
    if best.confidence < LOW_CONFIDENCE:
        return True
    for other in candidates[1:]:
        if other.principal is best.principal:
            continue
        return best.confidence - other.confidence <= AMBIGUITY_MARGIN + 1e-9
    return False


async def _existing_sketch_plane(
    twin: Any, pid: UUID, sketch_key: str, owner: str | None
) -> tuple[str, SketchPlane] | None:
    """The newest placed plane of another sketch on the same component."""
    from twin_core.items import list_items

    own = plane_item_key(sketch_key)
    best: tuple[datetime, str, SketchPlane] | None = None
    for item in await list_items(twin, project_id=pid, item_type=ITEM_TYPE):
        if item.key == own or item.head_node_id is None:
            continue
        node = await twin.get_work_product(item.head_node_id)
        if node is None:
            continue
        try:
            plane = _plane_of(node)
        except Exception:  # noqa: BLE001 - a malformed revision is skipped
            continue
        if not plane.placed or plane.principal is None or plane.owner_component != owner:
            continue
        stamp = item.updated_at
        if best is None or stamp > best[0]:
            best = (stamp, str((node.metadata or {}).get("sketch_item_key") or item.key), plane)
    return (best[1], best[2]) if best else None


async def propose_plane(
    twin: Any, project_id: str, sketch_key: str, request: PlaneProposalRequest
) -> PlaneProposal:
    """A candidate plane for the sketch, in inference order. Writes nothing."""
    from api_gateway.twin.sketch_strokes_recorder import current_sketch

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.sketch_plane.propose") as span:
        span.set_attribute("project_id", str(pid))
        sk_item, _ = await _sketch_head(twin, pid, sketch_key)
        key = sk_item.key
        sketch_ref = f"{key}@{sk_item.head_revision}"
        _, head = await _plane_head(twin, pid, key)
        current = _plane_of(head) if head is not None else None
        owner = request.owner_component or (current.owner_component if current else None)

        notes: list[str] = []
        cands: list[PlaneCandidate] = []
        if request.explicit is not None:
            cands.append(_candidate(request.explicit, ProposedBy.USER, 1.0, "you chose this plane"))
        if request.face_ref:
            notes.append(
                "Placing a sketch on a model face is not available yet; choose Front, Side or Top."
            )
        existing = await _existing_sketch_plane(twin, pid, key, owner)
        if existing is not None:
            other, plane = existing
            assert plane.principal is not None
            cands.append(
                _candidate(
                    plane.principal,
                    ProposedBy.EXISTING_SKETCH,
                    CONFIDENCE[ProposedBy.EXISTING_SKETCH],
                    f"{other} on the same component is on the {plane.view_name} plane",
                )
            )
        if request.view_hint:
            principal = VIEW_HINT_PLANES[request.view_hint]
            cands.append(
                _candidate(
                    principal,
                    ProposedBy.VIEW,
                    CONFIDENCE[ProposedBy.VIEW],
                    f"the view is looking at the {VIEW_NAMES[principal]}",
                )
            )
        if owner:
            cands.append(
                _candidate(
                    PrincipalPlane.XY,
                    ProposedBy.COMPONENT_FRAME,
                    CONFIDENCE[ProposedBy.COMPONENT_FRAME],
                    f"the base (XY) plane of {owner}'s frame",
                )
            )
        sketch = await current_sketch(twin, str(pid), key)
        text = " ".join(t for t in (request.hint_text, sketch.name if sketch else "") if t)
        cands.extend(heuristic_candidates(sketch.strokes if sketch else [], text))

        cands.sort(key=lambda c: PROPOSAL_ORDER.index(c.proposed_by))
        ambiguous = is_ambiguous(cands)
        best = cands[0]
        plane_id = plane_item_key(key)
        if current is not None and current.placed and request.explicit is None:
            # A confirmed plane is an earlier explicit choice: inference never overrides it.
            candidate = current
            ambiguous = False
            notes.append(
                f"This sketch is already on the {current.view_name or 'chosen'} plane; "
                "it stays there unless you choose another."
            )
        else:
            candidate = principal_plane(
                best.principal,
                plane_id=plane_id,
                state=PlaneState.PROPOSED,
                proposed_by=best.proposed_by,
                confidence=best.confidence,
                owner_component=owner,
                source_revision=sketch_ref,
            )
        span.set_attribute("sketch_plane.proposed_by", best.proposed_by.value)
        span.set_attribute("sketch_plane.ambiguous", ambiguous)
        logger.info(
            "sketch_plane_proposed",
            project_id=str(pid),
            sketch_key=key,
            principal=candidate.principal.value if candidate.principal else None,
            proposed_by=best.proposed_by.value,
            confidence=best.confidence,
            ambiguous=ambiguous,
            candidates=len(cands),
        )
        _count("proposed_ambiguous" if ambiguous else "proposed", best.proposed_by.value)
        return PlaneProposal(
            sketch_key=key,
            sketch_ref=sketch_ref,
            candidate=candidate,
            candidates=cands,
            ambiguous=ambiguous,
            current=current if current is not None and current.placed else None,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Reassign preview
# ---------------------------------------------------------------------------


def _target_plane(source: SketchPlane, request: PlaneReassignRequest, sketch_ref: str) -> Any:
    return principal_plane(
        request.target,
        plane_id=source.plane_id,
        origin=request.origin if request.origin is not None else source.origin,
        unit=source.unit,
        scale=source.scale,
        owner_component=source.owner_component,
        source_revision=sketch_ref,
    )


def build_preview(
    sketch_key: str,
    sketch_ref: str | None,
    strokes: list[dict[str, Any]],
    source: SketchPlane,
    target: SketchPlane,
    mode: ReassignMode,
) -> ReassignPreview:
    """The pure part of a reassign: where every point lands, and whether that is allowed."""
    from api_gateway.twin.sketch_strokes_recorder import MAX_COORD

    out: list[ReassignedStroke] = []
    worst = 0.0
    changed = False
    out_of_bounds = False
    for s in strokes:
        pts: list[tuple[float, float]] = []
        off = 0.0
        for p in s.get("points") or []:
            x, y = float(p[0]), float(p[1])
            nx, ny, d = reassign_point(x, y, source, target, mode)
            nx, ny = round(nx, 9), round(ny, 9)
            if abs(nx - x) > WORLD_TOLERANCE or abs(ny - y) > WORLD_TOLERANCE:
                changed = True
            if abs(nx) > MAX_COORD or abs(ny) > MAX_COORD:
                out_of_bounds = True
            pts.append((nx, ny))
            off = max(off, d)
        worst = max(worst, off)
        out.append(ReassignedStroke(id=str(s.get("id")), points=pts, off_plane=round(off, 9)))

    warnings: list[str] = []
    exact = mode == "preserve_local" or worst <= WORLD_TOLERANCE
    committable = True
    if mode == "preserve_world" and not exact:
        committable = False
        warnings.append(
            f"The sketch does not lie in the {target.view_name} plane (up to {worst:.4g} units "
            "off it), so its world position cannot be kept. Choose project to target or keep "
            "local coordinates."
        )
    if mode == "project_to_target" and worst > WORLD_TOLERANCE:
        warnings.append(f"Points move up to {worst:.4g} units to land on the new plane.")
    if out_of_bounds:
        committable = False
        warnings.append("Some points would fall outside the sketch's bounds on the new plane.")
    if source.unit is PlaneUnit.UNKNOWN:
        warnings.append("The sketch's scale is unknown: positions are in sketch units.")
    return ReassignPreview(
        sketch_key=sketch_key,
        sketch_ref=sketch_ref,
        mode=mode,
        source=source,
        target=target,
        strokes=out,
        max_off_plane=round(worst, 9),
        exact=exact,
        rewrites_strokes=mode != "preserve_local" and changed,
        committable=committable,
        warnings=warnings,
    )


async def preview_reassign(
    twin: Any, project_id: str, sketch_key: str, request: PlaneReassignRequest
) -> ReassignPreview:
    """What :func:`reassign_plane` would do. Writes nothing."""
    from api_gateway.twin.sketch_strokes_recorder import current_sketch

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.sketch_plane.preview_reassign") as span:
        span.set_attribute("project_id", str(pid))
        span.set_attribute("sketch_plane.mode", request.mode)
        sk_item, _ = await _sketch_head(twin, pid, sketch_key)
        key = sk_item.key
        sketch_ref = f"{key}@{sk_item.head_revision}"
        _, head = await _plane_head(twin, pid, key)
        if head is None or not _plane_of(head).placed:
            raise PlaneError("place the sketch on a plane before reassigning it")
        source = _plane_of(head)
        target = _target_plane(source, request, sketch_ref)
        sketch = await current_sketch(twin, str(pid), key)
        preview = build_preview(
            key, sketch_ref, sketch.strokes if sketch else [], source, target, request.mode
        )
        span.set_attribute("sketch_plane.committable", preview.committable)
        logger.info(
            "sketch_plane_reassign_previewed",
            project_id=str(pid),
            sketch_key=key,
            mode=request.mode,
            target=request.target.value,
            max_off_plane=preview.max_off_plane,
            committable=preview.committable,
        )
        _count("reassign_previewed")
        return preview


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_project_locks: dict[str, asyncio.Lock] = {}


def _lock_for(project_id: str) -> asyncio.Lock:
    lock = _project_locks.get(project_id)
    if lock is None:
        lock = _project_locks[project_id] = asyncio.Lock()
    return lock


async def _find_by_op_id(twin: Any, pid: UUID, op_id: str) -> Any | None:
    from twin_core.models.enums import WorkProductType

    for wp in await twin.list_work_products(
        work_product_type=WorkProductType.SKETCH_PLANE, project_id=pid
    ):
        if (wp.metadata or {}).get("op_id") == op_id:
            return wp
    return None


def _result(
    sketch_key: str,
    sketch_ref: str | None,
    node: Any,
    op_id: str,
    **extra: Any,
) -> PlaneChangeResult:
    view = _view(sketch_key, sketch_ref, node)
    meta = (node.metadata or {}) if node is not None else {}
    rewritten = meta.get("strokes_rewritten") or {}
    return PlaneChangeResult(
        **view.model_dump(),
        operation_id=operation_id_for(op_id),
        sketch_revision_written=extra.pop("sketch_revision_written", rewritten.get("to")),
        **extra,
    )


async def _write_revision(
    twin: Any,
    pid: UUID,
    sketch_item: Any,
    sketch_node: Any,
    plane: SketchPlane,
    *,
    op_id: str,
    action: str,
    base_revision: str | None,
    undo_to: int | None,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Write ``plane`` as the plane item's next revision, pinned to ``sketch_node``."""
    from api_gateway.twin.sketch_strokes_recorder import _store_blob
    from twin_core.consistency.record_pins import PINS_KEY, ItemPin
    from twin_core.models.enums import EdgeType, WorkProductType
    from twin_core.models.work_product import WorkProduct

    key = plane_item_key(sketch_item.key)
    sketch_ref = f"{sketch_item.key}@{sketch_item.head_revision}"
    name = f"{sketch_item.name or sketch_item.key} plane"
    plan = await plan_definition_revision(
        twin,
        item_type=ITEM_TYPE,
        name=name,
        project_id=pid,
        default_author="human",
        item_key=base_revision or key,
        change_reason=f"plane {action}",
    )
    parent_revision: str | None = None
    if plan is not None and plan.prior_node_id is not None and plan.revision > 1:
        parent_revision = f"{plan.key}@{plan.revision - 1}"

    wp_id = uuid4()
    plane_json = plane.model_dump(mode="json")
    blob = json.dumps(plane_json, separators=(",", ":"), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(blob).hexdigest()
    blob_key = _store_blob(str(wp_id), PLANE_FILENAME, blob, "application/json")
    pin = ItemPin(sketch_item.key, sketch_item.head_revision, sketch_node.id, "sketch_strokes")
    now = datetime.now(UTC)
    metadata: dict[str, Any] = {
        "original_filename": PLANE_FILENAME,
        "content_sha256": content_hash,
        "authored_by": "human",
        "plane": plane_json,
        "plane_state": plane.state.value,
        "plane_action": action,
        "op_id": op_id,
        "sketch_item_key": sketch_item.key,
        "sketch_ref": sketch_ref,
        "parent_revision": parent_revision,
        "undo_to": undo_to,
        PINS_KEY: [pin.to_json()],
        **(extra or {}),
    }
    if blob_key:
        metadata["minio_object_key"] = blob_key
    if plan is not None:
        metadata.update(plan.stamp())
    wp = WorkProduct(
        id=wp_id,
        name=name,
        type=WorkProductType.SKETCH_PLANE,
        domain="concept",
        file_path="",
        content_hash=content_hash,
        format="json",
        metadata=metadata,
        created_at=now,
        updated_at=now,
        created_by="human",
        project_id=pid,
    )
    created = await twin.create_work_product(wp)
    node_id = getattr(created, "id", wp_id)
    result: dict[str, Any] = {}
    await finish_definition_revision(twin, plan, node_id, name=name, result=result)
    try:
        await twin.add_edge(
            node_id,
            sketch_node.id,
            EdgeType.DEPENDS_ON,
            metadata={
                "kind": EDGE_KIND,
                "item_key": sketch_item.key,
                "revision": sketch_item.head_revision,
                "item_ref": sketch_ref,
            },
        )
    except Exception as exc:  # noqa: BLE001 - the revision is saved; the edge is reported
        logger.warning("sketch_plane_edge_failed", node_id=str(node_id), error=str(exc))
    return await twin.get_work_product(node_id) or created


def _publish(project_id: str, op_id: str, event: str, state: str, **fields: Any) -> None:
    from api_gateway.operations import publisher as operations

    operations.publish(
        project_id,
        operation_id_for(op_id),
        event,
        state,
        event_id=f"sketch-plane-{op_id}-{event}",
        domain="sketch",
        **fields,
    )


async def _with_operation(
    project_id: str,
    op_id: str,
    title: str,
    run: Any,
    done_title: Any,
) -> PlaneChangeResult:
    """Report ``run()`` as an operation: accepted, then completed or failed."""
    _publish(project_id, op_id, "accepted", "running", title=title, message=title)
    try:
        result: PlaneChangeResult = await run()
    except Exception as exc:
        _count("failed")
        logger.warning(
            "sketch_plane_change_failed", project_id=project_id, op_id=op_id, error=str(exc)
        )
        _publish(
            project_id,
            op_id,
            "failed",
            "failed",
            title="Sketch plane not changed",
            message=f"The plane was not changed: {str(exc)[:300]}",
            severity="error",
        )
        raise
    label = result.item_ref or "plane"
    _publish(
        project_id,
        op_id,
        "completed",
        "completed",
        title=done_title(result),
        message=f"{done_title(result)} ({label}).",
        target_revision=result.item_ref,
        base_revision=result.parent_revision,
        artifact_refs=(
            [
                {
                    "kind": "work_product",
                    "id": result.node_id,
                    "label": f"Plane {label}",
                    "authority": "preview",
                }
            ]
            if result.node_id
            else []
        ),
    )
    return result


def _plane_title(plane: SketchPlane) -> str:
    if plane.state is PlaneState.ARCHIVED:
        return "Sketch plane archived"
    if not plane.placed:
        return "Sketch kept as a concept (no plane)"
    return (
        f"Sketch placed on {plane.view_name} ({plane.principal.value if plane.principal else ''})"
    )


def _same_place(a: SketchPlane, b: SketchPlane) -> bool:
    return (
        a.state == b.state
        and a.kind == b.kind
        and a.principal == b.principal
        and a.origin == b.origin
        and a.unit == b.unit
        and a.scale == b.scale
        and a.owner_component == b.owner_component
    )


async def confirm_plane(
    twin: Any, project_id: str, sketch_key: str, request: PlaneConfirmRequest
) -> PlaneChangeResult:
    """Place the sketch on the plane the person chose. Inference is never consulted."""
    pid = _pid(project_id)

    async def run() -> PlaneChangeResult:
        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.sketch_plane.confirm") as span:
                span.set_attribute("project_id", str(pid))
                span.set_attribute("sketch_plane.principal", request.principal.value)
                sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)
                sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
                dup = await _find_by_op_id(twin, pid, request.op_id)
                if dup is not None:
                    return _result(sk_item.key, sketch_ref, dup, request.op_id, duplicate=True)
                plane_item, head = await _plane_head(twin, pid, sk_item.key)
                confidence = (
                    1.0
                    if request.proposed_by is ProposedBy.USER
                    else (request.confidence if request.confidence is not None else 1.0)
                )
                plane = principal_plane(
                    request.principal,
                    plane_id=plane_item_key(sk_item.key),
                    origin=request.origin,
                    unit=request.unit,
                    scale=request.scale,
                    owner_component=request.owner_component,
                    source_revision=sketch_ref,
                    proposed_by=request.proposed_by,
                    confidence=confidence,
                )
                if head is not None and _same_place(_plane_of(head), plane):
                    return _result(sk_item.key, sketch_ref, head, request.op_id, unchanged=True)
                node = await _write_revision(
                    twin,
                    pid,
                    sk_item,
                    sk_node,
                    plane,
                    op_id=request.op_id,
                    action="confirmed",
                    base_revision=request.base_revision,
                    undo_to=plane_item.head_revision if plane_item is not None else 0,
                )
                _count("confirmed", request.proposed_by.value)
                logger.info(
                    "sketch_plane_confirmed",
                    project_id=str(pid),
                    sketch_key=sk_item.key,
                    principal=request.principal.value,
                    proposed_by=request.proposed_by.value,
                    plane_ref=_ref(node.metadata or {}),
                )
                return _result(sk_item.key, sketch_ref, node, request.op_id)

    return await _with_operation(
        project_id,
        request.op_id,
        f"Placing sketch on {VIEW_NAMES[request.principal]}",
        run,
        lambda r: _plane_title(r.plane),
    )


async def _save_strokes(
    twin: Any,
    pid: UUID,
    sk_item: Any,
    strokes: list[dict[str, Any]],
    save_id: str,
    base: Any,
) -> str | None:
    """Save ``strokes`` as the sketch's next revision; return its ``KEY@n``."""
    from api_gateway.twin.sketch_strokes_recorder import (
        SketchSaveRequest,
        make_sketch_strokes_recorder,
    )

    request = SketchSaveRequest.model_validate(
        {
            "save_id": save_id,
            "name": sk_item.name or "Sketch",
            "item_key": sk_item.key,
            "base_revision": f"{sk_item.key}@{sk_item.head_revision}",
            "strokes": strokes,
            "viewport": base.viewport if base is not None else None,
            "reference_node_id": base.reference_node_id if base is not None else None,
        }
    )
    saved = await make_sketch_strokes_recorder(twin)(str(pid), request)
    return saved.item_ref


async def reassign_plane(
    twin: Any, project_id: str, sketch_key: str, request: PlaneReassignRequest
) -> PlaneChangeResult:
    """Commit a reassign: the new plane, and the moved strokes when the mode moves them."""
    from api_gateway.twin.sketch_strokes_recorder import current_sketch

    pid = _pid(project_id)

    async def run() -> PlaneChangeResult:
        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.sketch_plane.reassign") as span:
                span.set_attribute("project_id", str(pid))
                span.set_attribute("sketch_plane.mode", request.mode)
                sk_item, _ = await _sketch_head(twin, pid, sketch_key)
                sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
                dup = await _find_by_op_id(twin, pid, request.op_id)
                if dup is not None:
                    return _result(sk_item.key, sketch_ref, dup, request.op_id, duplicate=True)
                plane_item, head = await _plane_head(twin, pid, sk_item.key)
                if head is None or not _plane_of(head).placed:
                    raise PlaneError("place the sketch on a plane before reassigning it")
                source = _plane_of(head)
                target = _target_plane(source, request, sketch_ref)
                sketch = await current_sketch(twin, str(pid), sk_item.key)
                preview = build_preview(
                    sk_item.key,
                    sketch_ref,
                    sketch.strokes if sketch else [],
                    source,
                    target,
                    request.mode,
                )
                if not preview.committable:
                    raise PlaneError(" ".join(preview.warnings) or "this reassign cannot be made")
                extra: dict[str, Any] = {"reassign_mode": request.mode}
                if preview.rewrites_strokes and sketch is not None:
                    moved = {s.id: s.points for s in preview.strokes}
                    strokes = [
                        {**s, "points": [list(p) for p in moved.get(str(s.get("id")), [])]}
                        for s in sketch.strokes
                    ]
                    new_ref = await _save_strokes(
                        twin, pid, sk_item, strokes, f"plane-{request.op_id}", sketch
                    )
                    extra["strokes_rewritten"] = {"from": sketch_ref, "to": new_ref}
                    sk_item, _ = await _sketch_head(twin, pid, sk_item.key)
                sk_item, sk_node = await _sketch_head(twin, pid, sk_item.key)
                node = await _write_revision(
                    twin,
                    pid,
                    sk_item,
                    sk_node,
                    target.model_copy(
                        update={"source_revision": f"{sk_item.key}@{sk_item.head_revision}"}
                    ),
                    op_id=request.op_id,
                    action="reassigned",
                    base_revision=request.base_revision,
                    undo_to=plane_item.head_revision if plane_item is not None else 0,
                    extra=extra,
                )
                _count("reassigned", ProposedBy.USER.value)
                logger.info(
                    "sketch_plane_reassigned",
                    project_id=str(pid),
                    sketch_key=sk_item.key,
                    mode=request.mode,
                    target=request.target.value,
                    strokes_rewritten=bool(extra.get("strokes_rewritten")),
                    plane_ref=_ref(node.metadata or {}),
                )
                return _result(
                    sk_item.key, f"{sk_item.key}@{sk_item.head_revision}", node, request.op_id
                )

    return await _with_operation(
        project_id,
        request.op_id,
        f"Moving sketch to {VIEW_NAMES[request.target]}",
        run,
        lambda r: _plane_title(r.plane),
    )


async def _plane_at(twin: Any, plane_item: Any, revision: int) -> tuple[SketchPlane, Any]:
    from twin_core.items import item_history

    for rev in await item_history(twin, plane_item):
        if rev.revision == revision:
            node = await twin.get_work_product(rev.node_id)
            if node is not None:
                return _plane_of(node), node
    raise PlaneError(f"{plane_item.key}@{revision} could not be read")


async def undo_plane(
    twin: Any, project_id: str, sketch_key: str, request: PlaneSimpleRequest
) -> PlaneChangeResult:
    """Restore the plane (and strokes) from before the last change, as a new revision."""
    from api_gateway.twin.sketch_strokes_recorder import current_sketch

    pid = _pid(project_id)

    async def run() -> PlaneChangeResult:
        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.sketch_plane.undo") as span:
                span.set_attribute("project_id", str(pid))
                sk_item, _ = await _sketch_head(twin, pid, sketch_key)
                sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
                dup = await _find_by_op_id(twin, pid, request.op_id)
                if dup is not None:
                    return _result(sk_item.key, sketch_ref, dup, request.op_id, duplicate=True)
                plane_item, head = await _plane_head(twin, pid, sk_item.key)
                undo_to = (head.metadata or {}).get("undo_to") if head is not None else None
                if plane_item is None or head is None or undo_to is None:
                    raise PlaneError("there is no plane change to undo")
                if int(undo_to) == 0:
                    restored = unassigned_plane(
                        plane_id=plane_item_key(sk_item.key), source_revision=sketch_ref
                    )
                    next_undo: int | None = None
                else:
                    restored, old = await _plane_at(twin, plane_item, int(undo_to))
                    next_undo = (old.metadata or {}).get("undo_to")
                extra: dict[str, Any] = {"restored_from": f"{plane_item.key}@{undo_to}"}
                rewritten = (head.metadata or {}).get("strokes_rewritten") or {}
                if rewritten.get("from"):
                    from twin_core.items import resolve_item_ref

                    _, rev = await resolve_item_ref(twin, str(rewritten["from"]), pid)
                    old_node = await twin.get_work_product(rev.node_id)
                    from api_gateway.twin.sketch_strokes_recorder import _revision_from_node

                    old_sketch = _revision_from_node(old_node)
                    base = await current_sketch(twin, str(pid), sk_item.key)
                    new_ref = await _save_strokes(
                        twin, pid, sk_item, old_sketch.strokes, f"undo-{request.op_id}", base
                    )
                    extra["strokes_rewritten"] = {"from": sketch_ref, "to": new_ref}
                    extra["strokes_restored_from"] = rewritten["from"]
                sk_item, sk_node = await _sketch_head(twin, pid, sk_item.key)
                node = await _write_revision(
                    twin,
                    pid,
                    sk_item,
                    sk_node,
                    restored,
                    op_id=request.op_id,
                    action="undone",
                    base_revision=request.base_revision,
                    undo_to=next_undo,
                    extra=extra,
                )
                _count("undone")
                logger.info(
                    "sketch_plane_undone",
                    project_id=str(pid),
                    sketch_key=sk_item.key,
                    restored_from=extra["restored_from"],
                    plane_ref=_ref(node.metadata or {}),
                )
                return _result(
                    sk_item.key, f"{sk_item.key}@{sk_item.head_revision}", node, request.op_id
                )

    return await _with_operation(
        project_id, request.op_id, "Undoing the last plane change", run, _undo_title
    )


def _undo_title(result: PlaneChangeResult) -> str:
    return f"Plane change undone: {_plane_title(result.plane).lower()}"


async def archive_plane(
    twin: Any, project_id: str, sketch_key: str, request: PlaneSimpleRequest
) -> PlaneChangeResult:
    """Retire the sketch's plane. History is kept; undo brings it back."""
    pid = _pid(project_id)

    async def run() -> PlaneChangeResult:
        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.sketch_plane.archive") as span:
                span.set_attribute("project_id", str(pid))
                sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)
                sketch_ref = f"{sk_item.key}@{sk_item.head_revision}"
                dup = await _find_by_op_id(twin, pid, request.op_id)
                if dup is not None:
                    return _result(sk_item.key, sketch_ref, dup, request.op_id, duplicate=True)
                plane_item, head = await _plane_head(twin, pid, sk_item.key)
                if plane_item is None or head is None:
                    raise PlaneError("this sketch has no plane to archive")
                current = _plane_of(head)
                if current.state is PlaneState.ARCHIVED:
                    return _result(sk_item.key, sketch_ref, head, request.op_id, unchanged=True)
                archived = current.model_copy(update={"state": PlaneState.ARCHIVED})
                archived = SketchPlane.model_validate(archived.model_dump())
                node = await _write_revision(
                    twin,
                    pid,
                    sk_item,
                    sk_node,
                    archived,
                    op_id=request.op_id,
                    action="archived",
                    base_revision=request.base_revision,
                    undo_to=plane_item.head_revision,
                )
                _count("archived")
                logger.info(
                    "sketch_plane_archived",
                    project_id=str(pid),
                    sketch_key=sk_item.key,
                    plane_ref=_ref(node.metadata or {}),
                )
                return _result(sk_item.key, sketch_ref, node, request.op_id)

    return await _with_operation(
        project_id,
        request.op_id,
        "Archiving the sketch plane",
        run,
        lambda r: _plane_title(r.plane),
    )


__all__ = [
    "PLACED_STATES",
    "PlaneChangeResult",
    "PlaneConfirmRequest",
    "PlaneError",
    "PlaneKind",
    "PlaneProposal",
    "PlaneProposalRequest",
    "PlaneReassignRequest",
    "PlaneSimpleRequest",
    "ReassignPreview",
    "SketchPlaneView",
    "archive_plane",
    "confirm_plane",
    "get_plane",
    "plane_item_key",
    "plane_summary_for_sketch",
    "preview_reassign",
    "propose_plane",
    "reassign_plane",
    "undo_plane",
]
