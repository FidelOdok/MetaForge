"""STEP/IGES → GLB converter using OpenCascade (pythonocc-core).

Reads a CAD file, tessellates each shape, and exports a GLB binary
plus a metadata JSON describing the part tree.

Usage:
    python convert.py input.step --quality standard --output-dir /out
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger("occt-converter")

# Quality tiers: name → deflection parameter for BRepMesh_IncrementalMesh
QUALITY_TIERS = {
    "preview": 0.5,
    "standard": 0.1,
    "fine": 0.01,
}

# An achromatic colour (r≈g≈b) carries no per-part distinction and is almost
# always an exporter default (FreeCAD writes a uniform 0.5 gray on every object)
# rather than an intentional colour — so treat it as "no colour" and fall
# through to the generated palette. Only chromatic source colours are honoured.
_CHROMA_EPS = 0.04


def _color_rgb(c) -> tuple[float, float, float] | None:
    """Extract a chromatic (r,g,b) 0-1 tuple from an OCC colour, else None."""
    if c is None:
        return None
    try:
        r, g, b = c.Red(), c.Green(), c.Blue()
    except Exception:  # noqa: BLE001 — not a colour object
        return None
    if max(abs(r - g), abs(g - b), abs(r - b)) < _CHROMA_EPS:
        return None  # gray/white/black → use the palette instead
    return (float(r), float(g), float(b))


def _palette_color(name: str) -> tuple[float, float, float]:
    """Deterministic, visually distinct colour for a part name.

    Hashes the name to a hue and converts at fixed saturation/value, so the same
    part always gets the same pleasant mid-tone colour and sibling parts spread
    across the wheel — distinct enough to tell parts apart in the viewer.
    """
    h = int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16)
    hue = (h % 360) / 360.0
    return colorsys.hsv_to_rgb(hue, 0.55, 0.85)


def _read_step(path: str):
    """Read a STEP file and return the root shape."""
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file: {path} (status={status})")
    reader.TransferRoots()
    return reader.OneShape()


def _read_iges(path: str):
    """Read an IGES file and return the root shape."""
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.IGESControl import IGESControl_Reader

    reader = IGESControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read IGES file: {path} (status={status})")
    reader.TransferRoots()
    return reader.OneShape()


def _read_cad_file(path: str):
    """Read STEP or IGES based on file extension."""
    ext = Path(path).suffix.lower()
    if ext in (".step", ".stp"):
        return _read_step(path)
    elif ext in (".iges", ".igs"):
        return _read_iges(path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _read_step_named(path: str) -> list[tuple]:
    """Read a STEP file via the XDE layer, returning [(shape, product_name), ...].

    STEP assemblies carry a product name per component (e.g. "fuselage",
    "front_left_motor"). The plain STEPControl_Reader discards these, so parts
    end up anonymous (Part_1..N). This uses pythonocc's XDE helper to recover
    them. Returns [] when no names are available (older STEP, IGES) so the
    caller can fall back to anonymous solid enumeration.
    """
    import contextlib
    import io

    try:
        from OCC.Extend.DataExchange import read_step_file_with_names_colors
    except Exception as exc:  # noqa: BLE001 — helper unavailable in this build
        logger.warning("XDE named-read unavailable (%s); using anonymous parts", exc)
        return []

    try:
        # The helper prints progress to stdout; suppress so it can't corrupt
        # the JSON written to stdout by the CLI path.
        with contextlib.redirect_stdout(io.StringIO()):
            shape_dict = read_step_file_with_names_colors(path)
    except Exception as exc:  # noqa: BLE001 — fall back to anonymous solids
        logger.warning("XDE named read failed (%s); using anonymous parts", exc)
        return []

    named: list[tuple] = []
    for shape, label in shape_dict.items():
        if shape is None or shape.IsNull():
            continue
        if isinstance(label, (tuple, list)):
            name = label[0]
            color = _color_rgb(label[1]) if len(label) > 1 else None
        else:
            name, color = label, None
        name = str(name).strip() if name else ""
        if not name:
            continue
        named.append((shape, name, color))
    return named


def _get_sub_shapes(shape):
    """Extract child solids/shells from a compound shape."""
    from OCC.Core.TopAbs import TopAbs_SOLID
    from OCC.Core.TopExp import TopExp_Explorer

    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    return solids


def _tessellate(shape, deflection: float):
    """Tessellate a shape and return vertices + faces as numpy arrays."""
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopLoc import TopLoc_Location

    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)

    all_vertices = []
    all_faces = []
    vertex_offset = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        loc = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, loc)

        if triangulation is not None:
            nb_nodes = triangulation.NbNodes()
            nb_tris = triangulation.NbTriangles()

            for i in range(1, nb_nodes + 1):
                pnt = triangulation.Node(i)
                if not loc.IsIdentity():
                    pnt = pnt.Transformed(loc.Transformation())
                all_vertices.append([pnt.X(), pnt.Y(), pnt.Z()])

            for i in range(1, nb_tris + 1):
                tri = triangulation.Triangle(i)
                n1, n2, n3 = tri.Get()
                all_faces.append(
                    [
                        n1 - 1 + vertex_offset,
                        n2 - 1 + vertex_offset,
                        n3 - 1 + vertex_offset,
                    ]
                )

            vertex_offset += nb_nodes

        explorer.Next()

    if not all_vertices:
        return None, None
    return np.array(all_vertices, dtype=np.float32), np.array(all_faces, dtype=np.int32)


def _bounding_box(vertices: np.ndarray) -> dict:
    """Compute axis-aligned bounding box from vertices."""
    bb_min = vertices.min(axis=0).tolist()
    bb_max = vertices.max(axis=0).tolist()
    return {"min": bb_min, "max": bb_max}


def _source_format(path: str) -> str:
    """Determine the source CAD format from file extension."""
    ext = Path(path).suffix.lower()
    if ext in (".step", ".stp"):
        return "STEP-AP242"
    elif ext in (".iges", ".igs"):
        return "IGES"
    return "UNKNOWN"


def convert(input_path: str, quality: str, output_dir: str) -> dict:
    """Convert a STEP/IGES file to GLB + metadata JSON.

    Returns the metadata dict.
    """
    import trimesh

    deflection = QUALITY_TIERS.get(quality, QUALITY_TIERS["standard"])
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    logger.info("Reading CAD file: %s (quality=%s, deflection=%s)", input_path, quality, deflection)

    # Prefer named parts from the STEP product structure; fall back to
    # anonymous solid enumeration (IGES, unnamed STEP, or XDE failure).
    is_step = Path(input_path).suffix.lower() in (".step", ".stp")
    named = _read_step_named(input_path) if is_step else []
    if named:
        enumerated = named
        logger.info("Recovered %d named parts from STEP product structure", len(named))
    else:
        root_shape = _read_cad_file(input_path)
        sub_shapes = _get_sub_shapes(root_shape) or [root_shape]
        enumerated = [(shape, f"Part_{i + 1}", None) for i, shape in enumerate(sub_shapes)]
        logger.info("Using %d anonymous solid(s)", len(enumerated))

    from trimesh.visual.material import PBRMaterial

    scene = trimesh.Scene()
    parts: list[dict] = []
    materials: list[dict] = []
    total_triangles = 0
    used_names: dict[str, int] = {}

    for idx, (shape, part_name, src_color) in enumerate(enumerated):
        vertices, faces = _tessellate(shape, deflection)
        if vertices is None or faces is None:
            logger.warning("Skipping empty shape: %s", part_name)
            continue

        # Keep node names unique for trimesh/glTF even if products repeat.
        node_name = part_name
        if part_name in used_names:
            used_names[part_name] += 1
            node_name = f"{part_name}_{used_names[part_name]}"
        else:
            used_names[part_name] = 0

        # Per-part shading: honour an embedded STEP colour, else a deterministic
        # distinct palette colour by name so parts are visually separable. The
        # R3F viewer renders the GLB's own materials, so this surfaces directly.
        r, g, b = src_color if src_color is not None else _palette_color(part_name)
        rgba = [int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), 255]

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
        mesh.fix_normals()
        mesh.visual = trimesh.visual.TextureVisuals(
            material=PBRMaterial(
                name=node_name,
                baseColorFactor=rgba,
                metallicFactor=0.0,
                roughnessFactor=0.75,
            )
        )
        scene.add_geometry(mesh, node_name=node_name, geom_name=node_name)

        total_triangles += len(faces)
        parts.append(
            {
                "name": part_name,
                "meshName": node_name,
                "children": [],
                "boundingBox": _bounding_box(vertices),
                "color": rgba,
                "colorSource": "step" if src_color is not None else "palette",
            }
        )
        materials.append({"part": node_name, "baseColorFactor": rgba})

    # Export GLB
    glb_path = out / "model.glb"
    scene.export(str(glb_path), file_type="glb")
    file_size = glb_path.stat().st_size

    metadata = {
        "format": "metaforge-twin-export",
        "schemaVersion": "1.0",
        "sourceFormat": _source_format(input_path),
        "convertedAt": datetime.now(UTC).isoformat(),
        "parts": parts,
        "materials": materials,
        "stats": {
            "triangleCount": total_triangles,
            "fileSize": file_size,
        },
    }

    meta_path = out / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    logger.info(
        "Conversion complete: %d parts, %d triangles, %d bytes",
        len(parts),
        total_triangles,
        file_size,
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="STEP/IGES → GLB converter")
    parser.add_argument("input", help="Path to STEP or IGES file")
    parser.add_argument(
        "--quality",
        choices=list(QUALITY_TIERS.keys()),
        default="standard",
        help="Tessellation quality tier (default: standard)",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory to write GLB and metadata (default: ./output)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        metadata = convert(args.input, args.quality, args.output_dir)
        # Print metadata JSON to stdout for programmatic consumption
        print(json.dumps(metadata))
    except Exception as exc:
        logger.error("Conversion failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
