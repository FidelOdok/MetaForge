"""The L2 preview, the preview versus exact comparison, and Strategy A commits (C3D slice 5).

Covers spec section 2 (one shared feature definition for the preview and the
exact path), section 7 (bounding box, volume, sampled bidirectional surface
distance and intended features, with a per-component tolerance and no
universal threshold), section 8 (a manipulation is only ever a parameter edit
committed through the normal path), SYNC-005 (discrepancies are detected and
stored on the exact revision), SYNC-008 (an unchanged definition is not
rebuilt; only the changed component is) and 3D-RC-005 (a stale result never
replaces a newer head), as ``docs/twin_schema.md`` and ``docs/create-view.md``
state them.
"""

from __future__ import annotations

import json
import math
import struct
from typing import Any

import pytest
from fastapi import FastAPI

from api_gateway.twin.parametric_preview import (
    ParameterCommitRequest,
    ParametricPreviewError,
    commit_parameters,
    preview_definition,
)
from api_gateway.twin.preview_comparison import (
    Tolerance,
    compare_preview_to_exact,
    comparison_for_source,
    mesh_genus,
    read_glb_triangles,
)
from tests.unit.test_exact_cad import (  # noqa: F401 - _isolated is an autouse fixture
    PROJECT,
    FakeCad,
    Harness,
    _definition,
    _isolated,
    _parametric,
    _slot_sketch,
)
from twin_core.design_ir.feature_definition import (
    PreviewSolid,
    definition_from_entities,
    definition_hash,
    feature_entities,
    frame_of,
    profile_loops,
)
from twin_core.items import ItemRevisionConflictError, find_item
from twin_core.representation import preview_descriptor, representation_of

Tri = tuple[tuple[float, float, float], ...]

# ---------------------------------------------------------------------------
# Meshes the "kernel" returns, built by hand
# ---------------------------------------------------------------------------


def glb_of(tris: list[Any]) -> bytes:
    """A minimal binary glTF holding ``tris`` as one indexed mesh."""
    verts: list[tuple[float, float, float]] = []
    index: dict[tuple[float, float, float], int] = {}
    idx: list[int] = []
    for t in tris:
        for p in t:
            key = (float(p[0]), float(p[1]), float(p[2]))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            idx.append(index[key])
    pos = b"".join(struct.pack("<3f", *v) for v in verts)
    ind = b"".join(struct.pack("<I", i) for i in idx)
    binary = pos + ind
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(ind)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"},
        ],
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


def plate_mesh(
    nx: int, ny: int, cell: float, depth: float, holes: set[tuple[int, int]]
) -> list[Any]:
    """A closed plate of ``nx`` x ``ny`` cells with the ``holes`` cells cut through."""
    filled = {(i, j) for i in range(nx) for j in range(ny)} - holes
    tris: list[Any] = []

    def quad(a: Any, b: Any, c: Any, d: Any) -> None:
        tris.append((a, b, c))
        tris.append((a, c, d))

    for i, j in filled:
        x0, x1, y0, y1 = i * cell, (i + 1) * cell, j * cell, (j + 1) * cell
        quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0))
        quad((x0, y0, depth), (x0, y1, depth), (x1, y1, depth), (x1, y0, depth))
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (i + di, j + dj) in filled:
                continue
            if di == 1:
                quad((x1, y0, 0.0), (x1, y1, 0.0), (x1, y1, depth), (x1, y0, depth))
            elif di == -1:
                quad((x0, y0, 0.0), (x0, y0, depth), (x0, y1, depth), (x0, y1, 0.0))
            elif dj == 1:
                quad((x0, y1, 0.0), (x0, y1, depth), (x1, y1, depth), (x1, y1, 0.0))
            else:
                quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y0, depth), (x0, y0, depth))
    return tris


def prism_mesh(loop: list[tuple[float, float]], depth: float, dz: float = 0.0) -> list[Any]:
    """A closed prism over a convex ``loop`` (fan caps), lifted by ``dz``."""
    cx = sum(p[0] for p in loop) / len(loop)
    cy = sum(p[1] for p in loop) / len(loop)
    tris: list[Any] = []
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        tris.append(((cx, cy, dz), (b[0], b[1], dz), (a[0], a[1], dz)))
        tris.append(((cx, cy, depth + dz), (a[0], a[1], depth + dz), (b[0], b[1], depth + dz)))
        tris.append(((a[0], a[1], dz), (b[0], b[1], dz), (b[0], b[1], depth + dz)))
        tris.append(((a[0], a[1], dz), (b[0], b[1], depth + dz), (a[0], a[1], depth + dz)))
    return tris


def _plate_definition(depth: float = 2.0) -> dict[str, Any]:
    """A 30 x 20 plate with a 10 x 10 square through-hole at (10, 10)."""
    hole = [
        {"id": "h1", "type": "line", "start": (10.0, 10.0), "end": (20.0, 10.0)},
        {"id": "h2", "type": "line", "start": (20.0, 20.0), "end": (20.0, 10.0)},
        {"id": "h3", "type": "line", "start": (20.0, 20.0), "end": (10.0, 20.0)},
        {"id": "h4", "type": "line", "start": (10.0, 20.0), "end": (10.0, 10.0)},
    ]
    sketch = {
        "id": "sketch1",
        "op": "sketch",
        "body_ref": "body1",
        "plane": "XY",
        "offset": 0.0,
        "elements": [
            {"id": "r", "type": "rectangle", "origin": (0.0, 0.0), "width": 30.0, "height": 30.0},
            *hole,
        ],
    }
    return definition_from_entities(feature_entities(sketch, op="extrude", amount=depth))


PLATE_BBOX = {"min_x": 0.0, "min_y": 0.0, "min_z": 0.0, "max_x": 30.0, "max_y": 30.0, "max_z": 2.0}
PLATE_VOLUME = (900.0 - 100.0) * 2.0


# ---------------------------------------------------------------------------
# The shared definition
# ---------------------------------------------------------------------------


def test_the_preview_reads_the_entities_the_exact_path_lowers() -> None:
    d = _plate_definition()
    assert d["op"] == "extrude" and d["depth"] == 2.0
    assert d["frame"]["normal"] == [0.0, 0.0, 1.0]
    solid = PreviewSolid.from_definition(d)
    assert solid.through_holes == 1
    assert solid.volume() == pytest.approx(PLATE_VOLUME)
    # The hash ignores key order and float noise, and moves with a parameter.
    entities = feature_entities(
        {"id": "sketch1", "op": "sketch", "elements": []}, op="extrude", amount=2.0
    )
    shuffled = [dict(reversed(list(e.items()))) for e in entities]
    assert definition_hash(entities) == definition_hash(shuffled)
    moved = feature_entities(
        {"id": "sketch1", "op": "sketch", "elements": []}, op="extrude", amount=2.5
    )
    assert definition_hash(entities) != definition_hash(moved)


def test_arc_profiles_chain_and_revolve_follows_pappus() -> None:
    slot = _slot_sketch()["elements"]
    profile = profile_loops(slot)
    assert profile.open_chains == 0 and not profile.holes
    revolve = {
        "op": "revolve",
        "elements": [{"type": "rectangle", "origin": (10.0, 0.0), "width": 5.0, "height": 20.0}],
        "frame": frame_of(None),
        "angle": 180.0,
    }
    solid = PreviewSolid.from_definition(revolve)
    assert solid.volume() == pytest.approx(math.pi * (15**2 - 10**2) * 20 / 2)
    assert max(solid.distance(p) for p in solid.samples(64)) < 1e-9
    # CadQuery's XZ plane: x along X, normal along -Y, so v runs along +Z.
    assert frame_of({"plane": "XZ", "offset": 3})["origin"] == [0.0, -3.0, 0.0]


# ---------------------------------------------------------------------------
# The comparison helper on known shapes
# ---------------------------------------------------------------------------


def test_a_faithful_preview_is_consistent_on_every_check() -> None:
    tris = read_glb_triangles(glb_of(plate_mesh(3, 3, 10.0, 2.0, {(1, 1)})))
    record = compare_preview_to_exact(
        _plate_definition(), exact_bbox=PLATE_BBOX, exact_volume=PLATE_VOLUME, exact_triangles=tris
    )
    assert record["outcome"] == "consistent", record["checks"]
    by = {c["check"]: c for c in record["checks"]}
    assert by["features"]["intended_through_holes"] == 1
    assert by["features"]["exact_through_holes"] == 1
    assert by["surface_distance"]["hausdorff_mm"] < 1e-4
    assert by["volume"]["value_rel"] < 1e-9
    assert (
        record["tolerance"]["source"] == "default"
        and "not a manufacturing" in record["tolerance"]["basis"]
    )


def test_a_missing_hole_and_a_shifted_surface_are_discrepancies_with_values() -> None:
    solid_plate = plate_mesh(3, 3, 10.0, 2.0, set())
    record = compare_preview_to_exact(
        _plate_definition(),
        exact_bbox=PLATE_BBOX,
        exact_volume=1800.0,
        exact_triangles=read_glb_triangles(glb_of(solid_plate)),
    )
    assert record["outcome"] == "discrepancy"
    failed = {c["check"]: c for c in record["discrepancies"]}
    assert failed["features"]["exact_through_holes"] == 0
    assert failed["volume"]["exact_mm3"] == 1800.0 and failed["volume"][
        "preview_mm3"
    ] == pytest.approx(1600.0)
    # The hole's centre is 5 mm from the nearest preview surface. A bounded sample
    # estimates the largest distance from below, and still sees the hole.
    assert 1.0 < failed["surface_distance"]["hausdorff_mm"] <= 5.0 + 1e-6

    lifted = [
        tuple((p[0], p[1], p[2] + 1.0) for p in t) for t in plate_mesh(3, 3, 10.0, 2.0, {(1, 1)})
    ]
    shifted_box = {**PLATE_BBOX, "min_z": 1.0, "max_z": 3.0}
    record = compare_preview_to_exact(
        _plate_definition(),
        exact_bbox=shifted_box,
        exact_volume=PLATE_VOLUME,
        exact_triangles=lifted,
    )
    by = {c["check"]: c for c in record["checks"]}
    assert by["surface_distance"]["outcome"] == "fail"
    assert by["surface_distance"]["hausdorff_mm"] == pytest.approx(1.0, abs=1e-6)
    assert by["bounding_box"]["value_mm"] == pytest.approx(1.0)


def test_the_tolerance_is_per_component_and_never_universal() -> None:
    lifted = [
        tuple((p[0], p[1], p[2] + 1.0) for p in t) for t in plate_mesh(3, 3, 10.0, 2.0, {(1, 1)})
    ]
    shifted_box = {**PLATE_BBOX, "min_z": 1.0, "max_z": 3.0}
    loose = Tolerance.from_config({"abs_mm": 1.5, "basis": "a cosmetic cover"})
    record = compare_preview_to_exact(
        _plate_definition(),
        exact_bbox=shifted_box,
        exact_volume=PLATE_VOLUME,
        exact_triangles=lifted,
        tolerance=loose,
    )
    assert record["outcome"] == "consistent"
    assert record["tolerance"] == {**record["tolerance"], "abs_mm": 1.5, "source": "component"}


def test_without_the_exact_tessellation_the_mesh_checks_do_not_pass() -> None:
    record = compare_preview_to_exact(
        _plate_definition(), exact_bbox=PLATE_BBOX, exact_volume=PLATE_VOLUME, exact_triangles=None
    )
    assert record["outcome"] == "incomplete"
    by = {c["check"]: c["outcome"] for c in record["checks"]}
    assert by == {
        "bounding_box": "pass",
        "dimensions": "pass",
        "volume": "pass",
        "surface_distance": "not_evaluated",
        "features": "not_evaluated",
    }
    # An open mesh claims no hole count.
    genus, note = mesh_genus(plate_mesh(3, 3, 10.0, 2.0, {(1, 1)})[:-2], weld=1e-4)
    assert genus is None and "not closed" in note
    # A source that is not one preview feature has no comparison at all.
    assert (
        comparison_for_source({"kind": "cadquery_script", "script": "x"}, validity={}, glb=None)
        is None
    )


# ---------------------------------------------------------------------------
# The exact path: comparison stored, skip, commits
# ---------------------------------------------------------------------------

#: The rounded slot of ``_slot_sketch`` on XY: x -10..50, v -20..0, 5 deep.
SLOT_BBOX = {
    "min_x": -10.0,
    "min_y": -20.0,
    "min_z": 0.0,
    "max_x": 50.0,
    "max_y": 0.0,
    "max_z": 5.0,
}
SLOT_VOLUME = (40.0 * 20.0 + math.pi * 100.0) * 5.0


class SlotCad(FakeCad):
    """The adapter, measuring the slot the way the kernel would."""

    def __init__(self) -> None:
        super().__init__()
        self.box: dict[str, float] = dict(SLOT_BBOX)
        self.volume = SLOT_VOLUME

    def _measures(self) -> dict[str, Any]:
        return {"solid_count": 1, "volume_mm3": self.volume, "bounding_box": dict(self.box)}

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        out = await super().invoke(tool_id, params, timeout)
        if tool_id == "cadquery.execute_script":
            out.update(volume_mm3=self.volume, bounding_box=dict(self.box))
        return out


def _slot_glb(depth: float = 5.0) -> bytes:
    definition = {
        "op": "extrude",
        "elements": [
            {**e, "start": (e["start"][0], -e["start"][1]), "end": (e["end"][0], -e["end"][1])}
            if e["type"] == "line"
            else {
                **e,
                "center": (e["center"][0], -e["center"][1]),
                "start_angle": (-e["end_angle"]) % 360,
                "end_angle": (-e["start_angle"]) % 360,
            }
            for e in _slot_sketch()["elements"]
        ],
        "frame": frame_of(None),
        "depth": depth,
    }
    loop = PreviewSolid.from_definition(definition).profile.outer
    return glb_of(prism_mesh(loop, depth))


@pytest.fixture
def h() -> Harness:
    harness = Harness()
    harness.cad = SlotCad()
    harness.service._bridge = harness.cad  # noqa: SLF001
    harness.service._read_mesh = lambda step: _slot_glb()  # noqa: SLF001
    return harness


async def _build(h: Harness, **inputs: Any) -> Any:
    reply = await h.service.start_build(
        project_id=PROJECT,
        item_key="PSK-SLOT",
        inputs={"material": "PLA", **inputs},
        session_id="s",
    )
    job = await h.runner.wait(reply["job_id"], 5.0)
    assert job.status == "succeeded", job.error
    return job


async def test_the_comparison_is_stored_on_the_exact_revision(h: Harness) -> None:
    await _parametric(h, depth=5)
    job = await _build(h)
    assert job.result["preview_comparison"]["outcome"] == "consistent"
    cad = await find_item(h.twin, "CAD-SLOT", PROJECT)
    head = await h.node(str(cad.head_node_id))
    record = head.metadata["preview_comparison"]
    assert record["item_ref"] == "CAD-SLOT@1"
    assert record["preview"]["sketch_ref"] == "PSK-SLOT@1"
    assert record["preview"]["definition_hash"] == head.metadata["exact_source"]["definition_hash"]
    assert {c["check"] for c in record["checks"]} == {
        "bounding_box",
        "dimensions",
        "volume",
        "surface_distance",
        "features",
    }
    # A kernel that built something else: the discrepancy is recorded, the revision stays validated.
    h.cad.volume = SLOT_VOLUME * 2.0
    await _parametric_depth(h, 6)
    job = await _build(h)
    cad = await find_item(h.twin, "CAD-SLOT", PROJECT)
    head = await h.node(str(cad.head_node_id))
    assert head.metadata["geometry_state"] == "validated"
    assert head.metadata["preview_comparison"]["outcome"] == "discrepancy"
    assert "volume" in job.result["preview_comparison"]["discrepancies"]


async def _parametric_depth(h: Harness, depth: float) -> Any:
    """The person changed the depth on the sketch (a new PSK revision)."""
    from twin_core.models.enums import WorkProductType

    return await _definition(
        h,
        item_type="parametric_sketch",
        wp_type=WorkProductType.PARAMETRIC_SKETCH,
        name="Parametric sketch: Slot",
        key="PSK-SLOT",
        metadata={"parametric_sketch": _slot_sketch(depth=depth), "approved": True},
    )


async def test_an_unchanged_definition_is_not_rebuilt(h: Harness) -> None:
    await _parametric(h, depth=5)
    await _build(h)
    calls = list(h.cad.calls)
    again = await h.service.start_build(
        project_id=PROJECT, item_key="PSK-SLOT", inputs={"material": "PLA"}, session_id="t"
    )
    assert again["skipped"] is True and again["job_id"] is None
    assert again["result"]["skipped"] == "definition_unchanged"
    assert h.cad.calls == calls  # nothing ran on the kernel
    cad = await find_item(h.twin, "CAD-SLOT", PROJECT)
    assert cad.head_revision == 1
    # A changed definition is rebuilt.
    await _parametric_depth(h, 7)
    job = await _build(h)
    assert job.result.get("skipped") is None
    assert (await find_item(h.twin, "CAD-SLOT", PROJECT)).head_revision == 2


async def _pin_sketch(h: Harness) -> Any:
    """A second component: a round pin on the same plane."""
    from twin_core.models.enums import WorkProductType

    sketch = {
        "unit": "mm",
        "plane_id": "PLN-SLOT@1",
        "elements": [{"id": "c1", "type": "circle", "center": (0.0, 0.0), "radius": 4.0}],
        "constraints": [
            {
                "id": "r1",
                "type": "radius",
                "refs": ["c1"],
                "value": 4.0,
                "unit": "mm",
                "status": "accepted",
                "source": "human_confirmed",
            }
        ],
        "profiles": [{"primitive_ids": ["c1"]}],
        "feature_plan": [
            {
                "id": "feat-extrude-1",
                "op": "extrude",
                "profile_primitive_ids": ["c1"],
                "parameters": {
                    "depth": {"status": "provided", "value": 12, "unit": "mm"},
                    "material": {"status": "provided", "value": "PLA"},
                },
            }
        ],
    }
    return await _definition(
        h,
        item_type="parametric_sketch",
        wp_type=WorkProductType.PARAMETRIC_SKETCH,
        name="Parametric sketch: Pin",
        key="PSK-PIN",
        metadata={"parametric_sketch": sketch, "approved": True},
    )


async def test_a_parameter_commit_schedules_exactly_one_build_with_its_base(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _parametric(h, depth=5)
    await _pin_sketch(h)
    await _build(h)  # CAD-SLOT@1
    started: list[dict[str, Any]] = []
    real_start = h.runner.start

    async def spy(tool: str, args: dict[str, Any], *a: Any, **kw: Any) -> Any:
        started.append(
            {"tool": tool, "target_key": kw.get("target_key"), "base": kw.get("base_revision")}
        )
        return await real_start(tool, args, *a, **kw)

    monkeypatch.setattr(h.runner, "start", spy)
    out = await commit_parameters(
        h.twin,
        h.service,
        project_id=PROJECT,
        sketch_key="PSK-SLOT",
        request=ParameterCommitRequest(
            base_revision=1, values={"depth": 8, "material": "PLA"}, source="drag", preview_ms=3.2
        ),
    )
    assert out["sketch_ref"] == "PSK-SLOT@2" and out["base_revision"] == "PSK-SLOT@1"
    assert out["classification"]["change_class"] == "geometric"
    # Exactly one build, of the changed component only, pinned to the CAD head.
    assert started == [{"tool": "twin.build_exact_cad", "target_key": "CAD-SLOT", "base": "1"}]
    job = await h.runner.wait(out["build"]["job_id"], 5.0)
    assert job.status == "succeeded", job.error
    cad = await find_item(h.twin, "CAD-SLOT", PROJECT)
    head = await h.node(str(cad.head_node_id))
    assert head.metadata["exact_source"]["parametric_sketch_ref"] == "PSK-SLOT@2"
    assert head.metadata["exact_source"]["design_ir"]["entities"][2]["depth"] == 8.0
    assert await find_item(h.twin, "CAD-PIN", PROJECT) is None
    # The edit is the person's, recorded as such on the sketch revision.
    psk = await find_item(h.twin, "PSK-SLOT", PROJECT)
    node = await h.node(str(psk.head_node_id))
    assert node.metadata["parameter_edit"]["source"] == "drag"
    assert node.metadata["change_classification"]["change_class"] == "geometric"
    assert node.metadata["approved"] is True


async def test_a_parameter_commit_keeps_a_reconstructed_sketchs_plane(h: Harness) -> None:
    """A reconstructed sketch has no plane node: its plane is the principal placement
    accepted with the proposal. A parameter edit must carry it, or exact CAD refuses
    the next revision with "the sketch is not placed on a plane yet"."""
    from twin_core.design_ir.feature_history import PRINCIPAL_PLACEMENT_KEY, principal_placement
    from twin_core.models.enums import WorkProductType

    sketch = {**_slot_sketch(depth=5), "plane_id": None}
    placed = {"plane": "XY", "offset": 0.0, "status": "estimated"}
    await _definition(
        h,
        item_type="parametric_sketch",
        wp_type=WorkProductType.PARAMETRIC_SKETCH,
        name="Parametric sketch: Slot",
        key="PSK-SLOT",
        metadata={
            "parametric_sketch": sketch,
            "approved": True,
            "plane_id": None,
            PRINCIPAL_PLACEMENT_KEY: placed,
        },
    )
    out = await commit_parameters(
        h.twin,
        h.service,
        project_id=PROJECT,
        sketch_key="PSK-SLOT",
        request=ParameterCommitRequest(
            base_revision=1, values={"depth": 8, "material": "PLA"}, source="numeric"
        ),
    )
    assert out["sketch_ref"] == "PSK-SLOT@2"
    psk = await find_item(h.twin, "PSK-SLOT", PROJECT)
    node = await h.node(str(psk.head_node_id))
    assert node.metadata[PRINCIPAL_PLACEMENT_KEY] == placed
    assert principal_placement(node.metadata) == {"plane": "XY", "offset": 0.0}
    # And the build it scheduled gets past the plane check.
    job = await h.runner.wait(out["build"]["job_id"], 5.0)
    assert job.status == "succeeded", job.error


async def test_a_stale_commit_and_a_stale_result_are_rejected(h: Harness) -> None:
    await _parametric(h, depth=5)
    await _build(h)
    # Edited from PSK-SLOT@1 after someone else moved it on: refused, nothing scheduled.
    await _parametric_depth(h, 6)
    with pytest.raises(ItemRevisionConflictError):
        await commit_parameters(
            h.twin,
            h.service,
            project_id=PROJECT,
            sketch_key="PSK-SLOT",
            request=ParameterCommitRequest(base_revision=1, values={"depth": 9, "material": "PLA"}),
        )
    # The build of a fresh commit races a newer CAD head: the late result never replaces it.
    newer: dict[str, Any] = {}

    async def person_commits_meanwhile() -> None:
        newer.update(await h.part("edited by a person", name="Slot"))

    h.cad.on_execute = person_commits_meanwhile
    out = await commit_parameters(
        h.twin,
        h.service,
        project_id=PROJECT,
        sketch_key="PSK-SLOT",
        request=ParameterCommitRequest(base_revision=2, values={"depth": 9, "material": "PLA"}),
    )
    job = await h.runner.wait(out["build"]["job_id"], 5.0)
    assert job.status == "stale"
    cad = await find_item(h.twin, "CAD-SLOT", PROJECT)
    assert str(cad.head_node_id) == newer["node_id"]
    assert cad.head_revision == 2


async def test_a_late_build_against_a_moved_cad_head_ends_stale(h: Harness) -> None:
    await _parametric(h, depth=5)
    await _build(h)
    await _parametric_depth(h, 6)
    cad = await find_item(h.twin, "CAD-SLOT", PROJECT)
    newer: dict[str, Any] = {}

    async def person_commits_meanwhile() -> None:
        from api_gateway.twin import exact_cad as module

        content = b"ISO-10303-21;\nedited\nENDSEC;\n"
        module._cache_put(content)  # noqa: SLF001
        import base64

        newer.update(
            await h.record(
                step_base64=base64.b64encode(content).decode(),
                name="Slot",
                project_id=PROJECT,
                item_key=f"CAD-SLOT@{cad.head_revision}",
            )
        )

    h.cad.on_execute = person_commits_meanwhile
    reply = await h.service.start_build(
        project_id=PROJECT, item_key="PSK-SLOT", inputs={"material": "PLA"}, session_id="s"
    )
    job = await h.runner.wait(reply["job_id"], 5.0)
    assert job.status == "stale"
    after = await find_item(h.twin, "CAD-SLOT", PROJECT)
    assert str(after.head_node_id) == newer["node_id"]


async def test_a_radius_dimension_commit_moves_the_circle_and_its_rule(h: Harness) -> None:
    await _parametric(h, depth=5)  # the plane
    await _pin_sketch(h)
    out = await commit_parameters(
        h.twin,
        h.service,
        project_id=PROJECT,
        sketch_key="PSK-PIN",
        request=ParameterCommitRequest(base_revision=1, dimensions={"r1": 6.5}, build=False),
    )
    assert out["build"] is None
    psk = await find_item(h.twin, "PSK-PIN", PROJECT)
    node = await h.node(str(psk.head_node_id))
    content = node.metadata["parametric_sketch"]
    assert content["elements"][0]["radius"] == 6.5
    assert content["constraints"][0]["value"] == 6.5
    with pytest.raises(ParametricPreviewError) as caught:
        await commit_parameters(
            h.twin,
            h.service,
            project_id=PROJECT,
            sketch_key="PSK-PIN",
            request=ParameterCommitRequest(base_revision=2, dimensions={"nope": 3.0}),
        )
    assert caught.value.status == 422
    with pytest.raises(ParametricPreviewError):
        await commit_parameters(
            h.twin,
            h.service,
            project_id=PROJECT,
            sketch_key="PSK-PIN",
            request=ParameterCommitRequest(base_revision=2, values={"width": 3.0}),
        )


async def _box_sketch(h: Harness) -> Any:
    """A 40 x 20 plate: four joined lines, square corners, both sizes confirmed."""
    from twin_core.models.enums import WorkProductType

    def line(i: str, a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
        return {"id": i, "type": "line", "start": a, "end": b}

    def rule(i: str, t: str, refs: list[str], value: float | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"id": i, "type": t, "refs": refs, "status": "accepted"}
        if value is not None:
            out.update(value=value, unit="mm", source="human_confirmed")
        return out

    sketch = {
        "unit": "mm",
        "plane_id": "PLN-SLOT@1",
        "elements": [
            line("top", (0.0, 0.0), (40.0, 0.0)),
            line("right", (40.0, 0.0), (40.0, 20.0)),
            line("bottom", (40.0, 20.0), (0.0, 20.0)),
            line("left", (0.0, 20.0), (0.0, 0.0)),
        ],
        "constraints": [
            rule("k1", "coincident", ["top.end", "right.start"]),
            rule("k2", "coincident", ["right.end", "bottom.start"]),
            rule("k3", "coincident", ["bottom.end", "left.start"]),
            rule("k4", "coincident", ["left.end", "top.start"]),
            rule("h1", "horizontal", ["top"]),
            rule("h2", "horizontal", ["bottom"]),
            rule("v1", "vertical", ["right"]),
            rule("v2", "vertical", ["left"]),
            rule("L1", "length", ["top"], 40.0),
            rule("L2", "length", ["right"], 20.0),
        ],
        "profiles": [{"primitive_ids": ["top", "right", "bottom", "left"]}],
        "feature_plan": [
            {
                "id": "feat-extrude-1",
                "op": "extrude",
                "profile_primitive_ids": ["top", "right", "bottom", "left"],
                "parameters": {
                    "depth": {"status": "provided", "value": 3, "unit": "mm"},
                    "material": {"status": "provided", "value": "PLA"},
                },
            }
        ],
    }
    return await _definition(
        h,
        item_type="parametric_sketch",
        wp_type=WorkProductType.PARAMETRIC_SKETCH,
        name="Parametric sketch: Plate",
        key="PSK-PLATE",
        metadata={"parametric_sketch": sketch, "approved": True},
    )


async def test_a_length_dimension_commit_takes_the_solved_geometry(h: Harness) -> None:
    await _parametric(h, depth=5)  # the plane
    await _box_sketch(h)
    d = await preview_definition(h.twin, h.service, project_id=PROJECT, sketch_key="PSK-PLATE")
    lengths = {x["constraint_id"]: x for x in d["dimensions"] if x["type"] == "length"}
    assert set(lengths) == {"L1", "L2"}
    # On the plane, v up: the top line runs along v = 0.
    assert lengths["L1"]["start"] == [0.0, -0.0] and lengths["L1"]["end"] == [40.0, -0.0]
    assert {h_["kind"] for h_ in d["handles"]} == {"extrude_depth", "length"}
    solver = d["solver_sketch"]
    assert len(solver["elements"]) == 4 and len(solver["constraints"]) == 10

    # The browser solved the top to 50: the right side moved with it.
    solved = {
        "top": {"start": [0.0, 0.0], "end": [50.0, 0.0]},
        "right": {"start": [50.0, 0.0], "end": [50.0, 20.0]},
        "bottom": {"start": [50.0, 20.0], "end": [0.0, 20.0]},
    }
    out = await commit_parameters(
        h.twin,
        h.service,
        project_id=PROJECT,
        sketch_key="PSK-PLATE",
        request=ParameterCommitRequest(
            base_revision=1, dimensions={"L1": 50.0}, geometry=solved, source="drag", build=False
        ),
    )
    assert out["sketch_ref"] == "PSK-PLATE@2"
    psk = await find_item(h.twin, "PSK-PLATE", PROJECT)
    node = await h.node(str(psk.head_node_id))
    content = node.metadata["parametric_sketch"]
    top = next(e for e in content["elements"] if e["id"] == "top")
    assert top["end"] == [50.0, 0.0]
    l1 = next(c for c in content["constraints"] if c["id"] == "L1")
    assert l1["value"] == 50.0 and l1["source"] == "human_confirmed"
    assert node.metadata["parameter_edit"]["solved_elements"] == ["bottom", "right", "top"]

    # A solve that broke a rule (the right side no longer vertical) is refused.
    broken = {**solved, "top": {"start": [0.0, 0.0], "end": [60.0, 0.0]}}
    with pytest.raises(ParametricPreviewError) as caught:
        await commit_parameters(
            h.twin,
            h.service,
            project_id=PROJECT,
            sketch_key="PSK-PLATE",
            request=ParameterCommitRequest(
                base_revision=2, dimensions={"L1": 60.0}, geometry=broken, build=False
            ),
        )
    assert caught.value.status == 409 and "breaks accepted rules" in str(caught.value)
    # A length without its solved geometry, and geometry without a length, are refused.
    for request in (
        ParameterCommitRequest(base_revision=2, dimensions={"L1": 45.0}, build=False),
        ParameterCommitRequest(base_revision=2, geometry=solved, values={"depth": 4}),
        ParameterCommitRequest(
            base_revision=2, dimensions={"L1": 45.0}, geometry={"ghost": {"start": [0, 0]}}
        ),
    ):
        with pytest.raises(ParametricPreviewError) as caught:
            await commit_parameters(
                h.twin, h.service, project_id=PROJECT, sketch_key="PSK-PLATE", request=request
            )
        assert caught.value.status == 422


async def test_the_preview_definition_matches_the_exact_build(h: Harness) -> None:
    await _parametric(h)  # depth not given yet
    d = await preview_definition(h.twin, h.service, project_id=PROJECT, sketch_key="PSK-SLOT")
    assert d["parameters"]["depth"]["assumed"] is True and d["depth"] > 0
    assert d["buildable"] is False and d["definition_hash"] is None
    assert "depth" in d["missing"] and "material" in d["missing"]
    assert d["representation"]["level"] == "L2" and d["representation"]["authority"] == "preview"
    assert d["cad"]["exists"] is False

    await _parametric_depth(h, 5)
    await _build(h)
    d = await preview_definition(h.twin, h.service, project_id=PROJECT, sketch_key="PSK-SLOT")
    assert d["parameters"]["depth"]["assumed"] is False and d["depth"] == 5.0
    assert d["cad"]["geometry_state"] == "validated"
    # The browser's definition hash is the one the exact build stored: no material in the IR.
    d_with_material = dict(d)
    assert d_with_material["missing"] == ["material"]
    head = await h.node(d["cad"]["node_id"])
    stored = head.metadata["exact_source"]["definition_hash"]
    entities = head.metadata["exact_source"]["design_ir"]["entities"]
    assert definition_from_entities(entities)["elements"] == d["elements"]
    assert d["cad"]["comparison"]["outcome"] == "consistent"
    assert d["cad"]["last_validated"]["item_ref"] == "CAD-SLOT@1"
    assert stored == definition_hash(entities)


async def test_routes_map_the_preview_service(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from api_gateway.twin import routes as twin_routes
    from api_gateway.twin.exact_cad import set_exact_cad_service
    from api_gateway.twin.parametric_preview_routes import router

    monkeypatch.setattr(twin_routes, "get_twin", lambda: h.twin)
    set_exact_cad_service(h.service)
    app = FastAPI()
    app.include_router(router)
    await _parametric(h, depth=5)

    async def call(method: str, url: str, **kw: Any) -> Any:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.request(method, url, **kw)

    base = f"/v1/twin/projects/{PROJECT}/parametric-sketches/PSK-SLOT"
    r = await call("GET", f"{base}/preview-definition")
    assert r.status_code == 200 and r.json()["sketch_ref"] == "PSK-SLOT@1"
    r = await call(
        "POST",
        f"{base}/parameters",
        json={"base_revision": 1, "values": {"depth": 4, "material": "PLA"}},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["build"]["job_id"]
    await h.runner.wait(job_id, 5.0)
    r = await call("GET", f"{base}/preview-definition", params={"job_id": job_id})
    body = r.json()
    assert body["build"]["status"] == "succeeded"
    assert body["cad"]["matches_definition"] is True
    r = await call("POST", f"{base}/parameters", json={"base_revision": 1, "values": {"depth": 3}})
    assert r.status_code == 409
    r = await call(
        "GET", f"/v1/twin/projects/{PROJECT}/parametric-sketches/PSK-NONE/preview-definition"
    )
    assert r.status_code == 404


def test_the_client_preview_is_l2_and_never_exact() -> None:
    d = preview_descriptor(source_revision="PSK-SLOT@2", component_ids=["CMP-1"])
    assert (d["level"], d["authority"], d["label"]) == ("L2", "preview", "Preview")
    snapshot = representation_of("render", {"generated_by": "dashboard.parametric_preview"})
    assert snapshot is not None and snapshot["level"] == "L2" and snapshot["authority"] == "preview"
    claimed = representation_of(
        "render",
        {
            "generated_by": "dashboard.parametric_preview",
            "representation": {"level": "L3", "authority": "validated"},
        },
    )
    assert claimed is not None and claimed["level"] != "L3"
