"""A person's freehand sketch from the Create stage, saved as a twin revision (FORGE-789).

The dashboard draws strokes locally (nothing on the input path waits for the
network) and saves the whole sketch when the person asks, or after a short
idle. Each save becomes the next revision of one ``sketch_strokes`` item, so
the sketch has the same history, lineage and compare as any other definition.

What a save holds:

- the strokes as JSON, each with a stable id, its tool, points in sketch units
  (the shorter side of the drawing viewport spans one unit, origin at its
  centre) and pressure when the pen reported it;
- an SVG rendering generated **here**, from the validated strokes, so no
  client-supplied markup is ever stored or served;
- honesty fields the payload can never set: ``authority: human_sketch`` (a
  human sketch is not CAD and not evidence), ``scale: unknown`` and
  ``plane_state`` (FRS PLN-001, PLN-012). The plane state is
  ``UNASSIGNED`` until a person places the sketch on a plane
  (:mod:`api_gateway.twin.sketch_planes`, FORGE-790); a save after that
  records the plane's state and ``plane_ref`` at the time of the save;
- ``parent_revision`` (the item head it was drawn on) and ``save_id``, the
  client's id for the save. A retried save id returns the revision it already
  made instead of adding another (idempotent per save id).

Every save is reported as a short operation (``job:sketch-<save_id>``, domain
``sketch``): ``accepted`` then ``completed`` with an ``artifact_refs`` entry for
the new revision, or ``failed``. The event ids derive from the save id, so a
retry never duplicates them either.

Writes follow ``design_sketch``: a person drawing on their own stage is the
author, so the save is not held for approval; an agent never calls this path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

from api_gateway.twin.item_revisions import finish_definition_revision, plan_definition_revision
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_strokes_recorder")

#: The item type and the honesty fields every revision carries.
ITEM_TYPE = "sketch_strokes"
AUTHORITY = "human_sketch"
SCALE: Final = "unknown"
PLANE_STATE: Final = "UNASSIGNED"
SKETCH_SPACE = "sketch_unit"
DEFAULT_NAME = "Sketch"

#: Bounds on one save. A sketch of a few hundred strokes is far below them.
MAX_STROKES = 2000
MAX_POINTS_PER_STROKE = 10_000
MAX_POINTS_TOTAL = 200_000
#: Sketch units are bounded so a malformed client cannot ask for an absurd SVG.
MAX_COORD = 1000.0

SVG_FILENAME = "sketch.svg"
STROKES_FILENAME = "strokes.json"

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _count(outcome: str) -> None:
    try:
        _collector().record_sketch_save(outcome)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a save
        logger.debug("sketch_save_metric_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

SketchTool = Literal["pen", "line", "arc", "circle", "rectangle", "spline"]


class SketchStroke(BaseModel):
    """One stroke in sketch units. ``points`` are what is drawn, in order."""

    id: str = Field(min_length=1, max_length=100)
    tool: SketchTool = "pen"
    points: list[tuple[float, float]] = Field(min_length=1, max_length=MAX_POINTS_PER_STROKE)
    #: Per-point pen pressure in 0..1, only when the device reported it.
    pressure: list[float] | None = None
    #: Line width in screen pixels (drawn non-scaling).
    width: float = Field(default=2.0, ge=0.5, le=32.0)
    created_at: datetime | None = None
    source: Literal["human", "generated"] = "human"

    @field_validator("points")
    @classmethod
    def _finite_and_bounded(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for x, y in v:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("stroke points must be finite numbers")
            if abs(x) > MAX_COORD or abs(y) > MAX_COORD:
                raise ValueError(f"stroke points must lie within +/-{MAX_COORD:g} sketch units")
        return v

    @model_validator(mode="after")
    def _pressure_matches_points(self) -> SketchStroke:
        if self.pressure is not None:
            if len(self.pressure) != len(self.points):
                raise ValueError("pressure must have one value per point")
            if any(not (0.0 <= p <= 1.0) for p in self.pressure):
                raise ValueError("pressure values must lie in 0..1")
        return self


class SketchViewport(BaseModel):
    """The drawing viewport in CSS pixels when the sketch was last edited (SKP-001)."""

    width_px: float = Field(gt=0, le=100_000)
    height_px: float = Field(gt=0, le=100_000)


class SketchSaveRequest(BaseModel):
    """A save of the whole sketch. Each save becomes the item's next revision."""

    #: The client's id for this save. A retry with the same id makes no new revision.
    save_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default=DEFAULT_NAME, min_length=1, max_length=120)
    #: The sketch item to revise (``SKT-...``); the first save leaves it out.
    item_key: str | None = Field(default=None, max_length=120)
    #: The revision the person drew on top of, as the client last saw it.
    base_revision: str | None = Field(default=None, max_length=200)
    strokes: list[SketchStroke] = Field(default_factory=list, max_length=MAX_STROKES)
    viewport: SketchViewport | None = None
    #: The project image shown under the strokes while drawing, if any.
    reference_node_id: str | None = Field(default=None, max_length=100)
    #: Fixed in this slice: a sketch has no plane until one is confirmed.
    plane_state: Literal["UNASSIGNED"] = PLANE_STATE
    scale: Literal["unknown"] = SCALE

    @model_validator(mode="after")
    def _bounded_total(self) -> SketchSaveRequest:
        total = sum(len(s.points) for s in self.strokes)
        if total > MAX_POINTS_TOTAL:
            raise ValueError(f"a sketch holds at most {MAX_POINTS_TOTAL} points in all")
        ids = [s.id for s in self.strokes]
        if len(set(ids)) != len(ids):
            raise ValueError("stroke ids must be unique within a sketch")
        return self


class SketchSaveResponse(BaseModel):
    """The revision a save made (or, for a retried save id, already made)."""

    node_id: str
    item_key: str | None = None
    revision: int | None = None
    item_ref: str | None = None
    parent_revision: str | None = None
    save_id: str
    operation_id: str
    duplicate: bool = False
    stroke_count: int
    authority: str = AUTHORITY
    scale: str = SCALE
    plane_state: str = PLANE_STATE
    content_hash: str


class SketchRevisionResponse(BaseModel):
    """The current revision of a project's sketch, with its strokes."""

    node_id: str
    item_key: str | None = None
    revision: int | None = None
    item_ref: str | None = None
    parent_revision: str | None = None
    name: str
    save_id: str | None = None
    strokes: list[dict[str, Any]]
    viewport: dict[str, Any] | None = None
    reference_node_id: str | None = None
    authority: str = AUTHORITY
    scale: str = SCALE
    plane_state: str = PLANE_STATE
    space: str = SKETCH_SPACE
    created_at: str | None = None


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    out = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if out in ("-0", "") else out


def _bounds(strokes: list[SketchStroke]) -> tuple[float, float, float, float]:
    xs = [x for s in strokes for x, _ in s.points]
    ys = [y for s in strokes for _, y in s.points]
    if not xs:
        return (-0.5, -0.5, 0.5, 0.5)
    return (min(xs), min(ys), max(xs), max(ys))


def render_svg(strokes: list[SketchStroke], plane_state: str = PLANE_STATE) -> str:
    """A standalone SVG of the strokes. Deterministic; no script, no external refs."""
    state = plane_state if plane_state.replace("_", "").isalpha() else PLANE_STATE
    plane_text = "plane unassigned" if state == PLANE_STATE else f"plane {state.lower()}"
    x0, y0, x1, y1 = _bounds(strokes)
    # Always frame at least the unit square around the origin, plus a margin.
    x0, y0, x1, y1 = min(x0, -0.5), min(y0, -0.5), max(x1, 0.5), max(y1, 0.5)
    margin = 0.05 * max(x1 - x0, y1 - y0)
    vx, vy = x0 - margin, y0 - margin
    vw, vh = (x1 - x0) + 2 * margin, (y1 - y0) + 2 * margin
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{_fmt(vx)} {_fmt(vy)} {_fmt(vw)} {_fmt(vh)}" '
        f'width="{round(800 * vw / max(vw, vh))}" height="{round(800 * vh / max(vw, vh))}" '
        f'data-authority="human_sketch" data-scale="unknown" data-plane-state="{state}">',
        f"<title>Human sketch: not CAD, scale unknown, {plane_text}</title>",
        f'<rect x="{_fmt(vx)}" y="{_fmt(vy)}" width="{_fmt(vw)}" height="{_fmt(vh)}" '
        'fill="#ffffff"/>',
        '<g fill="none" stroke="#1f2937" stroke-linecap="round" stroke-linejoin="round">',
    ]
    for s in strokes:
        sid = "".join(c for c in s.id if c.isalnum() or c in "-_")[:100]
        width = _fmt(s.width)
        if len(s.points) == 1:
            x, y = s.points[0]
            parts.append(
                f'<circle data-stroke="{sid}" cx="{_fmt(x)}" cy="{_fmt(y)}" r="0.002" '
                f'stroke-width="{width}" vector-effect="non-scaling-stroke"/>'
            )
            continue
        d = "M" + " L".join(f"{_fmt(x)} {_fmt(y)}" for x, y in s.points)
        parts.append(
            f'<path data-stroke="{sid}" data-tool="{s.tool}" d="{d}" '
            f'stroke-width="{width}" vector-effect="non-scaling-stroke"/>'
        )
    parts.append("</g></svg>")
    return "\n".join(parts)


def strokes_json(request: SketchSaveRequest) -> list[dict[str, Any]]:
    """The strokes as stored (JSON-safe, points as ``[x, y]`` pairs)."""
    return [s.model_dump(mode="json", exclude_none=True) for s in request.strokes]


def operation_id_for(save_id: str) -> str:
    return f"job:sketch-{save_id}"


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

#: One save at a time per project, so a retried save id is never recorded twice.
_project_locks: dict[str, asyncio.Lock] = {}


def _lock_for(project_id: str) -> asyncio.Lock:
    lock = _project_locks.get(project_id)
    if lock is None:
        lock = _project_locks[project_id] = asyncio.Lock()
    return lock


async def _find_by_save_id(twin: Any, project_id: UUID, save_id: str) -> Any | None:
    from twin_core.models.enums import WorkProductType

    nodes = await twin.list_work_products(
        work_product_type=WorkProductType.SKETCH_STROKES, project_id=project_id
    )
    for wp in nodes:
        if (wp.metadata or {}).get("save_id") == save_id:
            return wp
    return None


def _response_from_node(wp: Any, *, duplicate: bool) -> SketchSaveResponse:
    meta = wp.metadata or {}
    key = meta.get("item_key")
    rev = meta.get("item_revision")
    return SketchSaveResponse(
        node_id=str(wp.id),
        item_key=key,
        revision=rev,
        item_ref=f"{key}@{rev}" if key and rev else None,
        parent_revision=meta.get("parent_revision"),
        save_id=str(meta.get("save_id") or ""),
        operation_id=operation_id_for(str(meta.get("save_id") or "")),
        duplicate=duplicate,
        stroke_count=int(meta.get("stroke_count") or 0),
        plane_state=str(meta.get("plane_state") or PLANE_STATE),
        content_hash=str(wp.content_hash),
    )


def _store_blob(node_id: str, filename: str, content: bytes, content_type: str) -> str | None:
    try:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key
    except Exception as exc:  # noqa: BLE001 - degrade like the other recorders
        logger.warning(
            "sketch_blob_store_skipped", node_id=node_id, filename=filename, error=str(exc)
        )
        return None


def make_sketch_strokes_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``save(project_id, request)`` bound to a twin and project backend."""

    async def save(project_id: str, request: SketchSaveRequest) -> SketchSaveResponse:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        try:
            pid = UUID(str(project_id))
        except ValueError as exc:
            raise ValueError(f"sketch save: project id {project_id!r} is not a UUID") from exc

        async with _lock_for(str(pid)):
            with tracer.start_as_current_span("twin.save_sketch_strokes") as span:
                span.set_attribute("project_id", str(pid))
                span.set_attribute("sketch.save_id", request.save_id)
                span.set_attribute("sketch.stroke_count", len(request.strokes))

                existing = await _find_by_save_id(twin, pid, request.save_id)
                if existing is not None:
                    span.set_attribute("sketch.duplicate", True)
                    logger.info(
                        "sketch_save_duplicate",
                        project_id=str(pid),
                        save_id=request.save_id,
                        node_id=str(existing.id),
                    )
                    _count("duplicate")
                    return _response_from_node(existing, duplicate=True)

                plan = await plan_definition_revision(
                    twin,
                    item_type=ITEM_TYPE,
                    name=request.name,
                    project_id=pid,
                    default_author="human",
                    item_key=request.item_key,
                    change_reason="sketch saved",
                )
                parent_revision: str | None = None
                parent_node_id: str | None = None
                if plan is not None and plan.prior_node_id is not None and plan.revision > 1:
                    parent_revision = f"{plan.key}@{plan.revision - 1}"
                    parent_node_id = str(plan.prior_node_id)

                # The plane is the plane item's, never the payload's (FORGE-790).
                plane_summary = None
                if plan is not None:
                    from api_gateway.twin.sketch_planes import plane_summary_for_sketch

                    plane_summary = await plane_summary_for_sketch(twin, pid, plan.key)
                plane_state = str((plane_summary or {}).get("state") or PLANE_STATE)
                plane_meta: dict[str, Any] = {"state": plane_state}
                if plane_summary and plane_summary.get("plane_ref"):
                    plane_meta["plane_ref"] = plane_summary["plane_ref"]
                    plane_meta["principal"] = plane_summary.get("principal")

                wp_id = uuid4()
                strokes = strokes_json(request)
                svg = render_svg(request.strokes, plane_state).encode("utf-8")
                strokes_blob = json.dumps(
                    {
                        "space": SKETCH_SPACE,
                        "scale": SCALE,
                        "plane": plane_meta,
                        "strokes": strokes,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                content_hash = hashlib.sha256(strokes_blob).hexdigest()

                svg_key = _store_blob(str(wp_id), SVG_FILENAME, svg, "image/svg+xml")
                strokes_key = _store_blob(
                    str(wp_id), STROKES_FILENAME, strokes_blob, "application/json"
                )

                now = datetime.now(UTC)
                metadata: dict[str, Any] = {
                    "original_filename": SVG_FILENAME,
                    "content_sha256": content_hash,
                    "authored_by": "human",
                    "source": "human",
                    "save_id": request.save_id,
                    "stroke_count": len(strokes),
                    "point_count": sum(len(s.points) for s in request.strokes),
                    "space": SKETCH_SPACE,
                    "space_mapping": (
                        "origin at the viewport centre; the shorter viewport side spans one unit"
                    ),
                    "viewport": request.viewport.model_dump() if request.viewport else None,
                    "reference_node_id": request.reference_node_id,
                    "client_base_revision": request.base_revision,
                    "parent_revision": parent_revision,
                    "parent_node_id": parent_node_id,
                    # Honesty fields. Never overridable by the payload.
                    "authority": AUTHORITY,
                    "scale": SCALE,
                    "plane_state": plane_state,
                    "plane": plane_meta,
                }
                if svg_key:
                    metadata["minio_object_key"] = svg_key
                if strokes_key:
                    metadata["sketch_files"] = {STROKES_FILENAME: strokes_key}
                else:
                    # No blob store: keep the strokes on the node so nothing is lost.
                    metadata["strokes"] = strokes
                if plan is not None:
                    metadata.update(plan.stamp())

                wp = WorkProduct(
                    id=wp_id,
                    name=request.name,
                    type=WorkProductType.SKETCH_STROKES,
                    domain="concept",
                    file_path="",
                    content_hash=content_hash,
                    format="svg",
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by="human",
                    project_id=pid,
                )
                created = await twin.create_work_product(wp)
                node_id = str(getattr(created, "id", wp_id))
                result: dict[str, Any] = {}
                await finish_definition_revision(
                    twin, plan, getattr(created, "id", wp_id), name=request.name, result=result
                )

                if project_backend is not None:
                    try:
                        await project_backend.link_work_product(
                            str(pid), node_id, request.name, ITEM_TYPE
                        )
                    except Exception as exc:  # noqa: BLE001 - link is best-effort
                        logger.warning("sketch_project_link_failed", error=str(exc))

                response = SketchSaveResponse(
                    node_id=node_id,
                    item_key=result.get("item_key"),
                    revision=result.get("revision"),
                    item_ref=result.get("item_ref"),
                    parent_revision=parent_revision,
                    save_id=request.save_id,
                    operation_id=operation_id_for(request.save_id),
                    stroke_count=len(strokes),
                    plane_state=plane_state,
                    content_hash=content_hash,
                )
                span.set_attribute("sketch.item_ref", response.item_ref or "")
                logger.info(
                    "sketch_saved",
                    project_id=str(pid),
                    node_id=node_id,
                    item_ref=response.item_ref,
                    parent_revision=parent_revision,
                    stroke_count=len(strokes),
                    blob_stored=bool(svg_key and strokes_key),
                )
                _count("saved")
                return response

    return save


# ---------------------------------------------------------------------------
# Save with operation events
# ---------------------------------------------------------------------------


def _publish(
    project_id: str, request: SketchSaveRequest, event: str, state: str, **fields: Any
) -> None:
    from api_gateway.operations import publisher as operations

    operations.publish(
        project_id,
        operation_id_for(request.save_id),
        event,
        state,
        event_id=f"sketch-{request.save_id}-{event}",
        domain="sketch",
        **fields,
    )


async def save_sketch_with_operation(
    save: Any, project_id: str, request: SketchSaveRequest
) -> SketchSaveResponse:
    """Run ``save`` and report it as an operation: accepted, then completed or failed."""
    _publish(
        project_id,
        request,
        "accepted",
        "running",
        title="Saving sketch",
        message="Saving your sketch",
    )
    try:
        response: SketchSaveResponse = await save(project_id, request)
    except Exception as exc:
        _count("failed")
        logger.warning(
            "sketch_save_failed",
            project_id=project_id,
            save_id=request.save_id,
            error=str(exc),
        )
        _publish(
            project_id,
            request,
            "failed",
            "failed",
            title="Sketch not saved",
            message="The sketch could not be saved. It is kept in this browser; try again.",
            severity="error",
        )
        raise
    label = response.item_ref or "sketch"
    plane = (
        "plane unassigned"
        if response.plane_state == PLANE_STATE
        else f"plane {response.plane_state.lower()}"
    )
    _publish(
        project_id,
        request,
        "completed",
        "completed",
        title="Sketch saved",
        message=(f"Sketch saved as {label}. A human sketch, not CAD: scale unknown, {plane}."),
        target_revision=response.item_ref,
        base_revision=response.parent_revision,
        artifact_refs=[
            {
                "kind": "work_product",
                "id": response.node_id,
                "label": f"Sketch {label}",
                "authority": "illustrative",
            }
        ],
    )
    return response


async def current_sketch(
    twin: Any, project_id: str, item_key: str | None = None
) -> SketchRevisionResponse | None:
    """The head revision of the project's sketch (the newest item when no key is given)."""
    from twin_core.items import find_item
    from twin_core.models.enums import WorkProductType

    pid = UUID(str(project_id))
    with tracer.start_as_current_span("twin.current_sketch_strokes") as span:
        span.set_attribute("project_id", str(pid))
        key = item_key
        if not key:
            nodes = await twin.list_work_products(
                work_product_type=WorkProductType.SKETCH_STROKES, project_id=pid
            )
            if not nodes:
                return None
            newest = max(nodes, key=lambda n: n.created_at)
            key = (newest.metadata or {}).get("item_key")
            if not key:
                return _revision_from_node(newest)
        item = await find_item(twin, key, pid)
        if item is None or item.head_node_id is None:
            return None
        wp = await twin.get_work_product(item.head_node_id)
        if wp is None:
            return None
        span.set_attribute("sketch.item_ref", f"{key}@{item.head_revision}")
        return _revision_from_node(wp)


def _load_strokes(meta: dict[str, Any]) -> list[dict[str, Any]]:
    inline = meta.get("strokes")
    if isinstance(inline, list):
        return [s for s in inline if isinstance(s, dict)]
    files = meta.get("sketch_files")
    key = files.get(STROKES_FILENAME) if isinstance(files, dict) else None
    if not key:
        return []
    from api_gateway.twin.blob_store import fetch_work_product_blob

    data = json.loads(fetch_work_product_blob(key).decode("utf-8"))
    strokes = data.get("strokes") if isinstance(data, dict) else None
    return [s for s in strokes or [] if isinstance(s, dict)]


def _revision_from_node(wp: Any) -> SketchRevisionResponse:
    meta = wp.metadata or {}
    key = meta.get("item_key")
    rev = meta.get("item_revision")
    return SketchRevisionResponse(
        node_id=str(wp.id),
        item_key=key,
        revision=rev,
        item_ref=f"{key}@{rev}" if key and rev else None,
        parent_revision=meta.get("parent_revision"),
        name=wp.name,
        save_id=meta.get("save_id"),
        strokes=_load_strokes(meta),
        viewport=meta.get("viewport"),
        reference_node_id=meta.get("reference_node_id"),
        plane_state=str(meta.get("plane_state") or PLANE_STATE),
        created_at=wp.created_at.isoformat() if wp.created_at else None,
    )
