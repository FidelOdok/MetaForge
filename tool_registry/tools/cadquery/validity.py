"""B-Rep validity and configured STEP export for the CadQuery adapter (CDE slice 6).

Two operations, both on STEP bytes so the gateway never needs a shared
volume with the adapter:

* :func:`validate_step` reads a STEP with OCCT's STEP reader (the same reader
  ``cq.importers.importStep`` uses) and checks the B-Rep: ``BRepCheck``
  validity (``Shape.isValid()``), at least one solid where a solid is
  expected, every solid closed, every solid's volume above zero, and no free
  edges (an edge bounded by one face only) on a solid. The verdict is
  ``pass``, ``warn``, ``fail`` or ``not_evaluated``; a check that could not
  run is never a pass (FRS CHG-005).
* :func:`export_step` writes a STEP with the schema this adapter is
  configured for (``CADQUERY_STEP_SCHEMA``: ``AP214`` by default, or
  ``AP242``) and reports the schema the file actually declares in its
  header, read back from the written file. Nothing claims AP242 unless the
  file says so.

The verdict rules (:func:`classify`) and the header parse
(:func:`schema_from_step_text`) are pure, so they are tested without a CAD
kernel.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cadquery.validity")

PASS = "pass"
WARN = "warn"
FAIL = "fail"
NOT_EVALUATED = "not_evaluated"

#: ``CADQUERY_STEP_SCHEMA`` value -> OCCT ``write.step.schema`` value.
STEP_SCHEMAS: dict[str, str] = {"AP214": "AP214IS", "AP242": "AP242DIS"}
STEP_SCHEMA_ENV = "CADQUERY_STEP_SCHEMA"
DEFAULT_STEP_SCHEMA = "AP214"
KERNEL = "OCCT via CadQuery"

_RANK = {PASS: 0, NOT_EVALUATED: 1, WARN: 2, FAIL: 3}


def configured_schema(env: dict[str, str] | None = None) -> str:
    """The STEP schema this adapter writes: ``AP214`` (default) or ``AP242``."""
    raw = ((env if env is not None else os.environ).get(STEP_SCHEMA_ENV) or "").strip().upper()
    return raw if raw in STEP_SCHEMAS else DEFAULT_STEP_SCHEMA


def schema_from_step_text(text: str) -> str | None:
    """The application protocol a STEP file declares in its ``FILE_SCHEMA``."""
    match = re.search(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    declared = match.group(1).upper()
    if "AP242" in declared:
        return "AP242"
    if "AUTOMOTIVE_DESIGN" in declared or "AP214" in declared:
        return "AP214"
    if "CONFIG_CONTROL_DESIGN" in declared or "AP203" in declared:
        return "AP203"
    return declared.strip().strip("'")[:80] or None


def _finding(check: str, outcome: str, detail: str) -> dict[str, str]:
    return {"check": check, "outcome": outcome, "detail": detail}


def classify(
    measures: dict[str, Any], *, expect_solid: bool = True, expect_single_solid: bool = False
) -> tuple[str, list[dict[str, str]]]:
    """``(outcome, findings)`` from a shape's measures. Pure.

    ``measures`` keys: ``is_valid`` (bool or None when BRepCheck could not
    run), ``solid_count``, ``closed_solid_count``, ``solid_volumes_mm3``
    (list), ``free_edge_count`` (int or None when it could not be counted).
    The worst finding decides: any fail is ``fail``; else any warn is
    ``warn``; else anything not evaluated is ``not_evaluated``; only a full
    set of passing checks is ``pass``.
    """
    findings: list[dict[str, str]] = []
    is_valid = measures.get("is_valid")
    if is_valid is None:
        findings.append(_finding("brep_check", NOT_EVALUATED, "BRepCheck could not run"))
    elif is_valid:
        findings.append(_finding("brep_check", PASS, "BRepCheck_Analyzer reports a valid shape"))
    else:
        findings.append(_finding("brep_check", FAIL, "BRepCheck_Analyzer reports an invalid shape"))

    solids = int(measures.get("solid_count") or 0)
    closed = int(measures.get("closed_solid_count") or 0)
    if expect_solid and solids == 0:
        findings.append(_finding("solids", FAIL, "no solid in the shape; a solid was expected"))
    elif expect_single_solid and solids > 1:
        findings.append(_finding("solids", WARN, f"{solids} solids where one was expected"))
    else:
        findings.append(_finding("solids", PASS, f"{solids} solid(s)"))
    if solids:
        if closed < solids:
            findings.append(
                _finding("closed", FAIL, f"{solids - closed} of {solids} solid(s) are not closed")
            )
        else:
            findings.append(_finding("closed", PASS, f"all {solids} solid(s) closed"))

    volumes = [float(v) for v in measures.get("solid_volumes_mm3") or []]
    if solids:
        empty = [v for v in volumes if not v > 0.0]
        if len(volumes) < solids:
            findings.append(_finding("volume", NOT_EVALUATED, "solid volumes could not be read"))
        elif empty:
            findings.append(
                _finding("volume", FAIL, f"{len(empty)} solid(s) with zero or negative volume")
            )
        else:
            findings.append(_finding("volume", PASS, f"total {sum(volumes):.3f} mm3"))

    free = measures.get("free_edge_count")
    if solids:
        if free is None:
            findings.append(
                _finding("free_edges", NOT_EVALUATED, "free edges could not be counted")
            )
        elif int(free) > 0:
            findings.append(
                _finding("free_edges", FAIL, f"{int(free)} free edge(s) on a solid (open boundary)")
            )
        else:
            findings.append(_finding("free_edges", PASS, "no free edges on any solid"))

    outcome = max((f["outcome"] for f in findings), key=lambda o: _RANK[o], default=NOT_EVALUATED)
    return outcome, findings


def _require_cq() -> Any:
    from tool_registry.tools.cadquery.operations import HAS_CADQUERY, CadqueryNotAvailableError

    if not HAS_CADQUERY:
        raise CadqueryNotAvailableError
    import cadquery as cq  # type: ignore[import-untyped]

    return cq


def flatten_compounds(
    roots: list[Any],
    *,
    is_compound: Callable[[Any], bool],
    children: Callable[[Any], list[Any]],
) -> list[Any]:
    """The non-compound shapes under ``roots``, depth first. Pure.

    A compound (at any depth) is replaced by its children; everything else is
    kept as it is, so a solid stays a solid and an open shell or a bare face
    stays what it is and still fails the validity check.
    """
    leaves: list[Any] = []
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        if is_compound(node):
            stack.extend(reversed(children(node)))
        else:
            leaves.append(node)
    return leaves


def _occt_tree() -> tuple[Callable[[Any], bool], Callable[[Any], list[Any]]]:
    """``(is_compound, children)`` on raw OCCT shapes (``TopoDS_Shape``)."""
    from OCP.TopAbs import TopAbs_COMPOUND  # type: ignore[import-not-found]
    from OCP.TopoDS import TopoDS_Iterator  # type: ignore[import-not-found]

    def is_compound(shape: Any) -> bool:
        return bool(shape.ShapeType() == TopAbs_COMPOUND)

    def children(shape: Any) -> list[Any]:
        out = []
        it = TopoDS_Iterator(shape)
        while it.More():
            out.append(it.Value())
            it.Next()
        return out

    return is_compound, children


def step_shape(cq: Any, obj: Any) -> Any:
    """The shape to write as STEP for a script, Workplane, Shape or Assembly.

    CadQuery can hand back a ``Compound`` object whose underlying OCCT shape is
    really a solid (``faces(">Z").shell(-1)`` does). ``cq.exporters.export``
    flattens a Workplane by iterating it, which on such an object yields the
    solid's *shell*, so the STEP held a shell and read back with no solid.
    Here every compound is walked on the OCCT shape itself and each leaf is
    cast to its real type: one leaf is written as it is, several as a compound
    of them. Returns ``None`` when ``obj`` holds no shape at all.
    """
    workplane_t, shape_t, assembly_t = (
        t if isinstance(t, type) else None
        for t in (getattr(cq, n, None) for n in ("Workplane", "Shape", "Assembly"))
    )
    if shape_t is None:
        return None
    if workplane_t is not None and isinstance(obj, workplane_t):
        roots = [v.wrapped for v in obj.vals() if isinstance(v, shape_t)]
    elif isinstance(obj, shape_t):
        roots = [obj.wrapped]
    elif assembly_t is not None and isinstance(obj, assembly_t):
        roots = [obj.toCompound().wrapped]
    else:
        return None

    is_compound, children = _occt_tree()
    leaves = flatten_compounds(
        [r for r in roots if not r.IsNull()], is_compound=is_compound, children=children
    )
    shapes = [cq.Shape.cast(leaf) for leaf in leaves]
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def write_step(cq: Any, obj: Any, path: str) -> int:
    """Write ``obj`` to ``path`` as STEP with its solids kept as solids.

    Returns the number of solids written. Falls back to
    ``cq.exporters.export`` only for an object :func:`step_shape` cannot read.
    """
    shape = step_shape(cq, obj)
    if shape is None:
        cq.exporters.export(obj, path)
        return 0
    shape.exportStep(path)
    solids = len(shape.Solids())
    logger.debug("cadquery_step_written", path=path, solids=solids)
    return solids


def _shape_of(cq: Any, workplane: Any) -> Any:
    objs = [o for o in workplane.vals() if hasattr(o, "wrapped")]
    if not objs:
        return None
    return objs[0] if len(objs) == 1 else cq.Compound.makeCompound(objs)


def _free_edge_count(solid: Any) -> int:
    from OCP.BRep import BRep_Tool  # type: ignore[import-not-found]
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE  # type: ignore[import-not-found]
    from OCP.TopExp import TopExp  # type: ignore[import-not-found]
    from OCP.TopoDS import TopoDS  # type: ignore[import-not-found]
    from OCP.TopTools import (  # type: ignore[import-not-found]
        TopTools_IndexedDataMapOfShapeListOfShape,
    )

    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(solid.wrapped, TopAbs_EDGE, TopAbs_FACE, edge_faces)
    free = 0
    for index in range(1, edge_faces.Extent() + 1):
        edge = TopoDS.Edge_s(edge_faces.FindKey(index))
        if BRep_Tool.Degenerated_s(edge):
            continue
        if edge_faces.FindFromIndex(index).Size() < 2:
            free += 1
    return free


def measure(shape: Any) -> dict[str, Any]:
    """Validity measures of a CadQuery shape. Each part degrades to "unknown"."""
    out: dict[str, Any] = {}
    try:
        out["is_valid"] = bool(shape.isValid())
    except Exception as exc:  # noqa: BLE001 - reported as not evaluated
        logger.warning("cadquery_brepcheck_failed", error=str(exc))
        out["is_valid"] = None
    solids = list(shape.Solids()) if hasattr(shape, "Solids") else []
    out["solid_count"] = len(solids)
    closed = 0
    volumes: list[float] = []
    free_total: int | None = 0
    for solid in solids:
        try:
            shells = list(solid.Shells())
            if shells and all(sh.Closed() for sh in shells):
                closed += 1
        except Exception as exc:  # noqa: BLE001 - counts as not closed
            logger.warning("cadquery_closed_check_failed", error=str(exc))
        try:
            volumes.append(float(solid.Volume()))
        except Exception as exc:  # noqa: BLE001 - volume unknown
            logger.warning("cadquery_volume_failed", error=str(exc))
        if free_total is not None:
            try:
                free_total += _free_edge_count(solid)
            except Exception as exc:  # noqa: BLE001 - free edges unknown
                logger.warning("cadquery_free_edges_failed", error=str(exc))
                free_total = None
    out["closed_solid_count"] = closed
    out["solid_volumes_mm3"] = [round(v, 6) for v in volumes]
    out["volume_mm3"] = round(sum(volumes), 6)
    out["free_edge_count"] = free_total
    try:
        bb = shape.BoundingBox()
        out["bounding_box"] = {
            "min_x": bb.xmin,
            "min_y": bb.ymin,
            "min_z": bb.zmin,
            "max_x": bb.xmax,
            "max_y": bb.ymax,
            "max_z": bb.zmax,
        }
    except Exception:  # noqa: BLE001 - an empty shape has no box
        out["bounding_box"] = None
    return out


def _decode(step_base64: str) -> bytes:
    try:
        content = base64.b64decode(step_base64, validate=True)
    except ValueError as exc:
        raise ValueError("step_base64 is not valid base64") from exc
    if not content:
        raise ValueError("step_base64 decodes to an empty file")
    return content


def validate_step(
    step_base64: str,
    *,
    work_dir: str,
    expect_solid: bool = True,
    expect_single_solid: bool = False,
) -> dict[str, Any]:
    """Read a STEP and judge its B-Rep. See the module docstring."""
    content = _decode(step_base64)
    started = time.monotonic()
    with tracer.start_as_current_span("cadquery.validate_shape") as span:
        try:
            cq = _require_cq()
        except Exception as exc:  # noqa: BLE001 - no kernel: nothing was checked
            return {
                "outcome": NOT_EVALUATED,
                "findings": [_finding("kernel", NOT_EVALUATED, str(exc))],
                "kernel": KERNEL,
            }
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".step", dir=work_dir, delete=False) as handle:
            handle.write(content)
            path = handle.name
        try:
            try:
                shape = _shape_of(cq, cq.importers.importStep(path))
            except Exception as exc:  # noqa: BLE001 - an unreadable STEP fails the check
                shape = None
                read_error = str(exc)
            else:
                read_error = "the STEP holds no shape"
            if shape is None:
                outcome, findings = FAIL, [_finding("read", FAIL, read_error[:300])]
                measures: dict[str, Any] = {}
            else:
                measures = measure(shape)
                outcome, findings = classify(
                    measures, expect_solid=expect_solid, expect_single_solid=expect_single_solid
                )
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass
        duration = round(time.monotonic() - started, 4)
        span.set_attribute("validity.outcome", outcome)
        span.set_attribute("validity.duration_s", duration)
        logger.info(
            "cadquery_shape_validated",
            outcome=outcome,
            solids=measures.get("solid_count"),
            free_edges=measures.get("free_edge_count"),
            duration_s=duration,
        )
        return {
            "outcome": outcome,
            "findings": findings,
            **measures,
            "kernel": KERNEL,
            "duration_s": duration,
        }


def export_step(step_base64: str, *, work_dir: str, schema: str | None = None) -> dict[str, Any]:
    """Write ``step_base64``'s shape back out as STEP with the configured schema.

    Returns the new file, the schema asked for, the schema the written file
    declares, and the source shape's measures (for the round-trip compare).
    """
    content = _decode(step_base64)
    wanted = (schema or configured_schema()).upper()
    if wanted not in STEP_SCHEMAS:
        raise ValueError(f"schema must be one of {sorted(STEP_SCHEMAS)}")
    cq = _require_cq()
    from OCP.IFSelect import IFSelect_RetDone  # type: ignore[import-not-found]
    from OCP.Interface import Interface_Static  # type: ignore[import-not-found]
    from OCP.STEPControl import (  # type: ignore[import-not-found]
        STEPControl_AsIs,
        STEPControl_Controller,
        STEPControl_Writer,
    )

    started = time.monotonic()
    with tracer.start_as_current_span("cadquery.export_step") as span:
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".step", dir=work_dir, delete=False) as handle:
            handle.write(content)
            source_path = handle.name
        out_path = source_path[:-5] + "-export.step"
        try:
            shape = step_shape(cq, cq.importers.importStep(source_path))
            if shape is None:
                raise ValueError("the STEP holds no shape to export")
            source = measure(shape)
            STEPControl_Controller.Init_s()
            Interface_Static.SetCVal_s("write.step.schema", STEP_SCHEMAS[wanted])
            writer = STEPControl_Writer()
            writer.Transfer(shape.wrapped, STEPControl_AsIs)
            if writer.Write(out_path) != IFSelect_RetDone:
                raise RuntimeError("the STEP writer did not complete")
            written = Path(out_path).read_bytes()
        finally:
            for p in (source_path, out_path):
                try:
                    Path(p).unlink()
                except OSError:
                    pass
        declared = schema_from_step_text(written[:4096].decode("ascii", errors="replace"))
        duration = round(time.monotonic() - started, 4)
        span.set_attribute("step.schema", declared or "unknown")
        span.set_attribute("step.size_bytes", len(written))
        logger.info(
            "cadquery_step_exported",
            schema_requested=wanted,
            schema=declared,
            size_bytes=len(written),
            duration_s=duration,
        )
        return {
            "step_base64": base64.b64encode(written).decode("ascii"),
            "size_bytes": len(written),
            "schema_requested": wanted,
            "schema": declared,
            "occt_schema_value": STEP_SCHEMAS[wanted],
            "source": source,
            "kernel": KERNEL,
            "duration_s": duration,
        }


__all__ = [
    "DEFAULT_STEP_SCHEMA",
    "FAIL",
    "NOT_EVALUATED",
    "PASS",
    "STEP_SCHEMAS",
    "WARN",
    "classify",
    "configured_schema",
    "export_step",
    "flatten_compounds",
    "measure",
    "schema_from_step_text",
    "step_shape",
    "validate_step",
    "write_step",
]
