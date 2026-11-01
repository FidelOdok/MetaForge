"""Design-for-manufacture evidence recorder: 3D-print overhang detection
(FORGE-273, gap G-D5).

Wires a real ``freecad.list_named_faces`` mesh face table (FORGE-277) to a
real Evidence entity recorded against a work product -- the same "run a
real tool, record its real structured output as graph-checkable Evidence"
pattern ``api_gateway/twin/thermal_evidence.py`` established (FORGE-297),
applied to a third data source: geometric mesh analysis rather than a
CalculiX solver call.

Deliberately one process (3D-print overhang), one rule. The MetaForge-
Planner mvp-roadmap's own "DFM PASS" gate criterion names no algorithm, and
the Jira gap list's CNC/3D-print/sheet-metal breakdown is a refinement with
no existing geometric primitives to build most of it on:

- CNC tool-access checking needs tool-geometry/reachability simulation --
  no existing primitives at all in this codebase.
- Minimum-wall-thickness measurement (any process) needs a genuinely new
  geometric algorithm (offset/ray-cast analysis); ``wall_thickness``
  currently exists only as a shell-operation *generation* parameter, never
  a measurement of arbitrary existing geometry.
- Sheet-metal DFM needs a flat-pattern geometry model this codebase
  doesn't have.

3D-print overhang detection was chosen because it is the one check
buildable today on real, already-computed geometry:
``tool_registry/tools/freecad/operations.py``'s ``_parse_inp_face_table``
(FORGE-239/277) already computes a real per-face area-weighted average
normal from a gmsh-generated mesh -- built for FEA boundary-condition
selection, directly reusable here for face-tilt-from-vertical.

**Known limitation, by design of the underlying primitive (not fixed
here)**: ``_triangle_area_and_normal``'s own docstring states the mesh
normal is "a" normal, not guaranteed to point outward from the solid --
determining true outward-ness needs the part's interior (BREP topology),
which isn't available from the flattened .inp mesh alone. A directed
"downward-facing normal = overhang, upward-facing = safe top surface"
check would therefore be confidently wrong on however many faces have an
inward-pointing normal. Rather than ship that, this module measures the
UNDIRECTED tilt of each face's plane from the vertical build axis (0
degrees = a vertical wall, 90 degrees = a perfectly horizontal face) and
flags any face tilted past the standard ~45-degree FDM self-supporting
threshold -- deliberately conservative: it also flags harmless flat top
surfaces alongside real unsupported overhangs, since the two are
geometrically indistinguishable from this data alone. Resolving true
outward-ness (e.g. from the CAD model's BREP topology rather than the
mesh) to split "overhang" from "flat top" is a real, separate follow-up
this module does not attempt.

Advisory only: this does NOT wire into ``twin.attempt_promotion``'s
blocking gate logic (``api_gateway/requirement_intelligence/promotion.py``)
or ``twin_core.consistency.gates``'s G3-G8 functions -- same explicit
non-goal FORGE-297 established for thermal evidence. The Evidence this
records is graph-checkable and reachable by the requirements
matrix/coverage machinery FORGE-297 shipped, but nothing here forces a
promotion to consult it.
"""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

#: Build direction (unit vector) the overhang check measures tilt against
#: when the caller doesn't supply one. Z-up, matching this codebase's other
#: geometry conventions (CAD/mesh coordinates).
DEFAULT_BUILD_AXIS: tuple[float, float, float] = (0.0, 0.0, 1.0)

#: The standard FDM "self-supporting" overhang convention: a face tilted up
#: to 45 degrees from a vertical wall orientation prints without support; a
#: face tilted further needs it. Measured here on the UNDIRECTED face
#: plane (see module docstring) -- a face at exactly 45.0 degrees is NOT
#: flagged (the threshold is the maximum still-printable tilt).
OVERHANG_TILT_THRESHOLD_DEG = 45.0


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    mag = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def compute_face_tilt_from_vertical_deg(
    normal: tuple[float, float, float],
    build_axis: tuple[float, float, float] = DEFAULT_BUILD_AXIS,
) -> float:
    """Undirected tilt (0-90 degrees) of a face's plane from the vertical
    build axis. 0 = a perfectly vertical wall (normal perpendicular to the
    build axis). 90 = a perfectly horizontal face (normal parallel to the
    build axis, either sense).

    Uses ``abs()`` of the normal/axis dot product deliberately: the mesh
    normal's sense (which way it points) isn't guaranteed (see module
    docstring), so this treats the normal as an undirected line. A
    zero-magnitude normal (degenerate/unresolved face) returns 0.0 --
    treated as a safe vertical wall rather than flagged, since there's no
    real geometric signal to flag on.
    """
    n = _normalize(normal)
    if n == (0.0, 0.0, 0.0):
        return 0.0
    b = _normalize(build_axis)
    dot = abs(n[0] * b[0] + n[1] * b[1] + n[2] * b[2])
    dot_clamped = max(-1.0, min(1.0, dot))
    angle_from_axis_deg = math.degrees(math.acos(dot_clamped))
    return 90.0 - angle_from_axis_deg


def compute_overhang_faces(
    faces: list[dict[str, Any]],
    *,
    build_axis: tuple[float, float, float] = DEFAULT_BUILD_AXIS,
    threshold_deg: float = OVERHANG_TILT_THRESHOLD_DEG,
) -> list[dict[str, Any]]:
    """Per-face overhang check over a ``freecad.list_named_faces`` face
    table. Faces with no resolvable normal (empty/zero-magnitude, e.g. a
    degenerate group) are skipped -- not flagged, not silently counted as
    passing; they simply don't appear in the result."""
    out: list[dict[str, Any]] = []
    for face in faces:
        normal = face.get("normal")
        if not isinstance(normal, (list, tuple)) or len(normal) != 3:
            continue
        normal_t = (float(normal[0]), float(normal[1]), float(normal[2]))
        if _normalize(normal_t) == (0.0, 0.0, 0.0):
            continue
        tilt = compute_face_tilt_from_vertical_deg(normal_t, build_axis)
        out.append(
            {
                "name": face.get("name"),
                "area_mm2": face.get("area_mm2"),
                "normal": [round(v, 6) for v in normal_t],
                "tilt_from_vertical_deg": round(tilt, 3),
                "flagged": tilt > threshold_deg,
            }
        )
    return out


def make_overhang_evidence_recorder(
    twin: Any,
    *,
    evidence_recorder: Any,
    mcp_bridge: Any,
) -> Any:
    """Return an async ``evaluate_overhang(...)`` bound to a twin +
    evidence recorder + mcp_bridge (a real ``freecad.list_named_faces``
    call)."""

    async def evaluate_overhang(
        *,
        work_product_id: str,
        project_id: str | None = None,
        mesh_file: str,
        build_axis: list[float] | None = None,
        threshold_deg: float | None = None,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        wp_id = UUID(work_product_id)
        wp = await twin.get_work_product(wp_id)
        if wp is None:
            raise ValueError(f"twin.evaluate_overhang_metric: no work_product {work_product_id!r}")

        axis: tuple[float, float, float] = (
            (float(build_axis[0]), float(build_axis[1]), float(build_axis[2]))
            if build_axis is not None
            else DEFAULT_BUILD_AXIS
        )
        threshold = threshold_deg if threshold_deg is not None else OVERHANG_TILT_THRESHOLD_DEG

        face_args = {"mesh_file": mesh_file}
        face_result = await mcp_bridge.invoke("freecad.list_named_faces", face_args)
        faces = face_result.get("faces", []) if isinstance(face_result, dict) else []

        checked = compute_overhang_faces(faces, build_axis=axis, threshold_deg=threshold)
        flagged = [f for f in checked if f["flagged"]]

        out: dict[str, Any] = {
            "faces": checked,
            "flagged_count": len(flagged),
            "total_faces": len(checked),
            "threshold_deg": threshold,
            "build_axis": list(axis),
            "dfm_pass": not flagged,
        }

        replay_args = {
            "work_product_id": work_product_id,
            "project_id": project_id,
            "mesh_file": mesh_file,
            "build_axis": list(axis),
            "threshold_deg": threshold,
        }

        statement = (
            f"3D-print overhang check: {len(flagged)}/{len(checked)} faces flagged "
            f"(tilt > {threshold:g} deg from vertical)"
        )

        ev = await evidence_recorder(
            evidence_type="calculation",
            producer={"tool": "freecad.list_named_faces"},
            inputs=face_args,
            result={"tier": 1, "metric": "overhang_check", **out},
            statement=statement,
            valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
            supersedes=supersedes,
            replay={"tool_id": "twin.evaluate_overhang_metric", "args": replay_args},
            project_id=project_id,
        )
        out["evidence_node_id"] = ev["node_id"]
        return out

    return evaluate_overhang
