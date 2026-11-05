"""The L2 parametric preview and Strategy A manipulation (C3D slice 5).

Two operations over a parametric sketch (``PSK-*``), both on the shared
feature definition (``twin_core.design_ir.feature_definition``):

:func:`preview_definition`
    What the browser draws the L2 preview from: the design IR entities the
    exact path would lower for the sketch's feature, read back as a preview
    definition (profile elements on the plane, the plane's world frame, the
    feature and its amount), plus the CAD item it builds into, that item's
    validated head (whose OCCT tessellation is the L3 display), whether that
    head was built from *this* definition (its ``definition_hash``), the
    stored preview versus exact comparison and, given ``job_id``, the state
    of the exact build the browser scheduled. A parameter the person has not
    given yet is shown at a labelled **assumed** value and the definition is
    not buildable; nothing assumed ever reaches the exact path.

:func:`commit_parameters`
    The only way a manipulation becomes authoritative (spec section 8's
    safety rule): a parameter edit (extrude depth, revolve angle, a
    confirmed radius dimension of a circle, or a confirmed length dimension
    of a line with the browser solver's re-solved geometry) committed as the parametric
    sketch's next revision, pinned to the revision the browser edited
    (``base_revision``; a moved head is refused, 409), classified as a
    ``geometric`` change (slice 3) and re-checked against every accepted
    rule. It then schedules exactly one exact build through the existing
    ``twin.build_exact_cad`` job with the CAD item's head as its base, which
    an unchanged definition skips (SYNC-008). The mesh the person dragged is
    never stored or read.
"""

from __future__ import annotations

import math
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.parametric_preview")

#: The REST job session (shared with the exact CAD routes).
REST_SESSION = "gateway-rest"

_metrics: Any = None


def _record(method: str, *args: Any) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        getattr(_metrics, method)(*args)
    except Exception:  # noqa: BLE001 - a metric never fails the work
        pass


class ParametricPreviewError(ValueError):
    """A request refused. ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class ComparisonTolerance(BaseModel):
    """A component's preview versus exact tolerance (no universal threshold)."""

    abs_mm: float = Field(default=0.25, ge=0, le=1000)
    rel: float = Field(default=0.01, ge=0, le=1)
    volume_rel: float = Field(default=0.02, ge=0, le=1)
    basis: str = Field(default="", max_length=500)


class ParameterCommitRequest(BaseModel):
    """A parameter edit from the viewer: drag handle release or the numeric field."""

    #: The parametric sketch revision the browser edited (its head when it did).
    base_revision: int = Field(ge=1)
    feature_id: str | None = Field(default=None, max_length=200)
    #: ``depth`` (extrude) or ``angle`` (revolve) in the sketch's unit / degrees,
    #: and optionally ``material`` (the plan's other required input).
    values: dict[str, float | str] = Field(default_factory=dict)
    #: Confirmed dimensions: a circle's radius or a line's length,
    #: ``{constraint_id: value}``.
    dimensions: dict[str, float] = Field(default_factory=dict)
    #: With a length dimension: the browser solver's re-solved positions of
    #: the sketch's elements (sketch space, y down), ``{element_id: fields}``.
    #: Only geometric fields of existing elements are taken, and every
    #: accepted rule is checked on the result, so a solve that broke one is
    #: refused rather than stored.
    geometry: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: Literal["drag", "numeric"] = "numeric"
    #: How long the browser took to rebuild the preview mesh, if it measured it.
    preview_ms: float | None = Field(default=None, ge=0, le=60000)
    comparison_tolerance: ComparisonTolerance | None = None
    #: Schedule the exact build (the default); ``False`` only records the revision.
    build: bool = True


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def _pid(project_id: str) -> UUID:
    try:
        return UUID(str(project_id))
    except ValueError as exc:
        raise ParametricPreviewError(f"project id {project_id!r} is not a UUID", 400) from exc


async def _psk(twin: Any, project_id: str, sketch_key: str) -> tuple[Any, Any]:
    from twin_core.items import find_item, parse_item_ref

    try:
        key, _ = parse_item_ref(sketch_key)
    except ValueError as exc:
        raise ParametricPreviewError(str(exc), 400) from exc
    item = await find_item(twin, key, _pid(project_id))
    if item is None or item.item_type != "parametric_sketch" or item.head_node_id is None:
        raise ParametricPreviewError(f"no parametric sketch {key} in this project", 404)
    node = await twin.get_work_product(item.head_node_id)
    if node is None:
        raise ParametricPreviewError(f"{key} has no readable head revision", 404)
    return item, node


def _sketch_of(node: Any) -> Any:
    from api_gateway.twin.exact_cad import load_parametric_sketch
    from twin_core.design_ir.sketch_compiler import ParametricSketch

    try:
        return ParametricSketch.model_validate(load_parametric_sketch(dict(node.metadata or {})))
    except Exception as exc:  # noqa: BLE001 - a stored sketch that does not parse
        raise ParametricPreviewError(
            f"the parametric sketch could not be read: {exc}", 422
        ) from exc


def _feature(sketch: Any, feature_id: str | None) -> Any:
    plan = list(sketch.feature_plan)
    if not plan:
        raise ParametricPreviewError(
            "the parametric sketch has no feature plan (no closed profile)", 422
        )
    chosen = next((f for f in plan if f.id == feature_id), None) if feature_id else plan[0]
    if chosen is None:
        raise ParametricPreviewError(
            f"no feature {feature_id!r} in the plan; choose one of {[f.id for f in plan]}", 404
        )
    return chosen


def _amount_name(op: str) -> str:
    return "depth" if op == "extrude" else "angle"


def _supported_dimensions(sketch: Any) -> list[dict[str, Any]]:
    """Confirmed dimensions a handle or field can edit: a circle's radius, a line's length."""
    from twin_core.design_ir.models import SketchCircle, SketchLine

    by_id = {getattr(el, "id", None): el for el in sketch.elements}
    out: list[dict[str, Any]] = []
    for c in sketch.constraints:
        if c.status != "accepted" or len(c.refs) != 1:
            continue
        el = by_id.get(c.refs[0])
        if c.type == "length" and isinstance(el, SketchLine) and not el.construction:
            out.append(
                {
                    "constraint_id": c.id,
                    "type": "length",
                    "primitive_id": el.id,
                    "value": float(c.value or math.dist(el.start, el.end)),
                    "unit": c.unit or sketch.unit,
                    "source": c.source,
                    # On the plane (v up), like the preview's elements.
                    "start": [float(el.start[0]), -float(el.start[1])],
                    "end": [float(el.end[0]), -float(el.end[1])],
                }
            )
            continue
        if c.type == "radius" and isinstance(el, SketchCircle):
            out.append(
                {
                    "constraint_id": c.id,
                    "type": "radius",
                    "primitive_id": el.id,
                    "value": float(c.value or el.radius),
                    "unit": c.unit or sketch.unit,
                    "source": c.source,
                    "center": [float(el.center[0]), -float(el.center[1])],
                }
            )
    return out


# ---------------------------------------------------------------------------
# The preview definition
# ---------------------------------------------------------------------------


async def preview_definition(
    twin: Any,
    exact: Any,
    *,
    project_id: str,
    sketch_key: str,
    feature_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """The L2 preview definition of a parametric sketch's feature, and its exact status."""
    from api_gateway.twin.exact_cad import (
        PlaneReattachmentRequiredError,
        cad_key_for_sketch,
        parametric_feature_ir,
    )
    from twin_core.design_ir.feature_definition import (
        ASSUMED_DEPTH_FRACTION,
        UNIT_TO_MM,
        FeatureDefinitionError,
        definition_from_entities,
    )
    from twin_core.design_ir.sketch_check import sketch_extent
    from twin_core.representation import preview_descriptor

    with tracer.start_as_current_span("twin.parametric_preview.definition") as span:
        item, node = await _psk(twin, project_id, sketch_key)
        sketch = _sketch_of(node)
        chosen = _feature(sketch, feature_id)
        meta = dict(node.metadata or {})
        ref = f"{item.key}@{item.head_revision}"
        span.set_attribute("sketch.ref", ref)
        span.set_attribute("feature.id", chosen.id)

        name = _amount_name(chosen.op)
        params: dict[str, Any] = {}
        missing: list[str] = []
        for pname, p in chosen.parameters.items():
            given = p.status == "provided" and p.value not in (None, "")
            params[pname] = {
                "value": p.value if given else None,
                "status": p.status,
                "unit": p.unit,
                "question": p.question,
                "assumed": False,
            }
            if not given:
                missing.append(pname)
        amount_param = params.get(name) or {"value": None, "status": "required_input"}
        try:
            amount = float(amount_param["value"]) if amount_param["value"] is not None else None
        except (TypeError, ValueError):
            amount = None
        if amount is None or amount <= 0:
            # Preview only: a labelled assumption, never sent to the exact path.
            extent = sketch_extent(sketch.elements) or 10.0
            amount = (
                360.0
                if chosen.op == "revolve"
                else round(max(1.0, extent * ASSUMED_DEPTH_FRACTION), 1)
            )
            params[name] = {**amount_param, "value": amount, "assumed": True}

        unit = str(sketch.unit)
        scale_assumed = unit not in UNIT_TO_MM
        placement: dict[str, Any] = {"plane": "XY", "offset": 0.0}
        plane_assumed = True
        plane_blocked: str | None = None
        plane_ref: str | None = None
        if sketch.plane_id and unit == "mm":
            try:
                placement, plane_ref = await exact._plane_fields(str(sketch.plane_id), project_id)  # noqa: SLF001
                plane_assumed = False
            except PlaneReattachmentRequiredError as exc:
                plane_blocked = str(exc)
            except Exception as exc:  # noqa: BLE001 - the preview falls back to XY, labelled
                plane_blocked = str(exc)[:300]
        # The preview reads the IR the exact path lowers. Unknown scale is not
        # CAD, so the entity is made as if in millimetres and labelled.
        as_mm = sketch if unit == "mm" else sketch.model_copy(update={"unit": "mm"})
        entities = parametric_feature_ir(as_mm, chosen, amount, placement)
        normalized = exact.parse_source({"entities": entities}, None)
        try:
            definition = definition_from_entities(
                normalized["design_ir"]["entities"], scale_to_mm=UNIT_TO_MM.get(unit, 1.0)
            )
        except FeatureDefinitionError as exc:
            raise ParametricPreviewError(f"this feature cannot be previewed: {exc}", 422) from exc
        buildable = (
            not missing
            and unit == "mm"
            and not plane_assumed
            and bool(meta.get("approved"))
            and plane_blocked is None
        )
        if not buildable:
            # Only a definition the exact path would build has a hash to match.
            definition["definition_hash"] = None

        cad_key = cad_key_for_sketch(item.key)
        cad = await _cad_view(twin, exact, project_id, cad_key, psk_meta=meta)
        if definition["definition_hash"] and cad.get("definition_hash"):
            cad["matches_definition"] = cad["definition_hash"] == definition["definition_hash"]
        else:
            cad["matches_definition"] = False

        handles: list[dict[str, Any]] = [
            {"kind": f"{chosen.op}_{name}", "parameter": name, "axis": "normal"}
        ]
        dims = _supported_dimensions(sketch)
        handles += [
            {
                "kind": d["type"],
                "constraint_id": d["constraint_id"],
                "primitive_id": d["primitive_id"],
            }
            for d in dims
        ]
        from twin_core.design_ir.feature_definition import PreviewSolid

        try:
            holes = PreviewSolid.from_definition(definition).through_holes
        except FeatureDefinitionError:
            holes = 0
        build = _job_view(exact, job_id) if job_id else None
        return {
            **definition,
            "sketch_key": item.key,
            "sketch_ref": ref,
            "base_revision": item.head_revision,
            "feature_id": chosen.id,
            "features": [{"id": f.id, "op": f.op} for f in sketch.feature_plan],
            "parameters": params,
            "missing": missing,
            "unit": unit,
            "scale_assumed": scale_assumed,
            "plane_ref": plane_ref,
            "plane_assumed": plane_assumed,
            "plane_blocked": plane_blocked,
            "approved": bool(meta.get("approved")),
            "buildable": buildable,
            "dimensions": dims,
            "handles": handles,
            # What the browser's solver re-solves a length drag over: the
            # sketch's elements (sketch space) and its accepted rules.
            "solver_sketch": {
                "elements": [el.model_dump(mode="json") for el in sketch.elements],
                "constraints": [
                    c.model_dump(mode="json") for c in sketch.constraints if c.status == "accepted"
                ],
            },
            "intended_through_holes": holes,
            "comparison_tolerance": meta.get("comparison_tolerance"),
            "representation": preview_descriptor(
                source_revision=ref,
                component_ids=[str(meta["component_id"])] if meta.get("component_id") else [],
            ),
            "cad": cad,
            "build": build,
        }


async def _cad_view(
    twin: Any, exact: Any, project_id: str, cad_key: str, *, psk_meta: dict[str, Any]
) -> dict[str, Any]:
    """The CAD item a sketch builds into: its head, validated revision and comparison."""
    from api_gateway.twin.exact_cad import COMPARISON_KEY
    from twin_core.geometry_state import (
        EXACT_ATTEMPTS_KEY,
        EXACT_SOURCE_KEY,
        geometry_state_of,
        validated_states,
    )
    from twin_core.items import find_item

    cad = await find_item(twin, cad_key, _pid(project_id))
    if cad is None or cad.head_node_id is None:
        attempts = list(psk_meta.get(EXACT_ATTEMPTS_KEY) or [])
        return {
            "item_key": cad_key,
            "exists": False,
            "head_revision": None,
            "node_id": None,
            "geometry_state": None,
            "definition_hash": None,
            "comparison": None,
            "latest_attempt": attempts[-1] if attempts else None,
            "last_validated": None,
        }
    head = await twin.get_work_product(cad.head_node_id)
    meta = dict(getattr(head, "metadata", None) or {})
    state = geometry_state_of(head.type.value, meta) if head is not None else None
    source = meta.get(EXACT_SOURCE_KEY)
    attempts = list(meta.get(EXACT_ATTEMPTS_KEY) or [])
    last_validated: dict[str, Any] | None
    if state in validated_states():
        last_validated = {
            "revision": cad.head_revision,
            "node_id": str(cad.head_node_id),
            "item_ref": f"{cad.key}@{cad.head_revision}",
            "is_head": True,
        }
    else:
        last_validated = await exact._last_validated(cad)  # noqa: SLF001
    return {
        "item_key": cad.key,
        "exists": True,
        "head_revision": cad.head_revision,
        "node_id": str(cad.head_node_id),
        "geometry_state": state.value if state else None,
        "definition_hash": source.get("definition_hash") if isinstance(source, dict) else None,
        "comparison": meta.get(COMPARISON_KEY),
        "latest_attempt": attempts[-1] if attempts else None,
        "last_validated": last_validated,
    }


def _job_view(exact: Any, job_id: str) -> dict[str, Any]:
    try:
        job = exact._job_runner().get(job_id)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - an unknown runner is an unknown job
        job = None
    if job is None:
        return {"job_id": job_id, "status": "unknown"}
    out: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "operation_id": f"job:{job.job_id}",
        "base_revision": job.base_revision,
        "error": job.error,
    }
    if isinstance(job.result, dict):
        out["result"] = {
            k: job.result.get(k)
            for k in ("item_ref", "node_id", "validated", "skipped", "preview_comparison")
            if k in job.result
        }
    return out


# ---------------------------------------------------------------------------
# The commit
# ---------------------------------------------------------------------------


def _number(name: str, raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError) as exc:
        raise ParametricPreviewError(f"{name} must be a number, not {raw!r}", 422) from exc
    if not math.isfinite(v) or v <= 0:
        raise ParametricPreviewError(f"{name} must be above zero", 422)
    return v


async def commit_parameters(
    twin: Any,
    exact: Any,
    *,
    project_id: str,
    sketch_key: str,
    request: ParameterCommitRequest,
    session_id: str = REST_SESSION,
) -> dict[str, Any]:
    """Commit a parameter edit as the sketch's next revision, then schedule one exact build."""
    from api_gateway.twin.change_classification import (
        HANDLING,
        PLAIN_MESSAGE,
        ChangeClass,
        ChangeClassification,
        turn_classification,
    )
    from api_gateway.twin.sketch_compiler_recorder import (
        PARAMETRIC_FILE,
        PARAMETRIC_TYPE,
        _lock_for,
        _write,
        render_primitives_svg,
    )
    from twin_core.consistency.record_pins import PINS_KEY
    from twin_core.design_ir.models import SketchCircle
    from twin_core.design_ir.sketch_check import check_accepted_constraints
    from twin_core.design_ir.sketch_compiler import FeatureParameter
    from twin_core.items import ItemRevisionConflictError
    from twin_core.models.enums import WorkProductType

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.parametric_preview.commit") as span:
        span.set_attribute("sketch.key", sketch_key)
        span.set_attribute("commit.source", request.source)
        async with _lock_for(str(pid)):
            item, node = await _psk(twin, project_id, sketch_key)
            if request.base_revision != item.head_revision:
                _record("record_manipulation_commit", request.source, "stale")
                raise ItemRevisionConflictError(
                    f"{item.key} is at @{item.head_revision}, not @{request.base_revision}; "
                    "the preview was edited from an older revision"
                )
            meta = dict(node.metadata or {})
            if not meta.get("approved"):
                _record("record_manipulation_commit", request.source, "refused")
                raise ParametricPreviewError(
                    "approve the parametric sketch before changing its parameters"
                )
            sketch = _sketch_of(node)
            chosen = _feature(sketch, request.feature_id)
            if request.preview_ms is not None:
                _record("record_preview_rebuild", chosen.op, request.preview_ms / 1000.0)
            amount_name = _amount_name(chosen.op)
            allowed = {amount_name, "material"}
            unknown = sorted(set(request.values) - allowed)
            if unknown:
                _record("record_manipulation_commit", request.source, "refused")
                raise ParametricPreviewError(
                    f"{chosen.id} has no parameter {unknown[0]!r} that can be set here; "
                    f"it takes {sorted(allowed)}",
                    422,
                )
            if not request.values and not request.dimensions:
                raise ParametricPreviewError("nothing to change", 422)

            stated: list[str] = []
            plan = []
            for f in sketch.feature_plan:
                if f.id != chosen.id:
                    plan.append(f)
                    continue
                parameters = dict(f.parameters)
                for pname, raw in request.values.items():
                    prior = parameters.get(pname) or FeatureParameter()
                    if pname == "material":
                        value: float | str = str(raw).strip()
                        if not value:
                            raise ParametricPreviewError("material must not be empty", 422)
                    else:
                        value = _number(pname, raw)
                        if pname == "angle" and value > 360:
                            raise ParametricPreviewError("angle must be at most 360", 422)
                    parameters[pname] = prior.model_copy(
                        update={"status": "provided", "value": value}
                    )
                    stated.append(f"{pname} {value}{(' ' + prior.unit) if prior.unit else ''}")
                plan.append(f.model_copy(update={"parameters": parameters}))

            elements = list(sketch.elements)
            constraints = list(sketch.constraints)
            supported = {d["constraint_id"]: d for d in _supported_dimensions(sketch)}
            lengths = [
                supported[cid]
                for cid in request.dimensions
                if cid in supported and supported[cid]["type"] == "length"
            ]
            if request.geometry and not lengths:
                _record("record_manipulation_commit", request.source, "refused")
                raise ParametricPreviewError(
                    "solved geometry is only taken with a line's length dimension", 422
                )
            if lengths:
                unsent = [
                    d["primitive_id"] for d in lengths if d["primitive_id"] not in request.geometry
                ]
                if unsent:
                    _record("record_manipulation_commit", request.source, "refused")
                    raise ParametricPreviewError(
                        "a length edit carries the solved sketch geometry; none was sent for "
                        + ", ".join(unsent),
                        422,
                    )
                elements = _apply_geometry(elements, request.geometry)
            for cid, raw in request.dimensions.items():
                dim = supported.get(cid)
                if dim is None:
                    _record("record_manipulation_commit", request.source, "refused")
                    raise ParametricPreviewError(
                        f"dimension {cid!r} cannot be edited here: only an accepted radius "
                        "of a circle or length of a line maps to a handle",
                        422,
                    )
                if dim["type"] == "length":
                    length = _number(f"dimension {cid}", raw)
                    constraints = [
                        c.model_copy(update={"value": length, "source": "human_confirmed"})
                        if c.id == cid
                        else c
                        for c in constraints
                    ]
                    stated.append(f"length {length} {sketch.unit}")
                    continue
                radius = _number(f"dimension {cid}", raw)
                elements = [
                    el.model_copy(update={"radius": radius})
                    if isinstance(el, SketchCircle) and el.id == dim["primitive_id"]
                    else el
                    for el in elements
                ]
                constraints = [
                    c.model_copy(update={"value": radius, "source": "human_confirmed"})
                    if c.id == cid
                    else c
                    for c in constraints
                ]
                stated.append(f"radius {radius} {sketch.unit}")
            failing = [c for c in check_accepted_constraints(elements, constraints) if not c.holds]
            if failing:
                _record("record_manipulation_commit", request.source, "refused")
                raise ParametricPreviewError(
                    "the new value breaks accepted rules: "
                    + "; ".join(f"{c.constraint_id} ({c.reason})" for c in failing[:5])
                )
            updated = sketch.model_copy(
                update={"elements": elements, "constraints": constraints, "feature_plan": plan}
            )
            content = updated.model_dump(mode="json")

            # Slice 3: a parameter edit is a geometric change, decided by the
            # person's own action rather than inferred from words.
            classification = ChangeClassification(
                change_class=ChangeClass.GEOMETRIC,
                confidence=1.0,
                stage="person",
                target_feature=chosen.id,
                handling=HANDLING[ChangeClass.GEOMETRIC],
                message=PLAIN_MESSAGE[ChangeClass.GEOMETRIC],
                signals=[f"direct manipulation ({request.source})"],
                stated_dimensions=stated,
            )
            base_ref = f"{item.key}@{item.head_revision}"
            carry = {
                k: meta[k]
                for k in (
                    "interpretation_id",
                    "interpretation_ref",
                    "sketch_ref",
                    "sketch_item_key",
                    "plane_id",
                    "unit",
                    "accepted_count",
                    "dropped_count",
                    "authority",
                    "approved",
                    "approved_at",
                    "approved_by",
                    "component_id",
                    "comparison_tolerance",
                    PINS_KEY,
                )
                if k in meta
            }
            if request.comparison_tolerance is not None:
                tol = request.comparison_tolerance.model_dump()
                if not tol.get("basis"):
                    tol["basis"] = "configured for this component"
                carry["comparison_tolerance"] = tol
            with turn_classification(classification):
                wp, result = await _write(
                    twin,
                    pid,
                    item_type=PARAMETRIC_TYPE,
                    wp_type=WorkProductType.PARAMETRIC_SKETCH,
                    # Pinned: the twin refuses it if the head moved meanwhile.
                    item_key=base_ref,
                    name=node.name,
                    change_reason="parameter edit: " + ", ".join(stated),
                    content=content,
                    content_file=PARAMETRIC_FILE,
                    svg=render_primitives_svg(updated.elements, title=node.name),
                    metadata={
                        **carry,
                        "parametric_sketch": content,
                        "feature_plan": [p.model_dump(mode="json") for p in plan],
                        "approved_via": "parameter_edit",
                        "parameter_edit": {
                            "base_revision": base_ref,
                            "feature_id": chosen.id,
                            "values": dict(request.values),
                            "dimensions": dict(request.dimensions),
                            "solved_elements": sorted(request.geometry),
                            "source": request.source,
                            "authority": "human_confirmed",
                        },
                        "change_classification": classification.model_dump(mode="json"),
                    },
                    depends_on=[],
                )
                new_ref = str(result.get("item_ref") or f"{item.key}@{item.head_revision + 1}")
                span.set_attribute("sketch.new_ref", new_ref)
                _record("record_manipulation_commit", request.source, "committed")
                logger.info(
                    "parametric_parameters_committed",
                    sketch_ref=new_ref,
                    base=base_ref,
                    feature_id=chosen.id,
                    source=request.source,
                    stated=stated,
                )
                build: dict[str, Any] | None = None
                if request.build:
                    build = await _schedule_build(
                        exact,
                        project_id=project_id,
                        sketch_key=item.key,
                        feature_id=chosen.id,
                        session_id=session_id,
                    )
        return {
            "sketch_key": item.key,
            "sketch_ref": new_ref,
            "revision": result.get("revision"),
            "node_id": str(wp.id),
            "base_revision": base_ref,
            "feature_id": chosen.id,
            "classification": {
                "change_class": classification.change_class.value,
                "message": classification.message,
                "stage": classification.stage,
            },
            "build": build,
        }


#: The fields of each element type a re-solve may move.
_GEOMETRY_FIELDS: dict[str, tuple[str, ...]] = {
    "line": ("start", "end"),
    "circle": ("center", "radius"),
    "arc": ("center", "radius", "start_angle", "end_angle"),
    "ellipse": ("center", "major_radius", "minor_radius", "rotation"),
    "spline": ("points",),
}


def _apply_geometry(elements: list[Any], geometry: dict[str, dict[str, Any]]) -> list[Any]:
    """The elements with the solver's new positions; only geometric fields move."""
    by_id = {getattr(el, "id", None): el for el in elements}
    unknown = sorted(k for k in geometry if k not in by_id)
    if unknown:
        raise ParametricPreviewError(f"no element {unknown[0]!r} in this sketch", 422)
    out = []
    for el in elements:
        moved = geometry.get(getattr(el, "id", None) or "")
        if not moved:
            out.append(el)
            continue
        fields = _GEOMETRY_FIELDS.get(str(el.type), ())
        update = {k: v for k, v in moved.items() if k in fields}
        try:
            out.append(type(el).model_validate({**el.model_dump(), **update}))
        except Exception as exc:  # noqa: BLE001 - a malformed solve is the caller's
            raise ParametricPreviewError(
                f"the solved geometry of {el.id} is not valid: {exc}", 422
            ) from exc
    return out


async def _schedule_build(
    exact: Any, *, project_id: str, sketch_key: str, feature_id: str, session_id: str
) -> dict[str, Any]:
    """One exact build of the sketch's CAD item from its head, or why none started."""
    from api_gateway.twin.exact_cad import ExactCadError as _ExactCadError
    from api_gateway.twin.exact_cad import (
        PlaneReattachmentRequiredError,
        RequiredInputMissingError,
    )

    try:
        reply = await exact.start_build(
            project_id=project_id,
            item_key=sketch_key,
            feature_id=feature_id,
            session_id=session_id,
        )
    except RequiredInputMissingError as exc:
        return {"status": "needs_input", "job_id": None, "detail": str(exc)}
    except PlaneReattachmentRequiredError as exc:
        return {"status": "blocked", "job_id": None, "detail": str(exc)}
    except _ExactCadError as exc:
        return {"status": "refused", "job_id": None, "detail": str(exc)}
    out = {
        "status": reply.get("status"),
        "job_id": reply.get("job_id"),
        "operation_id": f"job:{reply['job_id']}" if reply.get("job_id") else None,
        "skipped": bool(reply.get("skipped")),
        "reused": bool(reply.get("reused")),
    }
    if reply.get("skipped"):
        out["detail"] = "the definition is unchanged; the validated model is kept"
    return out


__all__ = [
    "ComparisonTolerance",
    "ParameterCommitRequest",
    "ParametricPreviewError",
    "commit_parameters",
    "preview_definition",
]
