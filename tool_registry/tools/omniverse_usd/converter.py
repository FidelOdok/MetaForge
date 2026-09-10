"""GLB -> OpenUSD conversion (MET-634).

MetaForge's CAD pipeline already tessellates STEP into GLB (`occt-converter`,
`cadquery.export_geometry`, `freecad.export_geometry`) for the dashboard's
web viewer. This module takes that existing, already-trusted GLB output and
authors an OpenUSD stage from it, rather than re-reading STEP directly --
``pythonocc-core`` (the STEP reader `occt-converter` uses) has no PyPI
wheels and needs conda, while OpenUSD (`usd-core`) and `trimesh` install
cleanly with plain `pip`. STEP-reading responsibility stays with the
existing converters; this adapter's job starts at GLB.

Mirrors ``validate-usd-minimum`` from NVIDIA's ``omniverse-cad-to-simready``
reference pipeline as a cheap viability gate, and stops there deliberately --
the full SimReady checklist (geometry/physics/units validation,
conform-profile auto-repair) is MET-639's scope, not this adapter's.

Two implementation details verified empirically before writing this (see
tests) rather than assumed from docs:

1. ``trimesh``'s GLB round-trip does NOT preserve node names as
   ``scene.geometry`` dict keys -- those come back as anonymous
   ``geometry_0``, ``geometry_1``, etc. The real per-part names live in
   ``scene.graph.nodes_geometry`` / ``scene.graph.get(node)``. Using the
   geometry dict keys directly would silently ship anonymous USD prim names,
   which is exactly the failure mode ``feedback_cad_parts_must_be_named``
   warns about for STEP/GLB.
2. USD's ``Gf.Matrix4d`` is row-vector convention (translation in the last
   *row*); trimesh/numpy transforms are column-vector convention
   (translation in the last *column*). Feeding a numpy transform straight
   into ``Gf.Matrix4d(*m.flatten())`` silently collapses every part to the
   stage origin -- the matrix must be transposed first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_VALID_STAGE_SUFFIXES = (".usda", ".usdc", ".usd")


class UsdConversionError(Exception):
    """Raised when GLB -> USD conversion fails for a reason other than a missing file."""


def _safe_prim_name(name: str) -> str:
    """Sanitize a node name into a legal USD prim name (alnum + underscore)."""
    sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def convert_glb_to_usd(
    glb_path: str,
    output_path: str,
    meters_per_unit: float = 0.001,
    up_axis: str = "Z",
) -> dict[str, Any]:
    """Convert a GLB file into an OpenUSD stage, preserving part names and transforms.

    Args:
        glb_path: Path to the input GLB file (from occt-converter/cadquery/freecad export).
        output_path: Path to write the USD stage to (.usda/.usdc/.usd).
        meters_per_unit: Stage scale to author (default 0.001 = millimeters).
        up_axis: Stage up axis, "Y" or "Z" (default "Z", matching CAD convention).

    Returns:
        Dict with keys: output_path, prim_count, mesh_count, part_names.

    Raises:
        FileNotFoundError: If the GLB file does not exist.
        ValueError: If output_path has an unsupported extension or up_axis is invalid.
        UsdConversionError: If the GLB has no usable geometry, or trimesh/pxr are missing.
    """
    glb_file = Path(glb_path)
    if not glb_file.exists():
        raise FileNotFoundError(f"GLB file not found: {glb_path}")

    output_file = Path(output_path)
    if output_file.suffix not in _VALID_STAGE_SUFFIXES:
        raise ValueError(
            f"output_path must be one of {_VALID_STAGE_SUFFIXES}, got: {output_file.suffix}"
        )

    if up_axis not in ("Y", "Z"):
        raise ValueError(f"up_axis must be 'Y' or 'Z', got: {up_axis!r}")

    try:
        import trimesh
    except ImportError as exc:
        raise UsdConversionError(
            "trimesh is not installed -- install the 'omniverse-usd' extra"
        ) from exc

    try:
        from pxr import Gf, Usd, UsdGeom
    except ImportError as exc:
        raise UsdConversionError(
            "usd-core (pxr) is not installed -- install the 'omniverse-usd' extra"
        ) from exc

    scene = trimesh.load(str(glb_file), file_type="glb")
    if not hasattr(scene, "graph") or not scene.graph.nodes_geometry:
        raise UsdConversionError(f"No geometry found in GLB file: {glb_path}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(output_file))
    stage.SetMetadata("metersPerUnit", meters_per_unit)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z if up_axis == "Z" else UsdGeom.Tokens.y)

    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())

    part_names: list[str] = []
    mesh_count = 0
    used_prim_names: dict[str, int] = {}

    for node_name in scene.graph.nodes_geometry:
        node_transform, geom_name = scene.graph.get(node_name)
        geom = scene.geometry.get(geom_name)
        if geom is None or len(geom.vertices) == 0:
            continue

        prim_name = _safe_prim_name(node_name)
        if prim_name in used_prim_names:
            used_prim_names[prim_name] += 1
            prim_name = f"{prim_name}_{used_prim_names[prim_name]}"
        else:
            used_prim_names[prim_name] = 0

        mesh = UsdGeom.Mesh.Define(stage, f"/Root/{prim_name}")
        mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in geom.vertices])
        mesh.CreateFaceVertexCountsAttr([3] * len(geom.faces))
        mesh.CreateFaceVertexIndicesAttr(geom.faces.flatten().tolist())

        # See module docstring point 2: transpose for USD's row-vector convention.
        xformable = UsdGeom.Xformable(mesh)
        xformable.AddTransformOp().Set(Gf.Matrix4d(*node_transform.T.flatten().tolist()))

        color = _extract_face_color(geom)
        if color is not None:
            mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])

        part_names.append(node_name)
        mesh_count += 1

    if mesh_count == 0:
        raise UsdConversionError(f"No convertible mesh geometry found in GLB file: {glb_path}")

    stage.GetRootLayer().Save()

    return {
        "output_path": str(output_file),
        "prim_count": mesh_count + 1,  # +1 for /Root
        "mesh_count": mesh_count,
        "part_names": part_names,
    }


def _extract_face_color(geom: Any) -> tuple[float, float, float] | None:
    """Best-effort (r, g, b) 0-1 tuple from a trimesh geometry's visual, or None."""
    visual = getattr(geom, "visual", None)
    if visual is None:
        return None
    try:
        face_colors = visual.face_colors
        if face_colors is None or len(face_colors) == 0:
            return None
        rgb = face_colors[0][:3] / 255.0
        return (float(rgb[0]), float(rgb[1]), float(rgb[2]))
    except Exception:  # noqa: BLE001 -- colour is optional, never fatal
        return None


def validate_usd_minimum(usd_path: str) -> dict[str, Any]:
    """Cheap structural viability gate on a USD stage.

    Mirrors the ``validate-usd-minimum`` stage from NVIDIA's reference
    pipeline: does the stage open, does it have a default prim, does it
    have at least one mesh, is metersPerUnit set. This is NOT the full
    SimReady checklist (geometry/physics/units validation, conform-profile
    auto-repair) -- that's MET-639's scope.

    Returns:
        Dict with keys: valid, mesh_count, has_default_prim, meters_per_unit, issues.

    Raises:
        FileNotFoundError: If the USD file does not exist.
    """
    usd_file = Path(usd_path)
    if not usd_file.exists():
        raise FileNotFoundError(f"USD file not found: {usd_path}")

    try:
        from pxr import Usd, UsdGeom
    except ImportError as exc:
        raise UsdConversionError(
            "usd-core (pxr) is not installed -- install the 'omniverse-usd' extra"
        ) from exc

    stage = Usd.Stage.Open(str(usd_file))
    issues: list[str] = []

    has_default_prim = stage.HasDefaultPrim()
    if not has_default_prim:
        issues.append("Stage has no default prim")

    meters_per_unit = stage.GetMetadata("metersPerUnit")
    if meters_per_unit is None:
        issues.append("Stage metadata missing metersPerUnit")

    mesh_count = sum(1 for prim in stage.Traverse() if UsdGeom.Mesh(prim))
    if mesh_count == 0:
        issues.append("Stage has no mesh geometry")

    return {
        "valid": len(issues) == 0,
        "mesh_count": mesh_count,
        "has_default_prim": has_default_prim,
        "meters_per_unit": meters_per_unit,
        "issues": issues,
    }


def describe_stage(usd_path: str) -> dict[str, Any]:
    """Return basic structural info about a USD stage.

    Returns:
        Dict with keys: up_axis, meters_per_unit, prim_paths, mesh_count.

    Raises:
        FileNotFoundError: If the USD file does not exist.
    """
    usd_file = Path(usd_path)
    if not usd_file.exists():
        raise FileNotFoundError(f"USD file not found: {usd_path}")

    try:
        from pxr import Usd, UsdGeom
    except ImportError as exc:
        raise UsdConversionError(
            "usd-core (pxr) is not installed -- install the 'omniverse-usd' extra"
        ) from exc

    stage = Usd.Stage.Open(str(usd_file))
    prim_paths = [prim.GetPath().pathString for prim in stage.Traverse()]
    mesh_count = sum(1 for prim in stage.Traverse() if UsdGeom.Mesh(prim))

    return {
        "up_axis": UsdGeom.GetStageUpAxis(stage),
        "meters_per_unit": stage.GetMetadata("metersPerUnit"),
        "prim_paths": prim_paths,
        "mesh_count": mesh_count,
    }
