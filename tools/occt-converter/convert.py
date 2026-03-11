"""STEP/IGES → GLB converter using OpenCascade (pythonocc-core).

Reads a CAD file, tessellates each shape, and exports a GLB binary
plus a metadata JSON describing the part tree.

Usage:
    python convert.py input.step --quality standard --output-dir /out
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

logger = logging.getLogger("occt-converter")

# Quality tiers: name → deflection parameter for BRepMesh_IncrementalMesh
QUALITY_TIERS = {
    "preview": 0.5,
    "standard": 0.1,
    "fine": 0.01,
}


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


def convert(input_path: str, quality: str, output_dir: str) -> dict:
    """Convert a STEP/IGES file to GLB + metadata JSON.

    Returns the metadata dict.
    """
    import trimesh

    deflection = QUALITY_TIERS.get(quality, QUALITY_TIERS["standard"])
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    logger.info("Reading CAD file: %s (quality=%s, deflection=%s)", input_path, quality, deflection)
    root_shape = _read_cad_file(input_path)

    sub_shapes = _get_sub_shapes(root_shape)
    if not sub_shapes:
        sub_shapes = [root_shape]

    scene = trimesh.Scene()
    parts: list[dict] = []
    total_triangles = 0

    for idx, shape in enumerate(sub_shapes):
        part_name = f"Part_{idx + 1}"
        mesh_name = f"mesh_{idx}"

        vertices, faces = _tessellate(shape, deflection)
        if vertices is None or faces is None:
            logger.warning("Skipping empty shape: %s", part_name)
            continue

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
        mesh.fix_normals()
        scene.add_geometry(mesh, node_name=mesh_name, geom_name=mesh_name)

        total_triangles += len(faces)
        parts.append(
            {
                "name": part_name,
                "meshName": mesh_name,
                "children": [],
                "boundingBox": _bounding_box(vertices),
            }
        )

    # Export GLB
    glb_path = out / "model.glb"
    scene.export(str(glb_path), file_type="glb")
    file_size = glb_path.stat().st_size

    metadata = {
        "parts": parts,
        "materials": [],
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
