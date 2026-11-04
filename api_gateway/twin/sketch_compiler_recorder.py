"""The sketch compiler's records: interpretations and parametric sketches.

The browser fits a saved sketch and proposes constraints (see
``dashboard/src/lib/sketch/``). This module stores what it computed and
promotes what a person accepted, keeping the representations apart (FSG-007):

``sketch_strokes`` (``SKT-*``)
    the person's raw strokes, a human sketch;
``sketch_interpretation`` (``SKI-*``)
    a **proposal**: fitted primitives and proposed, accepted and rejected
    constraints, all estimated. ``DEPENDS_ON`` the strokes revision it was
    made from, and pinned to it;
``parametric_sketch`` (``PSK-*``)
    the **accepted** result: only the accepted constraints, re-checked here,
    and a feature plan whose missing depth, angle and material are
    ``required_input`` (SKP-005). ``DEPENDS_ON`` both. It is held for
    approval through the same approver ``design_sketch`` uses, and is listed
    in ``/v1/approvals``.

Storing an interpretation validates its shape and re-checks its basic
geometry. Promoting one re-checks every accepted constraint with a
deterministic mirror of the browser's equations
(``twin_core.design_ir.sketch_check``) and refuses (409) when one does not
hold, when nothing was accepted, or when the sketch has moved on since.

A sketch whose plane is placed (confirmed or attached) carries that plane's
ref and unit into the interpretation and the promotion; otherwise the unit
stays unknown. The plane is always the plane item's, never the payload's.

Each interpretation is one operation (``job:sketch-compile-<id>``, domain
``sketch``): ``preview_ready`` at stage ``interpretation_proposed`` when it
is stored, ``review_required`` when it is promoted and awaits sign-off, and
``completed`` when the parametric sketch is approved. Storing is idempotent
per ``interpretation_id`` and promotion per ``promote_id``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, ValidationError

from api_gateway.twin.item_revisions import finish_definition_revision, plan_definition_revision
from observability.tracing import get_tracer
from twin_core.design_ir.models import (
    SketchArc,
    SketchCircle,
    SketchElement,
    SketchEllipse,
    SketchLine,
    SketchRectangle,
    SketchSpline,
)
from twin_core.design_ir.sketch_check import check_accepted_constraints, sketch_extent
from twin_core.design_ir.sketch_compiler import (
    FeaturePlanItem,
    SketchInterpretation,
    promote,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_compiler_recorder")

INTERPRETATION_TYPE = "sketch_interpretation"
PARAMETRIC_TYPE = "parametric_sketch"
#: Authority of each representation (FSG-007).
INTERPRETATION_AUTHORITY = "proposal"
PARAMETRIC_AUTHORITY = "human_accepted"
INTERPRETATION_FILE = "interpretation.json"
PARAMETRIC_FILE = "parametric_sketch.json"
SVG_FILE = "sketch.svg"
EDGE_KIND = "sketch_compiler"

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _count(stage: str, outcome: str) -> None:
    try:
        _collector().record_sketch_compile(stage, outcome)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a write
        logger.debug("sketch_compile_metric_failed", error=str(exc))


class SketchCompileError(ValueError):
    """A request the compiler refuses. ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail if detail is not None else message


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


class InterpretationSaveRequest(BaseModel):
    """Store the browser's interpretation of a sketch as a proposal revision."""

    interpretation: SketchInterpretation
    name: str = Field(default="Sketch interpretation", min_length=1, max_length=120)
    #: The interpretation revision this one edits (``SKI-...@n``), as the client saw it.
    base_revision: str | None = Field(default=None, max_length=200)


class InterpretationSaveResponse(BaseModel):
    node_id: str
    item_key: str | None = None
    revision: int | None = None
    item_ref: str | None = None
    interpretation_id: str
    sketch_ref: str | None = None
    plane_id: str | None = None
    unit: str = "unknown"
    operation_id: str
    duplicate: bool = False
    primitive_count: int
    constraint_count: int
    accepted_count: int
    #: Accepted constraints that do not hold on the geometry as sent. A
    #: proposal may be stored mid-edit; promotion refuses while any remain.
    unsatisfied: list[str] = Field(default_factory=list)
    authority: str = INTERPRETATION_AUTHORITY


class PromoteRequest(BaseModel):
    """Promote an interpretation revision to a parametric sketch."""

    #: The client's id for this promotion. A retry returns what it already made.
    promote_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="Parametric sketch", min_length=1, max_length=120)


class PromoteResponse(BaseModel):
    node_id: str
    item_key: str | None = None
    revision: int | None = None
    item_ref: str | None = None
    interpretation_ref: str
    sketch_ref: str | None = None
    plane_id: str | None = None
    unit: str = "unknown"
    promote_id: str
    operation_id: str
    duplicate: bool = False
    approved: bool = False
    approval_id: str
    accepted_count: int
    dropped_count: int
    feature_plan: list[FeaturePlanItem] = Field(default_factory=list)
    authority: str = PARAMETRIC_AUTHORITY


class ApproveParametricRequest(BaseModel):
    approved_by: str | None = Field(default=None, max_length=200)


class ApproveParametricResponse(BaseModel):
    node_id: str
    approved: bool
    approved_at: str | None = None


# ---------------------------------------------------------------------------
# Keys, lookups
# ---------------------------------------------------------------------------


def _stem(sketch_key: str) -> str:
    key = sketch_key.strip().upper()
    return key[4:] if key.startswith("SKT-") else key


def interpretation_item_key(sketch_key: str) -> str:
    """``SKT-FOO`` -> ``SKI-FOO``: one interpretation item per sketch item."""
    return f"SKI-{_stem(sketch_key)}"


def parametric_item_key(sketch_key: str) -> str:
    """``SKT-FOO`` -> ``PSK-FOO``: one parametric sketch item per sketch item."""
    return f"PSK-{_stem(sketch_key)}"


def operation_id_for(interpretation_id: str) -> str:
    return f"job:sketch-compile-{interpretation_id}"


def _pid(project_id: str | UUID) -> UUID:
    try:
        return UUID(str(project_id))
    except ValueError as exc:
        raise SketchCompileError(f"project id {project_id!r} is not a UUID", 400) from exc


def _ref(meta: dict[str, Any]) -> str | None:
    key, rev = meta.get("item_key"), meta.get("item_revision")
    return f"{key}@{rev}" if key and rev else None


async def _sketch_head(twin: Any, pid: UUID, sketch_key: str) -> tuple[Any, Any]:
    from twin_core.items import find_item, parse_item_ref

    key, _ = parse_item_ref(sketch_key)
    item = await find_item(twin, key, pid)
    if item is None or item.item_type != "sketch_strokes" or item.head_node_id is None:
        raise SketchCompileError(f"no saved sketch {key} in this project", 404)
    node = await twin.get_work_product(item.head_node_id)
    if node is None:
        raise SketchCompileError(f"sketch {key} has no readable head revision", 404)
    return item, node


async def _plane(twin: Any, pid: UUID, sketch_key: str) -> tuple[str | None, str]:
    """``(plane_ref, unit)`` of the sketch's plane when it is placed; else ``(None, "unknown")``."""
    from api_gateway.twin.sketch_planes import plane_summary_for_sketch

    summary = await plane_summary_for_sketch(twin, pid, sketch_key)
    if not summary or not summary.get("placed"):
        return None, "unknown"
    unit = str(summary.get("unit") or "unknown")
    return summary.get("plane_ref"), unit if unit in ("mm", "in") else "unknown"


async def _find_by_meta(twin: Any, pid: UUID, wp_type: Any, field: str, value: str) -> Any | None:
    for wp in await twin.list_work_products(work_product_type=wp_type, project_id=pid):
        if (wp.metadata or {}).get(field) == value:
            return wp
    return None


async def _revision_node(twin: Any, pid: UUID, ref: str) -> Any:
    """The interpretation node for ``SKI-...`` (its head) or ``SKI-...@n``."""
    from twin_core.items import find_item, parse_item_ref
    from twin_core.models.enums import WorkProductType

    key, rev = parse_item_ref(ref)
    item = await find_item(twin, key, pid)
    if item is None or item.item_type != INTERPRETATION_TYPE or item.head_node_id is None:
        raise SketchCompileError(f"no sketch interpretation {key} in this project", 404)
    if rev is None or rev == item.head_revision:
        node = await twin.get_work_product(item.head_node_id)
    else:
        node = None
        for wp in await twin.list_work_products(
            work_product_type=WorkProductType.SKETCH_INTERPRETATION, project_id=pid
        ):
            meta = wp.metadata or {}
            if meta.get("item_key") == key and meta.get("item_revision") == rev:
                node = wp
                break
    if node is None:
        raise SketchCompileError(f"no revision {ref} of that interpretation", 404)
    return node


def _store_blob(node_id: str, filename: str, content: bytes, content_type: str) -> str | None:
    from api_gateway.twin.sketch_strokes_recorder import _store_blob as store

    return store(node_id, filename, content, content_type)


def _load_interpretation(node: Any) -> SketchInterpretation:
    meta = node.metadata or {}
    raw = meta.get("interpretation")
    if raw is None:
        key = (meta.get("sketch_files") or {}).get(INTERPRETATION_FILE)
        if not key:
            raise SketchCompileError("the interpretation's content is missing", 500)
        from api_gateway.twin.blob_store import fetch_work_product_blob

        raw = json.loads(fetch_work_product_blob(key).decode("utf-8"))
    return SketchInterpretation.model_validate(raw)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def basic_geometry_problems(interp: SketchInterpretation) -> list[str]:
    """Degenerate shapes the browser should never send: a zero-length line, a zero sweep."""
    scale = max(sketch_extent(interp.primitives), 1e-9)
    floor = 1e-9 * scale
    problems: list[str] = []
    for el in interp.primitives:
        if isinstance(el, SketchLine) and math.dist(el.start, el.end) <= floor:
            problems.append(f"line {el.id} has no length")
        if isinstance(el, SketchArc) and abs((el.end_angle - el.start_angle) % 360) <= 1e-9:
            problems.append(f"arc {el.id} sweeps no angle")
        if isinstance(el, SketchSpline) and len(set(el.points)) < 2:
            problems.append(f"curve {el.id} has fewer than two distinct points")
    return problems


def _f(v: float) -> str:
    out = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if out in ("-0", "") else out


def _element_svg(el: SketchElement) -> str:
    dash = ' stroke-dasharray="4 3"' if (el.confidence is not None and el.confidence < 0.6) else ""
    if el.construction:
        dash = ' stroke-dasharray="2 4" stroke="#94a3b8"'
    attrs = f'data-primitive="{el.id or ""}" vector-effect="non-scaling-stroke"{dash}'
    if isinstance(el, SketchLine):
        return (
            f'<line x1="{_f(el.start[0])}" y1="{_f(el.start[1])}" '
            f'x2="{_f(el.end[0])}" y2="{_f(el.end[1])}" {attrs}/>'
        )
    if isinstance(el, SketchCircle):
        return (
            f'<circle cx="{_f(el.center[0])}" cy="{_f(el.center[1])}" r="{_f(el.radius)}" {attrs}/>'
        )
    if isinstance(el, SketchEllipse):
        return (
            f'<ellipse cx="{_f(el.center[0])}" cy="{_f(el.center[1])}" rx="{_f(el.major_radius)}" '
            f'ry="{_f(el.minor_radius)}" transform="rotate({_f(el.rotation)} '
            f'{_f(el.center[0])} {_f(el.center[1])})" {attrs}/>'
        )
    if isinstance(el, SketchArc):
        a0, a1 = math.radians(el.start_angle), math.radians(el.end_angle)
        sweep = (el.end_angle - el.start_angle) % 360 or 360
        x0, y0 = el.center[0] + el.radius * math.cos(a0), el.center[1] + el.radius * math.sin(a0)
        x1, y1 = el.center[0] + el.radius * math.cos(a1), el.center[1] + el.radius * math.sin(a1)
        large = 1 if sweep > 180 else 0
        return (
            f'<path d="M{_f(x0)} {_f(y0)} A{_f(el.radius)} {_f(el.radius)} 0 {large} 1 '
            f'{_f(x1)} {_f(y1)}" {attrs}/>'
        )
    if isinstance(el, SketchSpline):
        pts = " L".join(f"{_f(x)} {_f(y)}" for x, y in el.points)
        return f'<path d="M{pts}{" Z" if el.closed else ""}" {attrs}/>'
    if isinstance(el, SketchRectangle):
        return (
            f'<rect x="{_f(el.origin[0])}" y="{_f(el.origin[1])}" width="{_f(el.width)}" '
            f'height="{_f(el.height)}" {attrs}/>'
        )
    return ""  # pragma: no cover - exhaustive over SketchElement


def render_primitives_svg(elements: list[SketchElement], *, title: str) -> str:
    """A standalone SVG of the primitives, generated here from validated data only."""
    xs: list[float] = []
    ys: list[float] = []
    for el in elements:
        for name in ("start", "end", "center", "origin"):
            p = getattr(el, name, None)
            if p is not None:
                r = getattr(el, "major_radius", None) or getattr(el, "radius", 0.0) or 0.0
                xs += [p[0] - r, p[0] + r]
                ys += [p[1] - r, p[1] + r]
        if isinstance(el, SketchSpline):
            xs += [p[0] for p in el.points]
            ys += [p[1] for p in el.points]
    x0, x1 = (min(xs), max(xs)) if xs else (-0.5, 0.5)
    y0, y1 = (min(ys), max(ys)) if ys else (-0.5, 0.5)
    span = max(x1 - x0, y1 - y0, 1e-9)
    m = 0.06 * span
    vx, vy, vw, vh = x0 - m, y0 - m, (x1 - x0) + 2 * m, (y1 - y0) + 2 * m
    w = round(800 * vw / max(vw, vh))
    h = round(800 * vh / max(vw, vh))
    safe_title = "".join(c for c in title if c.isalnum() or c in " .,:-()")[:200]
    body = "\n".join(_element_svg(el) for el in elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(vx)} {_f(vy)} {_f(vw)} {_f(vh)}" '
        f'width="{w}" height="{h}">\n<title>{safe_title}</title>\n'
        f'<rect x="{_f(vx)}" y="{_f(vy)}" width="{_f(vw)}" height="{_f(vh)}" fill="#ffffff"/>\n'
        '<g fill="none" stroke="#1d4ed8" stroke-width="2" stroke-linecap="round">\n'
        f"{body}\n</g></svg>"
    )


# ---------------------------------------------------------------------------
# Writing revisions
# ---------------------------------------------------------------------------

_project_locks: dict[str, asyncio.Lock] = {}


def _lock_for(project_id: str) -> asyncio.Lock:
    lock = _project_locks.get(project_id)
    if lock is None:
        lock = _project_locks[project_id] = asyncio.Lock()
    return lock


async def _write(
    twin: Any,
    pid: UUID,
    *,
    item_type: str,
    wp_type: Any,
    item_key: str,
    name: str,
    change_reason: str,
    content: dict[str, Any],
    content_file: str,
    svg: str,
    metadata: dict[str, Any],
    depends_on: list[tuple[Any, str, int, str]],
) -> tuple[Any, dict[str, Any]]:
    """Write the next revision of ``item_key`` with a content blob, an SVG, pins and edges.

    ``depends_on`` is ``(node, item_key, revision, item_type)`` per dependency.
    """
    from twin_core.consistency.record_pins import PINS_KEY, ItemPin
    from twin_core.models.enums import EdgeType
    from twin_core.models.work_product import WorkProduct

    plan = await plan_definition_revision(
        twin,
        item_type=item_type,
        name=name,
        project_id=pid,
        default_author="human",
        item_key=item_key,
        change_reason=change_reason,
    )
    parent_revision: str | None = None
    if plan is not None and plan.prior_node_id is not None and plan.revision > 1:
        parent_revision = f"{plan.key}@{plan.revision - 1}"
    wp_id = uuid4()
    blob = json.dumps(content, separators=(",", ":"), sort_keys=True).encode("utf-8")
    content_hash = hashlib.sha256(blob).hexdigest()
    blob_key = _store_blob(str(wp_id), content_file, blob, "application/json")
    svg_key = _store_blob(str(wp_id), SVG_FILE, svg.encode("utf-8"), "image/svg+xml")
    pins = [ItemPin(key, rev, node.id, kind) for node, key, rev, kind in depends_on]
    meta: dict[str, Any] = {
        "original_filename": SVG_FILE,
        "content_sha256": content_hash,
        "authored_by": "human",
        "parent_revision": parent_revision,
        PINS_KEY: [p.to_json() for p in pins],
        **metadata,
    }
    if svg_key:
        meta["minio_object_key"] = svg_key
    if blob_key:
        meta["sketch_files"] = {content_file: blob_key}
    else:
        # No blob store: keep the content on the node so nothing is lost.
        meta[content_file.removesuffix(".json")] = content
    if plan is not None:
        meta.update(plan.stamp())
    now = datetime.now(UTC)
    wp = WorkProduct(
        id=wp_id,
        name=name,
        type=wp_type,
        domain="concept",
        file_path="",
        content_hash=content_hash,
        format="svg",
        metadata=meta,
        created_at=now,
        updated_at=now,
        created_by="human",
        project_id=pid,
    )
    created = await twin.create_work_product(wp)
    node_id = getattr(created, "id", wp_id)
    result: dict[str, Any] = {}
    await finish_definition_revision(twin, plan, node_id, name=name, result=result)
    for node, key, rev, _kind in depends_on:
        try:
            await twin.add_edge(
                node_id,
                node.id,
                EdgeType.DEPENDS_ON,
                metadata={
                    "kind": EDGE_KIND,
                    "item_key": key,
                    "revision": rev,
                    "item_ref": f"{key}@{rev}",
                },
            )
        except Exception as exc:  # noqa: BLE001 - the revision is saved; the edge is reported
            logger.warning("sketch_compile_edge_failed", node_id=str(node_id), error=str(exc))
    stored = await twin.get_work_product(node_id) or created
    return stored, result


def _publish(
    project_id: str, interpretation_id: str, event: str, state: str, **fields: Any
) -> None:
    from api_gateway.operations import publisher as operations

    operations.publish(
        project_id,
        operation_id_for(interpretation_id),
        event,
        state,
        event_id=f"sketch-compile-{interpretation_id}-{event}",
        domain="sketch",
        **fields,
    )


def _save_response(wp: Any, *, duplicate: bool) -> InterpretationSaveResponse:
    meta = wp.metadata or {}
    return InterpretationSaveResponse(
        node_id=str(wp.id),
        item_key=meta.get("item_key"),
        revision=meta.get("item_revision"),
        item_ref=_ref(meta),
        interpretation_id=str(meta.get("interpretation_id")),
        sketch_ref=meta.get("sketch_ref"),
        plane_id=meta.get("plane_id"),
        unit=str(meta.get("unit") or "unknown"),
        operation_id=operation_id_for(str(meta.get("interpretation_id"))),
        duplicate=duplicate,
        primitive_count=int(meta.get("primitive_count") or 0),
        constraint_count=int(meta.get("constraint_count") or 0),
        accepted_count=int(meta.get("accepted_count") or 0),
        unsatisfied=list(meta.get("unsatisfied") or []),
    )


async def save_interpretation(
    twin: Any, project_id: str, sketch_key: str, request: InterpretationSaveRequest
) -> InterpretationSaveResponse:
    """Store an interpretation as the next revision of the sketch's ``SKI-*`` item."""
    from twin_core.models.enums import WorkProductType

    pid = _pid(project_id)
    interp = request.interpretation
    async with _lock_for(str(pid)):
        with tracer.start_as_current_span("twin.sketch_compiler.save_interpretation") as span:
            span.set_attribute("project_id", str(pid))
            span.set_attribute("sketch.interpretation_id", interp.interpretation_id)
            span.set_attribute("sketch.primitive_count", len(interp.primitives))
            existing = await _find_by_meta(
                twin,
                pid,
                WorkProductType.SKETCH_INTERPRETATION,
                "interpretation_id",
                interp.interpretation_id,
            )
            if existing is not None:
                span.set_attribute("sketch.duplicate", True)
                _count("interpretation", "duplicate")
                return _save_response(existing, duplicate=True)

            sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)
            head_ref = f"{sk_item.key}@{sk_item.head_revision}"
            if interp.sketch_ref and interp.sketch_ref != head_ref:
                _count("interpretation", "refused")
                raise SketchCompileError(
                    f"this interpretation was made from {interp.sketch_ref}, but the sketch is "
                    f"now {head_ref}; make it editable again from the current sketch"
                )
            problems = basic_geometry_problems(interp)
            if problems:
                _count("interpretation", "refused")
                raise SketchCompileError("; ".join(problems[:10]), 422)

            plane_ref, plane_unit = await _plane(twin, pid, sk_item.key)
            # A placed plane's ref and unit are the plane item's; otherwise the
            # plane id is carried through and the unit stays unknown.
            interp = interp.model_copy(
                update={
                    "sketch_ref": head_ref,
                    "plane_id": plane_ref or interp.plane_id,
                    "unit": plane_unit if plane_ref else "unknown",
                }
            )
            try:
                interp = SketchInterpretation.model_validate(interp.model_dump())
            except ValidationError as exc:
                _count("interpretation", "refused")
                raise SketchCompileError(str(exc), 422) from exc
            checks = check_accepted_constraints(interp.primitives, interp.constraints)
            unsatisfied = [c.constraint_id for c in checks if not c.holds]
            content = interp.model_dump(mode="json")
            accepted = len(interp.accepted())
            wp, result = await _write(
                twin,
                pid,
                item_type=INTERPRETATION_TYPE,
                wp_type=WorkProductType.SKETCH_INTERPRETATION,
                item_key=request.base_revision or interpretation_item_key(sk_item.key),
                name=request.name,
                change_reason="sketch interpreted",
                content=content,
                content_file=INTERPRETATION_FILE,
                svg=render_primitives_svg(
                    interp.primitives, title="Sketch interpretation: proposed, estimated, not CAD"
                ),
                metadata={
                    "interpretation_id": interp.interpretation_id,
                    "sketch_ref": head_ref,
                    "sketch_item_key": sk_item.key,
                    "plane_id": interp.plane_id,
                    "unit": interp.unit,
                    "space": interp.space,
                    "primitive_count": len(interp.primitives),
                    "constraint_count": len(interp.constraints),
                    "accepted_count": accepted,
                    "unsatisfied": unsatisfied,
                    "solve_status": interp.solve.status if interp.solve else None,
                    "dof": interp.solve.dof if interp.solve else None,
                    # Honesty fields: a proposal, read off a drawing.
                    "authority": INTERPRETATION_AUTHORITY,
                    "estimated": True,
                },
                depends_on=[(sk_node, sk_item.key, int(sk_item.head_revision), "sketch_strokes")],
            )
            response = _save_response(wp, duplicate=False)
            response.item_key = result.get("item_key", response.item_key)
            response.revision = result.get("revision", response.revision)
            response.item_ref = result.get("item_ref", response.item_ref)
            span.set_attribute("sketch.item_ref", response.item_ref or "")
            logger.info(
                "sketch_interpretation_saved",
                project_id=str(pid),
                node_id=response.node_id,
                item_ref=response.item_ref,
                sketch_ref=head_ref,
                primitives=response.primitive_count,
                constraints=response.constraint_count,
                accepted=accepted,
                unsatisfied=len(unsatisfied),
                plane_id=interp.plane_id,
            )
            _count("interpretation", "proposed")

    label = response.item_ref or "interpretation"
    _publish(
        str(pid),
        interp.interpretation_id,
        "preview_ready",
        "waiting_for_user",
        title="Editable sketch proposed",
        stage_id="interpretation_proposed",
        stage_title="Interpretation proposed",
        message=(
            f"{response.primitive_count} shapes and {response.constraint_count} rules proposed "
            f"as {label}. Estimated from your drawing: accept the rules you meant."
        ),
        target_key=response.item_key,
        target_revision=response.item_ref,
        base_revision=head_ref,
        artifact_refs=[
            {
                "kind": "work_product",
                "id": response.node_id,
                "label": f"Interpretation {label}",
                "authority": "preview",
            }
        ],
    )
    return response


def _promote_response(wp: Any, *, duplicate: bool) -> PromoteResponse:
    meta = wp.metadata or {}
    content = meta.get("parametric_sketch") or {}
    plan = meta.get("feature_plan") or content.get("feature_plan") or []
    return PromoteResponse(
        node_id=str(wp.id),
        item_key=meta.get("item_key"),
        revision=meta.get("item_revision"),
        item_ref=_ref(meta),
        interpretation_ref=str(meta.get("interpretation_ref")),
        sketch_ref=meta.get("sketch_ref"),
        plane_id=meta.get("plane_id"),
        unit=str(meta.get("unit") or "unknown"),
        promote_id=str(meta.get("promote_id")),
        operation_id=operation_id_for(str(meta.get("interpretation_id"))),
        duplicate=duplicate,
        approved=bool(meta.get("approved")),
        approval_id=f"parametric_sketch:{wp.id}",
        accepted_count=int(meta.get("accepted_count") or 0),
        dropped_count=int(meta.get("dropped_count") or 0),
        feature_plan=[FeaturePlanItem.model_validate(p) for p in plan],
    )


async def promote_interpretation(
    twin: Any, project_id: str, interpretation_ref: str, request: PromoteRequest
) -> PromoteResponse:
    """Create a ``parametric_sketch`` revision from an interpretation's accepted constraints.

    Held for approval (``approved: false``) like a design sketch. Refused
    with 409 when nothing is accepted, an accepted constraint does not hold,
    or the sketch has moved on since the interpretation was made.
    """
    from twin_core.models.enums import WorkProductType

    pid = _pid(project_id)
    async with _lock_for(str(pid)):
        with tracer.start_as_current_span("twin.sketch_compiler.promote") as span:
            span.set_attribute("project_id", str(pid))
            span.set_attribute("sketch.promote_id", request.promote_id)
            existing = await _find_by_meta(
                twin, pid, WorkProductType.PARAMETRIC_SKETCH, "promote_id", request.promote_id
            )
            if existing is not None:
                _count("promotion", "duplicate")
                return _promote_response(existing, duplicate=True)

            interp_node = await _revision_node(twin, pid, interpretation_ref)
            imeta = interp_node.metadata or {}
            interp = _load_interpretation(interp_node)
            interp_key = str(imeta.get("item_key"))
            interp_rev = int(imeta.get("item_revision") or 0)
            ref = f"{interp_key}@{interp_rev}"
            span.set_attribute("sketch.interpretation_ref", ref)

            sketch_key = str(imeta.get("sketch_item_key") or "")
            sk_item, sk_node = await _sketch_head(twin, pid, sketch_key)
            head_ref = f"{sk_item.key}@{sk_item.head_revision}"
            if interp.sketch_ref != head_ref:
                _count("promotion", "refused")
                raise SketchCompileError(
                    f"{ref} was made from {interp.sketch_ref}, but the sketch is now {head_ref}; "
                    "make it editable again before promoting"
                )
            accepted = interp.accepted()
            if not accepted:
                _count("promotion", "refused")
                raise SketchCompileError(
                    "nothing is accepted yet: accept the rules and dimensions you meant first"
                )
            checks = check_accepted_constraints(interp.primitives, interp.constraints)
            failing = [c for c in checks if not c.holds]
            if failing:
                _count("promotion", "refused")
                span.set_attribute("sketch.failing_count", len(failing))
                raise SketchCompileError(
                    "accepted rules do not hold on this geometry: "
                    + "; ".join(f"{c.constraint_id} ({c.reason})" for c in failing[:10]),
                    detail={
                        "message": "accepted rules do not hold on this geometry",
                        "failing": [
                            {"id": c.constraint_id, "type": c.type, "reason": c.reason}
                            for c in failing
                        ],
                    },
                )
            # The plane may have been confirmed since the interpretation was stored.
            plane_ref, plane_unit = await _plane(twin, pid, sk_item.key)
            if plane_ref:
                interp = interp.model_copy(update={"plane_id": plane_ref, "unit": plane_unit})
            parametric = promote(interp, interpretation_ref=ref)
            content = parametric.model_dump(mode="json")
            wp, result = await _write(
                twin,
                pid,
                item_type=PARAMETRIC_TYPE,
                wp_type=WorkProductType.PARAMETRIC_SKETCH,
                item_key=parametric_item_key(sk_item.key),
                name=request.name,
                change_reason=f"promoted from {ref}",
                content=content,
                content_file=PARAMETRIC_FILE,
                svg=render_primitives_svg(
                    parametric.elements, title="Parametric sketch: accepted rules"
                ),
                metadata={
                    "promote_id": request.promote_id,
                    "interpretation_id": interp.interpretation_id,
                    "interpretation_ref": ref,
                    "sketch_ref": head_ref,
                    "sketch_item_key": sk_item.key,
                    "plane_id": parametric.plane_id,
                    "unit": parametric.unit,
                    "accepted_count": len(accepted),
                    "dropped_count": len(interp.constraints) - len(accepted),
                    "feature_plan": [p.model_dump(mode="json") for p in parametric.feature_plan],
                    "operation_id": operation_id_for(interp.interpretation_id),
                    "approved": False,
                    "approved_at": None,
                    "authority": PARAMETRIC_AUTHORITY,
                },
                depends_on=[
                    (interp_node, interp_key, interp_rev, INTERPRETATION_TYPE),
                    (sk_node, sk_item.key, int(sk_item.head_revision), "sketch_strokes"),
                ],
            )
            response = _promote_response(wp, duplicate=False)
            response.item_key = result.get("item_key", response.item_key)
            response.revision = result.get("revision", response.revision)
            response.item_ref = result.get("item_ref", response.item_ref)
            logger.info(
                "sketch_promoted",
                project_id=str(pid),
                node_id=response.node_id,
                item_ref=response.item_ref,
                interpretation_ref=ref,
                accepted=len(accepted),
                unit=parametric.unit,
                plane_id=parametric.plane_id,
            )
            _count("promotion", "promoted")

    label = response.item_ref or "parametric sketch"
    _publish(
        str(pid),
        interp.interpretation_id,
        "review_required",
        "waiting_for_user",
        title="Parametric sketch awaiting sign-off",
        stage_id="parametric_review",
        stage_title="Sign-off",
        message=(
            f"{label} made from {len(accepted)} accepted rules and dimensions. "
            "Approve it to make it the editable sketch."
        ),
        target_key=interp_key,
        target_revision=response.item_ref,
        base_revision=ref,
        requires_action=True,
        action_refs=[{"kind": "approval", "id": response.approval_id, "label": "Approve sketch"}],
        artifact_refs=[
            {
                "kind": "work_product",
                "id": response.node_id,
                "label": f"Sketch {label}",
                "authority": "preview",
            }
        ],
    )
    return response


async def approve_parametric_sketch(
    twin: Any, node_id: UUID, approved_by: str | None, audit: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Sign off a parametric sketch (the design-sketch approver) and complete its operation."""
    from api_gateway.twin.design_sketch_recorder import make_design_sketch_approver
    from twin_core.models.enums import WorkProductType

    with tracer.start_as_current_span("twin.sketch_compiler.approve") as span:
        span.set_attribute("sketch.node_id", str(node_id))
        wp = await twin.get_work_product(node_id)
        if wp is None:
            raise SketchCompileError(f"node {node_id} not found", 404)
        if wp.type != WorkProductType.PARAMETRIC_SKETCH:
            raise SketchCompileError(
                f"node {node_id} is a {wp.type.value}, not a parametric_sketch", 400
            )
        approve = make_design_sketch_approver(twin)
        try:
            result: dict[str, Any] = await approve(
                str(node_id),
                approved_by=approved_by,
                change_description="parametric sketch approved",
                **({"audit": audit} if audit else {}),
            )
        except ValueError as exc:
            _count("approval", "refused")
            raise SketchCompileError(str(exc)) from exc
        _count("approval", "approved")
        meta = wp.metadata or {}
        interpretation_id = str(meta.get("interpretation_id") or "")
        if interpretation_id and wp.project_id:
            ref = _ref(meta) or "parametric sketch"
            _publish(
                str(wp.project_id),
                interpretation_id,
                "completed",
                "completed",
                title="Parametric sketch approved",
                message=(
                    f"{ref} is approved. Its dimensions are the ones you confirmed; "
                    "depth and material are still needed before it becomes a part."
                ),
                target_key=meta.get("item_key"),
                target_revision=_ref(meta),
                base_revision=meta.get("interpretation_ref"),
                artifact_refs=[
                    {
                        "kind": "work_product",
                        "id": str(node_id),
                        "label": f"Sketch {ref}",
                        # Exact only once its unit is known: sizes in an
                        # unknown unit are proportions, not CAD.
                        "authority": "exact" if meta.get("unit") in ("mm", "in") else "preview",
                    }
                ],
            )
        logger.info("sketch_parametric_approved", node_id=str(node_id), approved_by=approved_by)
        return result
