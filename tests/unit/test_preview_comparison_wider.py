"""The wider preview versus exact comparison (spec section 7).

Openings beyond extrusions (revolves with a bore, part turns, ring cavities),
openings of multi-feature parts read from the design IR, and assemblies
compared component by component: pose and alignment against the structured
layout, interference and clearance between components. Whatever cannot be
measured is ``not_evaluated`` with its reason.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.preview_comparison import (
    ASSEMBLY_COMPARISON_KEY,
    MAX_ASSEMBLY_COMPONENTS,
    assembly_comparison_for_glbs,
    assembly_comparison_for_node,
    compare_assembly,
    compare_preview_to_exact,
    comparison_for_source,
    intended_topology,
    ir_intended_topology,
    mesh_topology,
    point_inside,
    read_glb_components,
    structured_layout_parts,
)
from tests.unit.test_parametric_preview import glb_of, plate_mesh
from tool_registry.tools.twin.concept_sketch import parse_layout
from tool_registry.tools.twin.layout_mesh import build_structured_mesh
from twin_core.design_ir.feature_definition import PreviewSolid, frame_of
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

Vec = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Meshes
# ---------------------------------------------------------------------------


def _revolved_point(u: float, v: float, deg: float) -> Vec:
    """The preview's own mapping on the XY frame: v along +Y, the radius turning in XZ."""
    a = math.radians(deg)
    return (u * math.cos(a), v, -u * math.sin(a))


def revolve_mesh(
    loops: list[list[tuple[float, float]]], angle: float, steps: int = 24
) -> list[Any]:
    """A closed triangle mesh of ``loops`` turned by ``angle`` (capped when not whole)."""
    whole = angle >= 360.0
    rings = steps if whole else steps + 1
    tris: list[Any] = []
    for loop in loops:
        n = len(loop)
        pts = [[_revolved_point(u, v, angle * k / steps) for u, v in loop] for k in range(rings)]
        for k in range(steps):
            a, b = pts[k], pts[(k + 1) % rings]
            for i in range(n):
                j = (i + 1) % n
                tris.append((a[i], b[i], b[j]))
                tris.append((a[i], b[j], a[j]))
    if not whole:
        # Caps: the profile region (outer minus its holes) at both ends, by a
        # simple strip between a rectangle and the rectangle hole inside it.
        outer = loops[0]
        holes = loops[1:]
        for deg in (0.0, angle):
            if not holes:
                c = _revolved_point(
                    sum(p[0] for p in outer) / len(outer),
                    sum(p[1] for p in outer) / len(outer),
                    deg,
                )
                for i in range(len(outer)):
                    a = _revolved_point(*outer[i], deg)
                    b = _revolved_point(*outer[(i + 1) % len(outer)], deg)
                    tris.append((c, a, b))
            else:
                hole = holes[0]
                for i in range(4):
                    a = _revolved_point(*outer[i], deg)
                    b = _revolved_point(*outer[(i + 1) % 4], deg)
                    c = _revolved_point(*hole[(i + 1) % 4], deg)
                    d = _revolved_point(*hole[i], deg)
                    tris.append((a, b, c))
                    tris.append((a, c, d))
    return tris


def rect(u0: float, v0: float, u1: float, v1: float) -> list[tuple[float, float]]:
    return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]


def box_mesh(lo: Vec, hi: Vec) -> list[Any]:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris: list[Any] = []
    for a, b, cc, d in faces:
        tris.append((c[a], c[b], c[cc]))
        tris.append((c[a], c[cc], c[d]))
    return tris


def _bbox(tris: list[Any]) -> dict[str, float]:
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "min_z": min(zs),
        "max_x": max(xs),
        "max_y": max(ys),
        "max_z": max(zs),
    }


def _revolve_def(elements: list[dict[str, Any]], angle: float) -> dict[str, Any]:
    return {"op": "revolve", "elements": elements, "frame": frame_of(None), "angle": angle}


def _rect_el(u0: float, v0: float, w: float, h: float) -> dict[str, Any]:
    return {"type": "rectangle", "origin": (u0, v0), "width": w, "height": h}


def _features(record: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in record["checks"] if c["check"] == "features")


# ---------------------------------------------------------------------------
# Revolves
# ---------------------------------------------------------------------------


def test_a_whole_turn_clear_of_the_axis_has_a_bore() -> None:
    d = _revolve_def([_rect_el(10.0, 0.0, 5.0, 20.0)], 360.0)
    topo = intended_topology(PreviewSolid.from_definition(d))
    assert topo["genus"] == 1 and topo["shells"] == 1 and "bore" in topo["basis"]

    ring = revolve_mesh([rect(10.0, 0.0, 15.0, 20.0)], 360.0)
    assert mesh_topology(ring, weld=1e-4)[:2] == (1, 1)
    record = compare_preview_to_exact(
        d, exact_bbox=_bbox(ring), exact_volume=None, exact_triangles=ring
    )
    f = _features(record)
    assert f["outcome"] == "pass", f
    assert f["exact_through_holes"] == 1 and f["exact_shells"] == 1

    # The kernel filled the bore: a solid cylinder is a discrepancy, with values.
    solid = revolve_mesh([rect(0.0, 0.0, 15.0, 20.0)], 360.0)
    f = _features(
        compare_preview_to_exact(
            d, exact_bbox=_bbox(solid), exact_volume=None, exact_triangles=solid
        )
    )
    assert f["outcome"] == "fail" and f["exact_through_holes"] == 0, f


def test_a_part_turn_has_no_bore_and_its_inner_loops_open_at_the_ends() -> None:
    d = _revolve_def([_rect_el(10.0, 0.0, 10.0, 20.0)], 180.0)
    assert intended_topology(PreviewSolid.from_definition(d))["genus"] == 0
    half = revolve_mesh([rect(10.0, 0.0, 20.0, 20.0)], 180.0, steps=12)
    f = _features(
        compare_preview_to_exact(d, exact_bbox=_bbox(half), exact_volume=None, exact_triangles=half)
    )
    assert f["outcome"] == "pass", f

    with_hole = _revolve_def(
        [_rect_el(10.0, 0.0, 10.0, 20.0), _rect_el(13.0, 5.0, 4.0, 10.0)], 180.0
    )
    topo = intended_topology(PreviewSolid.from_definition(with_hole))
    assert topo == {**topo, "genus": 1, "shells": 1}
    tunnel = revolve_mesh([rect(10.0, 0.0, 20.0, 20.0), rect(13.0, 5.0, 17.0, 15.0)], 180.0, 12)
    assert mesh_topology(tunnel, weld=1e-4)[:2] == (1, 1)


def test_a_whole_turn_inner_loop_is_an_enclosed_ring_cavity() -> None:
    d = _revolve_def([_rect_el(10.0, 0.0, 10.0, 20.0), _rect_el(13.0, 5.0, 4.0, 10.0)], 360.0)
    topo = intended_topology(PreviewSolid.from_definition(d))
    assert (topo["genus"], topo["shells"]) == (2, 2)
    body = revolve_mesh([rect(10.0, 0.0, 20.0, 20.0)], 360.0)
    cavity = [(t[0], t[2], t[1]) for t in revolve_mesh([rect(13.0, 5.0, 17.0, 15.0)], 360.0)]
    f = _features(
        compare_preview_to_exact(
            d, exact_bbox=_bbox(body), exact_volume=None, exact_triangles=body + cavity
        )
    )
    assert f["outcome"] == "pass", f
    # Without the cavity the shell count gives it away.
    f = _features(
        compare_preview_to_exact(d, exact_bbox=_bbox(body), exact_volume=None, exact_triangles=body)
    )
    assert f["outcome"] == "fail" and f["intended_shells"] == 2 and f["exact_shells"] == 1


def test_a_profile_across_the_axis_is_not_evaluated_with_its_reason() -> None:
    d = _revolve_def([_rect_el(-5.0, 0.0, 10.0, 20.0)], 360.0)
    f = _features(
        compare_preview_to_exact(
            d,
            exact_bbox={"min_x": 0, "min_y": 0, "min_z": 0, "max_x": 1, "max_y": 1, "max_z": 1},
            exact_volume=None,
            exact_triangles=None,
        )
    )
    assert f["outcome"] == "not_evaluated" and "crosses its axis" in f["detail"]


# ---------------------------------------------------------------------------
# Multi-feature parts
# ---------------------------------------------------------------------------


def _sketch(sid: str, elements: list[dict[str, Any]], offset: float = 0.0) -> dict[str, Any]:
    return {
        "id": sid,
        "op": "sketch",
        "body_ref": "body1",
        "plane": "XY",
        "offset": offset,
        "elements": elements,
    }


def _circle(x: float, y: float, r: float) -> dict[str, Any]:
    return {"type": "circle", "center": (x, y), "radius": r}


def _plate_ir(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": "body1", "op": "create_body"},
        _sketch("s1", [_rect_el(0.0, 0.0, 30.0, 30.0)]),
        {"id": "pad1", "op": "pad", "body_ref": "body1", "sketch_ref": "s1", "depth": 2.0},
        # A through cut from the top face, and a blind one.
        _sketch("s2", [_circle(15.0, 15.0, 3.0)], offset=2.0),
        {"id": "pk1", "op": "pocket", "body_ref": "body1", "sketch_ref": "s2", "depth": 2.0},
        _sketch("s3", [_circle(5.0, 5.0, 2.0)], offset=2.0),
        {"id": "pk2", "op": "pocket", "body_ref": "body1", "sketch_ref": "s3", "depth": 1.0},
        # A cut that crosses the edge is a notch, not a hole.
        _sketch("s4", [_rect_el(25.0, 10.0, 10.0, 4.0)], offset=2.0),
        {"id": "pk3", "op": "pocket", "body_ref": "body1", "sketch_ref": "s4", "depth": 2.0},
        {"id": "f1", "op": "fillet_edges", "body_ref": "body1", "radius": 0.5},
        *extra,
    ]


PLATE_BBOX = {"min_x": 0.0, "min_y": 0.0, "min_z": 0.0, "max_x": 30.0, "max_y": 30.0, "max_z": 2.0}


def test_multi_feature_openings_are_read_from_the_design_ir() -> None:
    topo = ir_intended_topology(_plate_ir())
    assert topo["genus"] == 1, topo
    assert "1 blind" in topo["basis"] and "1 at an edge" in topo["basis"]
    assert topo["shells"] is None  # an edge cut could split the part

    source = {"kind": "design_ir", "design_ir": {"entities": _plate_ir()}}
    holed = glb_of(plate_mesh(3, 3, 10.0, 2.0, {(1, 1)}))
    record = comparison_for_source(source, validity={"bounding_box": PLATE_BBOX}, glb=holed)
    assert record is not None
    assert record["preview"]["op"] == "multi_feature"
    by = {c["check"]: c for c in record["checks"]}
    assert by["features"]["outcome"] == "pass"
    # No L2 preview of a multi-feature part: the shape checks say why.
    for name in ("bounding_box", "dimensions", "volume", "surface_distance"):
        assert by[name]["outcome"] == "not_evaluated" and "no L2 preview" in by[name]["detail"]
    assert record["outcome"] == "incomplete"

    filled = glb_of(plate_mesh(3, 3, 10.0, 2.0, set()))
    record = comparison_for_source(source, validity={"bounding_box": PLATE_BBOX}, glb=filled)
    assert record is not None and record["outcome"] == "discrepancy"
    assert record["discrepancies"][0]["check"] == "features"


def test_unpredictable_features_say_why_and_ir_with_no_feature_tree_is_skipped() -> None:
    loft = {
        "id": "l1",
        "op": "loft",
        "body_ref": "body1",
        "profile_ref": "s1",
        "section_refs": ["s2"],
    }
    topo = ir_intended_topology(_plate_ir(loft))
    assert "loft" in topo["reason"]
    tilted = [
        *_plate_ir()[:3],
        {**_sketch("s9", [_circle(15.0, 15.0, 3.0)]), "plane": "XZ"},
        {"id": "pk9", "op": "pocket", "body_ref": "body1", "sketch_ref": "s9", "depth": 50.0},
    ]
    assert "not parallel" in ir_intended_topology(tilted)["reason"]
    primitives = {
        "kind": "design_ir",
        "design_ir": {"entities": [{"id": "b", "op": "create_primitive", "kind": "box"}]},
    }
    assert comparison_for_source(primitives, validity={}, glb=None) is None


# ---------------------------------------------------------------------------
# Assemblies
# ---------------------------------------------------------------------------

LAYOUT: dict[str, Any] = {
    "concept_name": "Box",
    "parts": [
        {
            "name": "Base",
            "size_mm": [40, 20, 10],
            "position_mm": [0, 0, 0],
            "provenance": "intended",
        },
        {
            "name": "Lid",
            "size_mm": [40, 20, 2],
            "position_mm": [0, 0, 12],
            "provenance": "intended",
        },
    ],
}
IDS = {"Base": "CMP-base", "Lid": "CMP-lid"}


def _layout_glb() -> bytes:
    return build_structured_mesh(parse_layout(LAYOUT), IDS).glb


def _part(cid: str, name: str, lo: Vec, hi: Vec) -> dict[str, Any]:
    return {"key": cid, "component_id": cid, "name": name, "tris": box_mesh(lo, hi)}


def _check(record: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in record["checks"] if c["check"] == name)


def test_the_layout_parts_are_read_from_the_l1_extras() -> None:
    parts = structured_layout_parts(_layout_glb())
    assert [(p["component_id"], p["box"]) for p in parts] == [
        ("CMP-base", [0.0, 0.0, 0.0, 40.0, 20.0, 10.0]),
        ("CMP-lid", [0.0, 0.0, 12.0, 40.0, 20.0, 14.0]),
    ]


def test_an_assembly_as_laid_out_is_consistent_and_reports_clearance() -> None:
    # The CAD sits 100 mm along x from the layout: a whole-assembly offset is not a pose error.
    exact = [
        _part("CMP-base", "Base", (100, 0, 0), (140, 20, 10)),
        _part("CMP-lid", "Lid", (100, 0, 12), (140, 20, 14)),
    ]
    record = compare_assembly(exact, layout_parts=structured_layout_parts(_layout_glb()))
    assert record["outcome"] == "consistent", record["checks"]
    pose = _check(record, "component_pose")
    assert pose["value_mm"] == pytest.approx(0.0, abs=1e-9)
    clash = _check(record, "interference")
    assert clash["min_clearance_mm"] == pytest.approx(2.0)
    assert "reported, not judged" in clash["detail"]


def test_a_moved_turned_or_clashing_part_is_a_discrepancy() -> None:
    layout = structured_layout_parts(_layout_glb())
    moved = [
        _part("CMP-base", "Base", (0, 0, 0), (40, 20, 10)),
        _part("CMP-lid", "Lid", (0, 0, 40), (40, 20, 42)),
    ]
    record = compare_assembly(moved, layout_parts=layout)
    pose = _check(record, "component_pose")
    assert pose["outcome"] == "fail" and pose["value_mm"] > 5

    turned = [
        _part("CMP-base", "Base", (0, 0, 0), (40, 20, 10)),
        _part("CMP-lid", "Lid", (10, -10, 12), (30, 30, 14)),
    ]
    align = _check(compare_assembly(turned, layout_parts=layout), "component_alignment")
    assert align["outcome"] == "fail"
    lid = next(c for c in align["components"] if c["component"] == "CMP-lid")
    assert (lid["long_axis_layout"], lid["long_axis_exact"]) == ("x", "y")

    clashing = [
        _part("CMP-base", "Base", (0, 0, 0), (40, 20, 10)),
        _part("CMP-lid", "Lid", (0, 0, 6), (40, 20, 14)),
    ]
    clash = _check(compare_assembly(clashing, layout_parts=layout), "interference")
    assert clash["outcome"] == "fail"
    pair = clash["pairs"][0]
    assert pair["state"] == "interfering" and pair["depth_mm"] > 0.5

    # Parts that touch face to face are in contact, not interfering.
    touching = [
        _part("CMP-base", "Base", (0, 0, 0), (40, 20, 10)),
        _part("CMP-lid", "Lid", (0, 0, 10), (40, 20, 12)),
    ]
    clash = _check(compare_assembly(touching), "interference")
    assert clash["outcome"] == "pass" and clash["pairs"][0]["state"] == "contact"


def test_what_cannot_be_measured_is_not_evaluated_with_a_reason() -> None:
    exact = [
        _part("CMP-base", "Base", (0, 0, 0), (40, 20, 10)),
        _part("CMP-lid", "Lid", (0, 0, 12), (40, 20, 14)),
    ]
    record = compare_assembly(exact, layout_parts=None)
    pose = _check(record, "component_pose")
    assert pose["outcome"] == "not_evaluated" and "no structured layout" in pose["detail"]
    assert record["outcome"] == "incomplete"

    many = [
        _part(f"CMP-p{i}", f"P{i}", (i * 20.0, 0, 0), (i * 20.0 + 10, 10, 10))
        for i in range(MAX_ASSEMBLY_COMPONENTS + 1)
    ]
    clash = _check(compare_assembly(many), "interference")
    assert clash["outcome"] == "not_evaluated" and str(MAX_ASSEMBLY_COMPONENTS) in clash["detail"]

    # An open mesh is not tested for interference.
    open_lid = _part("CMP-lid", "Lid", (0, 0, 6), (40, 20, 14))
    open_lid["tris"] = open_lid["tris"][:-2]
    clash = _check(compare_assembly([exact[0], open_lid]), "interference")
    assert (
        clash["outcome"] == "not_evaluated"
        and clash["pairs"][0]["detail"] == "a mesh is not closed"
    )

    record = assembly_comparison_for_glbs(None, _layout_glb(), note="the converter is down")
    assert record["outcome"] == "incomplete"
    assert all(c["detail"] == "the converter is down" for c in record["checks"])


def test_point_inside_a_closed_mesh() -> None:
    box = box_mesh((0, 0, 0), (10, 10, 10))
    assert point_inside((5, 5, 5), box)
    assert not point_inside((15, 5, 5), box)


# ---------------------------------------------------------------------------
# The stored record, from the converter's GLB
# ---------------------------------------------------------------------------

PROJECT = UUID("80980980-9809-4809-8809-809809809809")


async def test_the_assembly_comparison_is_stored_on_the_revision() -> None:
    from tests.unit.test_component_ids_in_cad_export import ASSEMBLY
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    layout = WorkProduct(
        name="Structured mesh",
        type=WorkProductType.RENDER,
        domain="mechanical",
        file_path="",
        content_hash="l1",
        format="glb",
        metadata={"generated_by": "twin.render_structured_mesh"},
        created_by="test",
        project_id=PROJECT,
    )
    cad = WorkProduct(
        name="Enclosure",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash=hashlib.sha256(b"cad").hexdigest(),
        format="step",
        metadata={"part_component_ids": {"base": "CMP-base", "lid": "CMP-lid"}},
        created_by="test",
        project_id=PROJECT,
    )
    await twin.create_work_product(layout)
    await twin.create_work_product(cad)
    seen: dict[str, bytes] = {}

    def read_blob(wp: Any) -> bytes | None:
        return _layout_glb() if wp.id == layout.id else ASSEMBLY

    def to_glb(step: bytes, filename: str) -> tuple[bytes | None, str | None]:
        # Stands in for the converter: the stamped names become node extras.
        seen["step"] = step
        from tests.unit.test_component_ids_in_cad_export import _converter_ids

        ids = _converter_ids()
        parts = [
            _part("CMP-base", "base", (0, 0, 0), (40, 20, 10)),
            _part("CMP-lid", "lid", (0, 0, 12), (40, 20, 14)),
        ]
        glb = _two_node_glb(parts)
        glb, _n = ids.add_node_extras(
            glb,
            {
                "base": {"component_id": "CMP-base", "part_name": "base"},
                "lid": {"component_id": "CMP-lid", "part_name": "lid"},
            },
        )
        return glb, None

    record = await assembly_comparison_for_node(twin, cad, read_blob=read_blob, to_glb=to_glb)
    assert b"lid [CMP-lid]" in seen["step"]
    assert record["outcome"] == "consistent", record["checks"]
    assert {c["component_id"] for c in record["components"]} == {"CMP-base", "CMP-lid"}
    stored = (await twin.get_work_product(cad.id)).metadata[ASSEMBLY_COMPARISON_KEY]
    assert stored["outcome"] == "consistent"
    # The same content is answered from the record.
    seen.clear()
    again = await assembly_comparison_for_node(
        twin, await twin.get_work_product(cad.id), read_blob=read_blob, to_glb=to_glb
    )
    assert again["compared_at"] == record["compared_at"] and not seen


async def test_the_route_compares_cad_and_refuses_anything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from fastapi import FastAPI

    from api_gateway.twin import routes as twin_routes
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    monkeypatch.setattr(twin_routes, "_twin", twin)
    monkeypatch.setattr(twin_routes, "_read_wp_blob", lambda wp: None)
    cad = WorkProduct(
        name="Bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="c1",
        format="step",
        metadata={},
        created_by="test",
        project_id=PROJECT,
    )
    doc = WorkProduct(
        name="Notes",
        type=WorkProductType.DOCUMENTATION,
        domain="mechanical",
        file_path="",
        content_hash="d1",
        format="md",
        metadata={},
        created_by="test",
        project_id=PROJECT,
    )
    await twin.create_work_product(cad)
    await twin.create_work_product(doc)
    app = FastAPI()
    app.include_router(twin_routes.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get(f"/v1/twin/nodes/{cad.id}/assembly-comparison")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outcome"] == "incomplete"
        assert body["checks"][0]["detail"] == "the revision has no stored STEP to tessellate"
        r = await client.get(f"/v1/twin/nodes/{doc.id}/assembly-comparison")
        assert r.status_code == 400
        r = await client.get("/v1/twin/nodes/not-a-uuid/assembly-comparison")
        assert r.status_code == 400


def _two_node_glb(parts: list[dict[str, Any]]) -> bytes:
    """A GLB with one node and mesh per part, named by part name."""
    import json
    import struct

    binary = b""
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        verts = [p for t in part["tris"] for p in t]
        pos = b"".join(struct.pack("<3f", *v) for v in verts)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(pos)})
        accessors.append(
            {"bufferView": i, "componentType": 5126, "count": len(verts), "type": "VEC3"}
        )
        binary += pos
        meshes.append({"primitives": [{"attributes": {"POSITION": i}}]})
        nodes.append({"name": part["name"], "mesh": i})
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = (
        struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def test_components_group_by_their_id_extras() -> None:
    parts = [
        _part("CMP-base", "base", (0, 0, 0), (40, 20, 10)),
        _part("CMP-lid", "lid", (0, 0, 12), (40, 20, 14)),
    ]
    groups = read_glb_components(_two_node_glb(parts))
    assert [g["component_id"] for g in groups] == [None, None]
    assert [g["name"] for g in groups] == ["base", "lid"]
