"""Face-attached sketch planes, signatures, remapping and blocking (CDE slice 9).

Covers FRS PLN-009 (semantic tags and geometry signatures, never a face index
alone; ambiguous and broken attachments detected), PLN-010
(``REATTACHMENT_REQUIRED`` keeps the last valid state and blocks dependent
authoritative commits), PLN-011 (exact revisions built on the plane, and
their STEP, go stale through the record pins) and RC-05 (a topology change
re-attaches, asks, or blocks; it never attaches to the wrong face
silently), as ``docs/twin_schema.md`` states them.

The CadQuery adapter is stubbed (CI has no CAD kernel); ``TestRealKernel``
runs ``cadquery.describe_faces`` for real when ``cadquery`` is importable.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.operations import publisher as ops
from api_gateway.twin import face_attachment as fa
from api_gateway.twin import routes as twin_routes
from api_gateway.twin import sketch_planes as sp
from api_gateway.twin.exact_cad import (
    EXACT_BUILD_TOOL,
    STEP_EXPORT_TOOL,
    PlaneReattachmentRequiredError,
    set_exact_cad_service,
)
from api_gateway.twin.sketch_strokes_recorder import SketchSaveRequest, make_sketch_strokes_recorder
from tests.unit.test_exact_cad import PROJECT, FakeCad, Harness, _definition
from tool_registry import jobs as jobs_module
from tool_registry.jobs import set_job_head_resolver
from twin_core.consistency.record_pins import unregister_head_moved_listener
from twin_core.geometry.face_signature import (
    PlanarFace,
    RemapOutcome,
    expected_faces,
    frame_from_face,
    part_size_of,
    remap,
    score_candidate,
    signature_of,
    tag_faces,
)
from twin_core.geometry_state import GeometryState, geometry_state_of, step_export_view
from twin_core.items import find_item, item_history
from twin_core.models.enums import WorkProductType
from twin_core.models.sketch_plane import (
    PlaneKind,
    PlaneState,
    PlaneUnit,
    SketchPlane,
    cross,
    dot,
)

# ── synthetic faces ───────────────────────────────────────────────────────

_AXES = {
    "-x": (-1.0, 0.0, 0.0),
    "+x": (1.0, 0.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-z": (0.0, 0.0, -1.0),
    "+z": (0.0, 0.0, 1.0),
}


def _face(
    face_id: str,
    normal: tuple[float, float, float],
    centroid: tuple[float, float, float],
    area: float,
    *,
    half: tuple[float, float, float],
    edge: tuple[float, float, float],
    adjacent: list[tuple[float, float, float]],
    tri: int = 0,
) -> dict[str, Any]:
    lo = [c - h for c, h in zip(centroid, half, strict=True)]
    hi = [c + h for c, h in zip(centroid, half, strict=True)]
    return {
        "face_id": face_id,
        "normal": list(normal),
        "offset": dot(normal, centroid),
        "area": area,
        "centroid": list(centroid),
        "bounding_box": {
            "min_x": lo[0],
            "min_y": lo[1],
            "min_z": lo[2],
            "max_x": hi[0],
            "max_y": hi[1],
            "max_z": hi[2],
        },
        "outer_loop_edges": 4,
        "longest_edge": {"direction": list(edge), "length": 1.0},
        "adjacent": [{"face_id": "?", "surface": "plane", "normal": list(n)} for n in adjacent],
        "mesh": {
            "part_index": 0,
            "face_index": tri,
            "triangle_start": tri * 2,
            "triangle_count": 2,
        },
    }


def box_faces(
    w: float, d: float, h: float, *, ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """The six faces of a box from (0,0,0) to (w,d,h)."""
    names = ids or [f"s0f{i}" for i in range(6)]
    cx, cy, cz = w / 2, d / 2, h / 2
    z_adj = [_AXES["-x"], _AXES["+x"], _AXES["-y"], _AXES["+y"]]
    x_adj = [_AXES["-y"], _AXES["+y"], _AXES["-z"], _AXES["+z"]]
    y_adj = [_AXES["-x"], _AXES["+x"], _AXES["-z"], _AXES["+z"]]
    long_xy = (1.0, 0.0, 0.0) if w >= d else (0.0, 1.0, 0.0)
    long_xz = (1.0, 0.0, 0.0) if w >= h else (0.0, 0.0, 1.0)
    long_yz = (0.0, 1.0, 0.0) if d >= h else (0.0, 0.0, 1.0)
    return [
        _face(
            names[0],
            _AXES["-x"],
            (0, cy, cz),
            d * h,
            half=(0, cy, cz),
            edge=long_yz,
            adjacent=x_adj,
            tri=0,
        ),
        _face(
            names[1],
            _AXES["+x"],
            (w, cy, cz),
            d * h,
            half=(0, cy, cz),
            edge=long_yz,
            adjacent=x_adj,
            tri=1,
        ),
        _face(
            names[2],
            _AXES["-y"],
            (cx, 0, cz),
            w * h,
            half=(cx, 0, cz),
            edge=long_xz,
            adjacent=y_adj,
            tri=2,
        ),
        _face(
            names[3],
            _AXES["+y"],
            (cx, d, cz),
            w * h,
            half=(cx, 0, cz),
            edge=long_xz,
            adjacent=y_adj,
            tri=3,
        ),
        _face(
            names[4],
            _AXES["-z"],
            (cx, cy, 0),
            w * d,
            half=(cx, cy, 0),
            edge=long_xy,
            adjacent=z_adj,
            tri=4,
        ),
        _face(
            names[5],
            _AXES["+z"],
            (cx, cy, h),
            w * d,
            half=(cx, cy, 0),
            edge=long_xy,
            adjacent=z_adj,
            tri=5,
        ),
    ]


def _planar(raw: list[dict[str, Any]]) -> list[PlanarFace]:
    return [PlanarFace.model_validate(f) for f in raw]


def _top(faces: list[PlanarFace]) -> PlanarFace:
    return max((f for f in faces if f.normal[2] > 0.99), key=lambda f: f.offset)


def _rect_source(depth: float) -> dict[str, Any]:
    """A recorded exact build: a 20 x 30 rectangle on XY padded by ``depth``."""
    return {
        "kind": "design_ir",
        "parametric_sketch_ref": "PSK-BOX@1",
        "feature_id": "F1",
        "design_ir": {
            "entities": [
                {"id": "body1", "op": "create_body"},
                {
                    "id": "sketch1",
                    "op": "sketch",
                    "body_ref": "body1",
                    "plane": "XY",
                    "offset": 0.0,
                    "elements": [
                        {
                            "id": "r1",
                            "type": "rectangle",
                            "origin": [0, 0],
                            "width": 20,
                            "height": 30,
                        }
                    ],
                },
                {
                    "id": "feature1",
                    "op": "pad",
                    "body_ref": "body1",
                    "sketch_ref": "sketch1",
                    "depth": depth,
                },
            ]
        },
    }


# ── frame (Appendix A, PLN-004) ──────────────────────────────────────────


@pytest.mark.parametrize("which", range(6))
def test_the_frame_from_a_face_is_right_handed_and_outward(which: int) -> None:
    face = _planar(box_faces(20, 30, 10))[which]
    frame = frame_from_face(face)
    assert frame.normal == pytest.approx(face.normal)  # outward: the face's own normal
    assert cross(frame.u_axis, frame.v_axis) == pytest.approx(frame.normal)
    assert dot(frame.u_axis, frame.v_axis) == pytest.approx(0.0, abs=1e-12)
    # The origin is the centroid on the face's plane.
    assert dot(frame.normal, frame.origin) == pytest.approx(face.offset)
    # U lies along the face's longest outer edge.
    assert abs(dot(frame.u_axis, tuple(face.longest_edge["direction"]))) == pytest.approx(1.0)  # type: ignore[index]
    # And it is a valid stored plane.
    plane = SketchPlane(
        plane_id="PLN-X",
        kind=PlaneKind.FACE,
        origin=frame.origin,
        u_axis=frame.u_axis,
        v_axis=frame.v_axis,
        normal=frame.normal,
        unit=PlaneUnit.MM,
        attachment={"item_key": "CAD-X", "revision": 1, "face_ref": face.face_id},
        state=PlaneState.ATTACHED,
    )
    assert plane.design_ir_fields()["frame"]["normal"] == frame.normal


def test_a_tilted_face_with_no_straight_edge_still_gets_a_frame() -> None:
    n = (1 / math.sqrt(2), 0.0, 1 / math.sqrt(2))
    face = PlanarFace(face_id="f", normal=n, offset=5.0, area=3.0, centroid=(3.0, 1.0, 4.0))
    frame = frame_from_face(face)
    assert cross(frame.u_axis, frame.v_axis) == pytest.approx(n)
    assert dot(frame.normal, frame.origin) == pytest.approx(5.0)


# ── signatures and remapping (PLN-009) ───────────────────────────────────


def test_semantic_tags_come_from_the_recorded_build() -> None:
    faces = tag_faces(_planar(box_faces(20, 30, 10)), _rect_source(10))
    tags = {f.face_id: f.semantic_tag for f in faces}
    assert tags["s0f5"] == "PSK-BOX:F1:top"
    assert tags["s0f4"] == "PSK-BOX:F1:bottom"
    assert tags["s0f2"] == "PSK-BOX:F1:side:r1.0"  # the edge y=0
    assert tags["s0f1"] == "PSK-BOX:F1:side:r1.1"  # the edge x=20
    assert len(expected_faces(_rect_source(10))) == 6
    assert expected_faces({"kind": "cadquery_script", "script": "x"}) == []


def test_a_box_that_gets_taller_remaps_its_top_uniquely() -> None:
    old = tag_faces(_planar(box_faces(20, 30, 10)), _rect_source(10))
    # The new revision numbers its faces differently: the index means nothing.
    shuffled = ["s0f5", "s0f4", "s0f3", "s0f2", "s0f1", "s0f0"]
    new = tag_faces(_planar(box_faces(20, 30, 20, ids=shuffled)), _rect_source(20))
    sig = signature_of(_top(old), part_size_of(old))
    result = remap(sig, new)
    assert result.outcome is RemapOutcome.UNIQUE
    assert result.face is not None and result.face.face_id == "s0f0"  # renumbered top
    assert result.face.offset == pytest.approx(20.0)
    assert result.runner_up is None or result.score - result.runner_up >= 0.1
    # The old top's id now names the -x side: it is never picked for it.
    assert next(f for f in new if f.face_id == "s0f5").normal[0] == pytest.approx(-1.0)


def test_without_tags_the_only_matching_face_still_remaps_uniquely() -> None:
    old = _planar(box_faces(20, 30, 10))
    new = _planar(box_faces(20, 30, 20))
    result = remap(signature_of(_top(old), part_size_of(old)), new)
    assert result.outcome is RemapOutcome.UNIQUE
    assert result.face is not None and result.face.face_id == "s0f5"


def _plate_with_bosses(centres: list[float]) -> list[PlanarFace]:
    """A 60 x 60 x 5 plate with 10 x 10 x 5 bosses on top at x = each centre."""
    out = [
        _face(
            "plate-top",
            _AXES["+z"],
            (0, 0, 5),
            3600 - 100 * len(centres),
            half=(30, 30, 0),
            edge=(1, 0, 0),
            adjacent=[_AXES["-x"], _AXES["+x"], _AXES["-y"], _AXES["+y"]] * (1 + len(centres)),
        ),
        _face(
            "plate-bottom",
            _AXES["-z"],
            (0, 0, 0),
            3600,
            half=(30, 30, 0),
            edge=(1, 0, 0),
            adjacent=[_AXES["-x"], _AXES["+x"], _AXES["-y"], _AXES["+y"]],
        ),
    ]
    for i, x in enumerate(centres):
        out.append(
            _face(
                f"boss{i}-top",
                _AXES["+z"],
                (x, 0, 10),
                100,
                half=(5, 5, 0),
                edge=(1, 0, 0),
                adjacent=[_AXES["-x"], _AXES["+x"], _AXES["-y"], _AXES["+y"]],
            )
        )
    return _planar(out)


def test_a_mirrored_feature_makes_the_remap_ambiguous() -> None:
    before = _plate_with_bosses([0.0])
    sig = signature_of(next(f for f in before if f.face_id == "boss0-top"), part_size_of(before))
    after = _plate_with_bosses([-15.0, 15.0])  # the boss became a mirrored pair
    result = remap(sig, after)
    assert result.outcome is RemapOutcome.AMBIGUOUS
    assert result.face is None
    assert {c["face_id"] for c in result.as_dict()["candidates"]} == {"boss0-top", "boss1-top"}


def test_a_removed_face_is_missing_and_never_swapped_for_another() -> None:
    before = _plate_with_bosses([0.0])
    sig = signature_of(next(f for f in before if f.face_id == "boss0-top"), part_size_of(before))
    result = remap(sig, _plate_with_bosses([]))
    assert result.outcome is RemapOutcome.MISSING
    assert result.face is None
    # The plate top faces the same way but is not the same face.
    plate = next(f for f in _plate_with_bosses([]) if f.face_id == "plate-top")
    assert score_candidate(sig, plate, part_size_of(before)).score < 0.6


def test_a_tag_mismatch_never_wins_on_geometry_alone() -> None:
    old = tag_faces(_planar(box_faces(20, 30, 10)), _rect_source(10))
    sig = signature_of(_top(old), part_size_of(old))
    # Same geometry, but the build now says another feature made that face.
    imposter = _top(old).model_copy(update={"semantic_tag": "PSK-OTHER:F9:top"})
    assert score_candidate(sig, imposter, part_size_of(old)).score < 0.6


# ── the service: attach, remap, block, resolve ───────────────────────────


class FaceCad(FakeCad):
    """The stub adapter, plus ``cadquery.describe_faces`` answering ``faces``."""

    def __init__(self) -> None:
        super().__init__()
        self.faces: list[dict[str, Any]] = box_faces(20, 30, 10)

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        if tool_id == "cadquery.describe_faces":
            self.calls.append(tool_id)
            return {
                "faces": [dict(f) for f in self.faces],
                "solid_count": 1,
                "face_count": len(self.faces),
                "bounding_box": None,
                "mesh_source": "occt-converter: per part, faces in explorer order",
            }
        return await super().invoke(tool_id, params, timeout)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[None]:
    import api_gateway.twin.blob_store as blob_store
    import digital_twin.storage.work_product_blobs as wpb

    stored: dict[str, bytes] = {}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"wp/{node_id}/{filename}"
        stored[key] = content
        return key

    monkeypatch.setattr(wpb, "store_work_product_blob", fake_store)
    monkeypatch.setattr(blob_store, "fetch_work_product_blob", lambda key: stored[key])
    monkeypatch.setattr(wpb, "fetch_work_product_blob", lambda key: stored[key])
    monkeypatch.setenv("METAFORGE_CAD_CACHE_DIR", str(tmp_path / "cad_cache"))
    ops.init_operations(None)
    fa.register()
    yield
    unregister_head_moved_listener(fa.remap_on_head_moved)
    ops.init_operations(None)
    set_job_head_resolver(None)
    set_exact_cad_service(None)
    for tool in (EXACT_BUILD_TOOL, STEP_EXPORT_TOOL):
        jobs_module._retry_factories.pop(tool, None)  # noqa: SLF001


@pytest.fixture
def h() -> Harness:
    harness = Harness()
    harness.cad = FaceCad()
    harness.service._bridge = harness.cad  # noqa: SLF001 - the stub, with faces
    set_exact_cad_service(harness.service)
    return harness


async def _base(h: Harness) -> str:
    """CAD-BASE: a validated 20 x 30 x 10 box."""
    v1 = await h.part("v1", name="Base")
    job = await h.run_build(v1["item_key"], script="result = box(20, 30, 10)")
    assert job.status == "succeeded", job.error
    return str(v1["item_key"])


async def _move_base(h: Harness, key: str, faces: list[dict[str, Any]], script: str) -> None:
    h.cad.faces = faces
    job = await h.run_build(key, script=script)
    assert job.status == "succeeded", job.error


async def _sketch(h: Harness) -> str:
    out = await make_sketch_strokes_recorder(h.twin)(
        PROJECT,
        SketchSaveRequest(
            save_id="save-face-sketch",
            name="Rib",
            strokes=[{"id": "s1", "tool": "pen", "points": [[0, 0], [5, 5]]}],
        ),
    )
    assert out.item_key
    return out.item_key


async def _attach(
    h: Harness, sketch: str, base: str, face_id: str = "s0f5", op: str = "attach-001"
) -> Any:
    return await fa.attach_to_face(
        h.twin, PROJECT, sketch, fa.AttachFaceRequest(op_id=op, item_key=base, face_id=face_id)
    )


async def _plane(h: Harness, sketch: str) -> SketchPlane:
    return (await sp.get_plane(h.twin, PROJECT, sketch)).plane


def _events(op_prefix: str) -> list[Any]:
    store = ops.get_operation_publisher().store
    return [e for e in store.events_after(PROJECT, 0) if e.operation_id.startswith(op_prefix)]


async def test_attach_uses_the_face_frame_and_stores_the_signature(h: Harness) -> None:
    base = await _base(h)
    sketch = await _sketch(h)
    listed = await fa.list_faces(h.twin, PROJECT, base)
    assert listed.item_ref == f"{base}@2" and len(listed.faces) == 6
    assert listed.mesh_map["s0f5"]["triangle_start"] == 10
    result = await _attach(h, sketch, base)
    plane = result.plane
    assert plane.kind is PlaneKind.FACE and plane.state is PlaneState.ATTACHED
    assert plane.origin == pytest.approx((10.0, 15.0, 10.0))
    assert plane.normal == pytest.approx((0.0, 0.0, 1.0))
    assert plane.u_axis == pytest.approx((0.0, 1.0, 0.0))  # the 30 mm edge
    assert plane.unit is PlaneUnit.MM
    assert plane.attachment is not None
    assert (plane.attachment.item_key, plane.attachment.revision) == (base, 2)
    assert plane.attachment.face_ref == "s0f5"
    assert plane.attachment.signature["area"] == pytest.approx(600.0)  # type: ignore[index]
    # The face catalogue is kept on the revision: one adapter call for both reads.
    assert h.cad.calls.count("cadquery.describe_faces") == 1
    # Idempotent per op_id; the same face again changes nothing.
    again = await _attach(h, sketch, base)
    assert again.duplicate and again.item_ref == result.item_ref
    same = await _attach(h, sketch, base, op="attach-002")
    assert same.unchanged
    completed = [e for e in _events("job:sketch-plane-attach-001") if e.type == "completed"]
    assert completed and completed[0].domain == "sketch"


async def test_only_validated_geometry_can_be_picked(h: Harness) -> None:
    v1 = await h.part("raw", name="Raw")
    sketch = await _sketch(h)
    with pytest.raises(fa.FaceAttachmentError, match="validated"):
        await fa.list_faces(h.twin, PROJECT, v1["item_key"])
    with pytest.raises(fa.FaceAttachmentError, match="validated"):
        await _attach(h, sketch, v1["item_key"])
    empty = await fa.attachable_revisions(h.twin, PROJECT)
    assert empty.items == [] and empty.reason and "validated" in empty.reason
    base = await _base(h)
    listed = await fa.attachable_revisions(h.twin, PROJECT)
    assert [i.item_key for i in listed.items] == [base]
    with pytest.raises(fa.FaceAttachmentError, match="no planar face"):
        await _attach(h, sketch, base, face_id="s9f9", op="attach-bad")


async def test_a_taller_box_moves_the_plane_with_its_face(h: Harness) -> None:
    base = await _base(h)
    sketch = await _sketch(h)
    await _attach(h, sketch, base)
    await _move_base(h, base, box_faces(20, 30, 20), "result = box(20, 30, 20)")
    plane = await _plane(h, sketch)
    assert plane.state is PlaneState.ATTACHED
    assert plane.origin == pytest.approx((10.0, 15.0, 20.0))  # moved with the face
    assert plane.attachment is not None and plane.attachment.revision == 3
    assert plane.attachment.remapped_from == f"{base}@2#s0f5"
    view = await sp.get_plane(h.twin, PROJECT, sketch)
    assert view.action == "remapped"
    done = [e for e in _events("job:sketch-plane-remap-") if e.type == "completed"]
    assert done and done[-1].domain == "sketch"


async def test_an_unmoved_face_carries_forward_without_a_new_revision(h: Harness) -> None:
    base = await _base(h)
    sketch = await _sketch(h)
    first = await _attach(h, sketch, base)
    await _move_base(h, base, box_faces(20, 30, 10), "result = box(20, 30, 10)  # tidy")
    view = await sp.get_plane(h.twin, PROJECT, sketch)
    assert view.item_ref == first.item_ref  # no new plane revision
    assert view.plane.attachment is not None and view.plane.attachment.revision == 3


def _mirrored_tops() -> list[dict[str, Any]]:
    """The box's top split into two identical tops, mirrored about x = 10."""
    faces = [f for f in box_faces(20, 30, 10) if f["face_id"] != "s0f5"]
    for i, x in enumerate((0.0, 20.0)):
        faces.append(
            _face(
                f"s0f{6 + i}",
                _AXES["+z"],
                (x, 15.0, 10.0),
                600.0,
                half=(10, 15, 0),
                edge=(0, 1, 0),
                adjacent=[_AXES["-x"], _AXES["+x"], _AXES["-y"], _AXES["+y"]],
                tri=6 + i,
            )
        )
    return faces


async def _psk_on(h: Harness, sketch: str) -> str:
    """An approved parametric sketch on the sketch's (face) plane, depth 4."""
    view = await sp.get_plane(h.twin, PROJECT, sketch)
    await _definition(
        h,
        item_type="parametric_sketch",
        wp_type=WorkProductType.PARAMETRIC_SKETCH,
        name="Parametric sketch: Rib",
        key="PSK-RIB",
        metadata={
            "approved": True,
            "parametric_sketch": {
                "unit": "mm",
                "plane_id": view.item_ref,
                "elements": [
                    {"id": "l1", "type": "line", "start": (0.0, 0.0), "end": (4.0, 0.0)},
                    {"id": "l2", "type": "line", "start": (4.0, 0.0), "end": (4.0, -2.0)},
                    {"id": "l3", "type": "line", "start": (4.0, -2.0), "end": (0.0, -2.0)},
                    {"id": "l4", "type": "line", "start": (0.0, -2.0), "end": (0.0, 0.0)},
                ],
                "constraints": [],
                "profiles": [{"primitive_ids": ["l1", "l2", "l3", "l4"]}],
                "feature_plan": [
                    {
                        "id": "F1",
                        "op": "extrude",
                        "profile_primitive_ids": ["l1", "l2", "l3", "l4"],
                        "parameters": {"depth": {"status": "provided", "value": 4, "unit": "mm"}},
                    }
                ],
            },
        },
    )
    return "PSK-RIB"


async def test_rc05_topology_change_asks_blocks_and_stales_dependents(h: Harness) -> None:
    base = await _base(h)
    sketch = await _sketch(h)
    await _attach(h, sketch, base)
    psk = await _psk_on(h, sketch)

    # Built on the face: the frame reaches the lowering, and the revision is pinned.
    built = await h.service.start_build(project_id=PROJECT, item_key=psk, session_id="s", wait=True)
    assert built["validated"], built
    rib = await find_item(h.twin, "CAD-RIB", PROJECT)
    head = await h.node(str(rib.head_node_id))
    assert b"cq.Plane(origin=" in (h.service.read_step(head) or b"")
    sketch_entity = head.metadata["exact_source"]["design_ir"]["entities"][1]
    assert sketch_entity["frame"]["normal"] == pytest.approx([0.0, 0.0, 1.0])
    assert head.metadata["staleness"] == "current"
    assert head.metadata["depends_on_items"][0]["item_key"].startswith("PLN-")
    export = await h.service.start_export(node_id=str(head.id), session_id="s", wait=True)
    assert export["step_export"]["state"] == "ready"

    # The base changes so two faces match equally: never a silent pick.
    await _move_base(h, base, _mirrored_tops(), "result = two_tops()")
    plane = await _plane(h, sketch)
    assert plane.state is PlaneState.REATTACHMENT_REQUIRED
    assert plane.origin == pytest.approx((10.0, 15.0, 10.0))  # the last valid frame is kept
    assert plane.attachment is not None and plane.attachment.revision == 2
    pending = plane.attachment.pending or {}
    assert pending["outcome"] == "ambiguous" and pending["revision"] == 3
    assert {c["face_id"] for c in pending["candidates"]} == {"s0f6", "s0f7"}
    review = [e for e in _events("job:sketch-plane-remap-") if e.type == "review_required"]
    assert review and review[-1].requires_action
    assert review[-1].action_refs[0].kind == "open"
    assert review[-1].action_refs[0].id.startswith("/twin?node=")
    assert "choose where it goes" in review[-1].title

    # Dependent evidence is stale through the pins: the rib and its STEP.
    rib_head = await h.node(str(rib.head_node_id))
    assert geometry_state_of("cad_model", rib_head.metadata) is GeometryState.STALE
    step = step_export_view(
        rib_head.metadata, revision=1, head_revision=1, content_hash=rib_head.content_hash
    )
    assert step is not None and step["state"] == "stale"

    # Authoritative commits on the plane are refused; reads and previews are not.
    with pytest.raises(PlaneReattachmentRequiredError, match="REATTACHMENT_REQUIRED"):
        await h.service.start_build(project_id=PROJECT, item_key=psk, session_id="s")
    with pytest.raises(PlaneReattachmentRequiredError):
        await h.service.start_build(project_id=PROJECT, item_key="CAD-RIB", session_id="s")
    assert await fa.plane_block_reason(h.twin, PROJECT, sp.plane_item_key(sketch))
    proposal = await sp.propose_plane(h.twin, PROJECT, sketch, sp.PlaneProposalRequest())
    assert proposal.requires_confirmation
    assert (await fa.list_faces(h.twin, PROJECT, base)).item_ref == f"{base}@3"

    # The person picks one of the two: attached again, and the build is allowed.
    resolved = await fa.resolve_reattachment(
        h.twin,
        PROJECT,
        sketch,
        fa.ResolveReattachmentRequest(op_id="resolve-001", action="pick_face", face_id="s0f7"),
    )
    assert resolved.plane.state is PlaneState.ATTACHED
    assert resolved.plane.origin == pytest.approx((20.0, 15.0, 10.0))
    assert resolved.plane.attachment.remapped_from == f"{base}@2#s0f5"  # type: ignore[union-attr]
    dup = await fa.resolve_reattachment(
        h.twin,
        PROJECT,
        sketch,
        fa.ResolveReattachmentRequest(op_id="resolve-001", action="pick_face", face_id="s0f7"),
    )
    assert dup.duplicate
    assert await fa.plane_block_reason(h.twin, PROJECT, sp.plane_item_key(sketch)) is None
    rebuilt = await h.service.start_build(
        project_id=PROJECT, item_key=psk, session_id="s", wait=True
    )
    assert rebuilt["validated"], rebuilt
    rib = await find_item(h.twin, "CAD-RIB", PROJECT)
    new_head = await h.node(str(rib.head_node_id))
    assert new_head.metadata["exact_source"]["design_ir"]["entities"][1]["frame"]["origin"] == (
        pytest.approx([20.0, 15.0, 10.0])
    )
    assert geometry_state_of("cad_model", new_head.metadata) is GeometryState.VALIDATED


async def test_a_removed_face_requires_reattachment(h: Harness) -> None:
    base = await _base(h)
    sketch = await _sketch(h)
    await _attach(h, sketch, base)
    no_top = [f for f in box_faces(20, 30, 10) if f["face_id"] != "s0f5"]
    await _move_base(h, base, no_top, "result = open_box()")
    plane = await _plane(h, sketch)
    assert plane.state is PlaneState.REATTACHMENT_REQUIRED
    assert (plane.attachment.pending or {})["outcome"] == "missing"  # type: ignore[union-attr]
    # A later revision where the face is back resolves it with no person needed.
    await _move_base(h, base, box_faces(20, 30, 12), "result = box(20, 30, 12)")
    again = await _plane(h, sketch)
    assert again.state is PlaneState.ATTACHED
    assert again.origin[2] == pytest.approx(12.0)


async def test_resolve_by_detaching(h: Harness) -> None:
    base = await _base(h)
    sketch = await _sketch(h)
    await _attach(h, sketch, base)
    with pytest.raises(fa.FaceAttachmentError, match="nothing to resolve"):
        await fa.resolve_reattachment(
            h.twin,
            PROJECT,
            sketch,
            fa.ResolveReattachmentRequest(op_id="resolve-early", action="detach_unassigned"),
        )
    await _move_base(h, base, _mirrored_tops(), "result = two_tops()")
    to_top = await fa.resolve_reattachment(
        h.twin,
        PROJECT,
        sketch,
        fa.ResolveReattachmentRequest(
            op_id="resolve-top", action="detach_principal", principal="XY"
        ),
    )
    assert to_top.plane.kind is PlaneKind.PRINCIPAL and to_top.plane.state is PlaneState.CONFIRMED
    assert to_top.plane.origin == pytest.approx((10.0, 15.0, 10.0))
    item = await find_item(h.twin, sp.plane_item_key(sketch), PROJECT)
    history = await item_history(h.twin, item)
    assert len(history) == 3  # attached, reattachment required, detached


# ── routes ───────────────────────────────────────────────────────────────


def test_routes_attach_list_resolve_and_block(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from api_gateway.twin.exact_cad_routes import router as exact_router
    from api_gateway.twin.face_attachment_routes import router as face_router

    monkeypatch.setattr(twin_routes, "get_twin", lambda: h.twin)
    app = FastAPI()
    app.include_router(face_router)
    app.include_router(exact_router)
    client = TestClient(app)
    loop = asyncio.new_event_loop()
    try:
        base = loop.run_until_complete(_base(h))
        sketch = loop.run_until_complete(_sketch(h))
        root = f"/v1/twin/projects/{PROJECT}"
        assert client.get(f"{root}/attachable-geometry").json()["items"][0]["item_key"] == base
        faces = client.get(f"{root}/items/{base}/faces").json()
        assert len(faces["faces"]) == 6 and "s0f5" in faces["mesh_map"]
        assert client.get(f"{root}/items/CAD-NOPE/faces").status_code == 404
        body = {"op_id": "route-attach-1", "item_key": base, "face_id": "s0f5"}
        attached = client.post(f"{root}/sketches/{sketch}/plane/attach-face", json=body)
        assert attached.status_code == 200, attached.text
        assert attached.json()["plane"]["state"] == "ATTACHED"
        assert client.post(f"{root}/sketches/{sketch}/plane/attach-face", json=body).json()[
            "duplicate"
        ]
        loop.run_until_complete(_psk_on(h, sketch))
        loop.run_until_complete(_move_base(h, base, _mirrored_tops(), "result = two_tops()"))
        refused = client.post("/v1/twin/items/PSK-RIB/exact-build", json={"project_id": PROJECT})
        assert refused.status_code == 409
        assert "REATTACHMENT_REQUIRED" in refused.json()["detail"]
        resolve = {"op_id": "route-resolve-1", "action": "detach_unassigned"}
        out = client.post(f"{root}/sketches/{sketch}/plane/resolve-reattachment", json=resolve)
        assert out.status_code == 200 and out.json()["plane"]["state"] == "UNASSIGNED"
        again = client.post(
            f"{root}/sketches/{sketch}/plane/resolve-reattachment",
            json={"op_id": "route-resolve-2", "action": "detach_unassigned"},
        )
        assert again.status_code == 409
    finally:
        loop.close()


# ── the adapter, for real when the kernel is here ────────────────────────


class TestRealKernel:
    @pytest.fixture(autouse=True)
    def _kernel(self) -> None:
        pytest.importorskip("cadquery")

    @staticmethod
    def _describe(shape: Any, tmp_path: Any) -> list[PlanarFace]:
        import cadquery as cq

        from tool_registry.tools.cadquery.faces import describe_faces

        path = tmp_path / "part.step"
        cq.exporters.export(shape, str(path))
        out = describe_faces(base64.b64encode(path.read_bytes()).decode(), work_dir=str(tmp_path))
        return _planar(out["faces"])

    def test_box_faces_are_outward_and_the_taller_top_remaps(self, tmp_path: Any) -> None:
        import cadquery as cq

        low = self._describe(cq.Workplane("XY").box(20, 30, 10, centered=False), tmp_path)
        high = self._describe(cq.Workplane("XY").box(20, 30, 20, centered=False), tmp_path)
        assert len(low) == 6
        for f in low:
            frame = frame_from_face(f)
            # Outward: the centroid of the box is behind every face.
            assert dot(f.normal, (10.0, 15.0, 5.0)) < f.offset
            assert cross(frame.u_axis, frame.v_axis) == pytest.approx(frame.normal)
        result = remap(signature_of(_top(low), part_size_of(low)), high)
        assert result.outcome is RemapOutcome.UNIQUE and result.face is not None
        assert result.face.offset == pytest.approx(20.0)

    def test_the_mesh_map_covers_every_triangle_in_order(self, tmp_path: Any) -> None:
        import cadquery as cq

        faces = self._describe(cq.Workplane("XY").box(20, 30, 10), tmp_path)
        ranges = sorted((f.mesh["triangle_start"], f.mesh["triangle_count"]) for f in faces)  # type: ignore[index]
        position = 0
        for start, count in ranges:
            assert start == position and count > 0
            position += count
