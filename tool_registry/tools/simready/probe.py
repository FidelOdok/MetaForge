"""Reading what a SimReady stage says about the part, and a GLB to show it (FORGE-711).

Needs the 'omniverse-usd' extra (usd-core, trimesh). Everything returned is in
SI units whatever the stage was authored in: metres from ``metersPerUnit``,
kilograms from ``kilogramsPerUnit``. The GLB preview is the exception: it is
in millimetres, the unit the twin viewer draws every other model in.

SimReady assets compose their geometry as instanceable prims, so every
traversal here includes instance proxies; a plain ``stage.Traverse()`` sees
an asset with no meshes at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tool_registry.tools.omniverse_usd.converter import UsdConversionError


def _pxr() -> Any:
    try:
        import pxr  # noqa: F401
        from pxr import Gf, Usd, UsdGeom, UsdPhysics
    except ImportError as exc:
        raise UsdConversionError(
            "usd-core (pxr) is not installed -- install the 'omniverse-usd' extra"
        ) from exc
    return Gf, Usd, UsdGeom, UsdPhysics


def _plain(value: Any) -> Any:
    """USD metadata (VtDictionary, Gf vectors, arrays) as JSON-able Python."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    try:
        return [_plain(v) for v in value]
    except TypeError:
        return str(value)


def _latest_validation(meta: dict[str, Any]) -> dict[str, Any]:
    validation = meta.get("validation")
    if not isinstance(validation, dict):
        return {}
    runs = validation.get("validated_features")
    out: dict[str, Any] = {
        "profile": validation.get("profile"),
        "profile_version": validation.get("profile_version"),
    }
    if isinstance(runs, dict) and runs:
        date = max(runs)
        features = runs[date] if isinstance(runs[date], dict) else {}
        out["validated_on"] = date
        out["passed"] = sorted(k for k, v in features.items() if v.get("passed") is True)
        out["failed"] = sorted(k for k, v in features.items() if v.get("passed") is False)
    return out


def probe_stage(usd_path: str | Path) -> dict[str, Any]:
    """Units, envelope, mass, joints, contact materials and labels of a stage."""
    Gf, Usd, UsdGeom, UsdPhysics = _pxr()
    path = Path(usd_path)
    if not path.is_file():
        raise FileNotFoundError(f"USD file not found: {usd_path}")
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise UsdConversionError(f"could not open {usd_path} as a USD stage")
    default = stage.GetDefaultPrim()
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    kgpu = float(UsdPhysics.GetStageKilogramsPerUnit(stage) or 1.0)
    prims = list(stage.Traverse(Usd.TraverseInstanceProxies()))

    extents_m: list[float] = []
    if default:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        box = cache.ComputeWorldBound(default).ComputeAlignedRange()
        if not box.IsEmpty():
            extents_m = [round(float(v) * mpu, 6) for v in box.GetSize()]

    # A body and the mesh under it can both carry the same MassAPI mass, so
    # only the topmost prim with a mass counts; summing every one doubles it.
    massed = {}
    for prim in prims:
        if prim.HasAPI(UsdPhysics.MassAPI):
            mass = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
            if mass:
                massed[prim.GetPath()] = float(mass) * kgpu
    bodies = [
        {"path": path.pathString, "mass_kg": mass}
        for path, mass in massed.items()
        if not any(a in massed for a in path.GetAncestorsRange() if a != path)
    ]
    joints = [
        {"path": p.GetPath().pathString, "type": p.GetTypeName()}
        for p in prims
        if p.IsA(UsdPhysics.Joint)
    ]
    contact = []
    for prim in prims:
        if not prim.HasAPI(UsdPhysics.MaterialAPI):
            continue
        api = UsdPhysics.MaterialAPI(prim)
        contact.append(
            {
                "path": prim.GetPath().pathString,
                "static_friction": api.GetStaticFrictionAttr().Get(),
                "dynamic_friction": api.GetDynamicFrictionAttr().Get(),
                "restitution": api.GetRestitutionAttr().Get(),
            }
        )
    labels: dict[str, list[str]] = {}
    for prim in prims:
        for attr in prim.GetAttributes():
            name = attr.GetName()
            if name.startswith("semantics:labels:"):
                value = attr.Get()
                if value:
                    labels.setdefault(name.split(":", 2)[2], [])
                    labels[name.split(":", 2)[2]] += [str(v) for v in value]
    labels = {k: sorted(set(v)) for k, v in labels.items()}
    meshes = sum(1 for p in prims if p.IsA(UsdGeom.Mesh))

    layer_meta = _plain(dict(stage.GetRootLayer().customLayerData or {}))
    simready = layer_meta.get("SimReady_Metadata") or {}
    variants = {}
    if default:
        for name in default.GetVariantSets().GetNames():
            vset = default.GetVariantSet(name)
            variants[name] = {
                "options": list(vset.GetVariantNames()),
                "selected": vset.GetVariantSelection(),
            }

    body_mass = sum(b["mass_kg"] for b in bodies)
    declared_mass = simready.get("mass")
    mass_kg = body_mass or (float(declared_mass) * kgpu if declared_mass else None)
    return {
        "meters_per_unit": mpu,
        "kilograms_per_unit": kgpu,
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "default_prim": default.GetPath().pathString if default else None,
        "mesh_count": meshes,
        "extents_m": extents_m,
        "mass_kg": round(mass_kg, 6) if mass_kg else None,
        "mass_source": "rigid bodies" if body_mass else ("layer metadata" if mass_kg else None),
        "rigid_bodies": bodies,
        "joints": joints,
        "contact_materials": contact,
        "semantic_labels": labels,
        "physics_variants": variants,
        "asset": {
            k: simready.get(k)
            for k in ("asset_name", "asset_type", "category", "author", "uuid", "qcode")
            if simready.get(k) is not None
        },
        "asset_license": simready.get("asset_license"),
        "conformance": _latest_validation(simready),
    }


def _triangles(counts: list[int], indices: list[int]) -> list[list[int]]:
    """Fan-triangulate USD polygons (faceVertexCounts / faceVertexIndices)."""
    faces: list[list[int]] = []
    at = 0
    for n in counts:
        poly = indices[at : at + n]
        at += n
        faces += [[poly[0], poly[i], poly[i + 1]] for i in range(1, n - 1)]
    return faces


#: Grey for a face with no material colour and no display colour.
_NEUTRAL = (0.7, 0.7, 0.7)

#: The preview is drawn beside CAD the OCCT converter writes unscaled from
#: STEP, which is millimetres; a GLB in metres shows up a thousand times small.
_PREVIEW_MM_PER_M = 1000.0


def _material_rgb(prim: Any) -> tuple[float, float, float] | None:
    """The diffuse colour of the UsdPreviewSurface bound to ``prim``, if it has one."""
    from pxr import UsdShade

    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if not material:
        return None
    shader = material.ComputeSurfaceSource()[0]
    diffuse = shader.GetInput("diffuseColor") if shader else None
    if not diffuse:
        return None
    for attr in diffuse.GetValueProducingAttributes():
        value = attr.Get()
        if value is not None and len(value) == 3:
            return (float(value[0]), float(value[1]), float(value[2]))
    return None  # a texture drives it


def _face_colours(mesh: Any, prim: Any, counts: list[int]) -> list[tuple[float, float, float]]:
    """One colour per triangle: subset material, else mesh material, else display colour.

    SimReady parts colour themselves through materials, often one per geometry
    subset, and leave ``displayColor`` black; reading only that drew every
    part black.
    """
    from pxr import UsdGeom

    display = mesh.GetDisplayColorAttr().Get()
    base = _material_rgb(prim) or (tuple(float(c) for c in display[0]) if display else _NEUTRAL)
    per_face = [base] * len(counts)
    for subset in UsdGeom.Subset.GetAllGeomSubsets(mesh):
        rgb = _material_rgb(subset.GetPrim())
        if rgb is None:
            continue
        for face in subset.GetIndicesAttr().Get() or []:
            if 0 <= face < len(per_face):
                per_face[face] = rgb
    # _triangles fans each n-gon into n - 2 triangles, in face order.
    return [rgb for rgb, n in zip(per_face, counts, strict=True) for _ in range(max(n - 2, 0))]


def stage_to_glb(usd_path: str | Path, glb_path: str | Path) -> dict[str, Any]:
    """Write a GLB of every visible mesh, in millimetres and Y up.

    A preview, not a conversion: each face keeps its material's diffuse colour
    (or the display colour); textures, shading and physics are not carried.
    """
    Gf, Usd, UsdGeom, _ = _pxr()
    try:
        import numpy as np
        import trimesh
    except ImportError as exc:
        raise UsdConversionError(
            "trimesh is not installed -- install the 'omniverse-usd' extra"
        ) from exc
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise UsdConversionError(f"could not open {usd_path} as a USD stage")
    mpu = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    z_up = str(UsdGeom.GetStageUpAxis(stage)) == "Z"
    # glTF is Y up: rotate -90 degrees about X when the stage is Z up.
    scale = mpu * _PREVIEW_MM_PER_M
    to_gltf = np.diag([scale, scale, scale, 1.0])
    if z_up:
        to_gltf = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]]) @ to_gltf

    scene = trimesh.Scene()
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    meshes = 0
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        imageable = UsdGeom.Imageable(prim)
        if imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        if imageable.ComputePurpose() in (UsdGeom.Tokens.guide, UsdGeom.Tokens.proxy):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            continue
        faces = _triangles(list(counts), list(indices))
        if not faces:
            continue
        # USD matrices are row-vector (translation in the last row); transpose.
        world = np.array(cache.GetLocalToWorldTransform(prim), dtype=float).T
        tri = trimesh.Trimesh(
            vertices=np.array(points, dtype=float), faces=np.array(faces), process=False
        )
        colours = [
            [*(int(max(0.0, min(1.0, c)) * 255) for c in rgb), 255]
            for rgb in _face_colours(mesh, prim, list(counts))
        ]
        if len({tuple(c) for c in colours}) > 1:
            # glTF colours vertices, so a corner two colours share would blend.
            tri.unmerge_vertices()
        tri.visual.face_colors = colours
        name = prim.GetPath().pathString.strip("/").replace("/", "_") or f"mesh_{meshes}"
        scene.add_geometry(tri, node_name=name, geom_name=name, transform=to_gltf @ world)
        meshes += 1
    if meshes == 0:
        raise UsdConversionError(f"{usd_path} has no visible mesh to preview")
    out = Path(glb_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(scene.export(file_type="glb"))
    return {"glb_path": str(out), "mesh_count": meshes, "bytes": out.stat().st_size}
