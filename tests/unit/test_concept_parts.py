"""Concept parts: split the L0 concept, map its parts, redo or seal one part.

Every provider here is a fake: nothing calls Tripo. The GLBs are built in the
test, one node per part (or one mesh with several primitives, to check that a
model returned whole is reported ``not_segmented`` and never guessed into
parts).
"""

from __future__ import annotations

import json
import struct
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from api_gateway.twin.concept_parts import (
    COMPLETE_TOOL,
    REGENERATE_TOOL,
    SEGMENT_TOOL,
    ConceptPartsError,
    ConceptPartsService,
    confirmed_parts,
    part_extents_mm,
    part_label_ok,
    propose_mapping,
)
from api_gateway.twin.glb_bounds import glb_bounds
from api_gateway.twin.glb_parts import (
    REGENERATED_KEY,
    node_face_samples,
    node_mesh_bytes,
    read_parts,
    split_glb,
    subset_glb,
)
from api_gateway.twin.mesh_generation import (
    SEGMENT_MODEL_SEMANTIC,
    GeneratedMesh,
    TripoMeshGenerator,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

pytestmark = pytest.mark.asyncio

PROJECT = "31111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# GLB fixtures
# ---------------------------------------------------------------------------


def _box(centre: tuple[float, float, float], size: tuple[float, float, float]) -> bytes:
    cx, cy, cz = centre
    hx, hy, hz = (s / 2 for s in size)
    corners = [
        (cx + sx * hx, cy + sy * hy, cz + sz * hz)
        for sx in (-1, 1)
        for sy in (-1, 1)
        for sz in (-1, 1)
    ]
    return struct.pack(f"<{len(corners) * 3}f", *[v for c in corners for v in c])


_BOX_TRIS = [
    0,
    1,
    3,
    0,
    3,
    2,
    4,
    6,
    7,
    4,
    7,
    5,
    0,
    4,
    5,
    0,
    5,
    1,
    2,
    3,
    7,
    2,
    7,
    6,
    0,
    2,
    6,
    0,
    6,
    4,
    1,
    5,
    7,
    1,
    7,
    3,
]


def make_glb(parts: list[dict[str, Any]], *, one_mesh: bool = False) -> bytes:
    """One node per part (``name``, ``centre``, ``size``, optional ``extras``).

    With ``one_mesh`` every part is a primitive of a single mesh on one node.
    """
    doc: dict[str, Any] = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [{"byteLength": 0}],
    }
    binary = b""
    prims = []
    for part in parts:
        pos = _box(part["centre"], part["size"])
        idx = struct.pack(f"<{len(_BOX_TRIS)}H", *_BOX_TRIS)
        idx += b"\x00" * (-len(idx) % 4)
        vals = struct.unpack(f"<{24}f", pos)
        lo = [min(vals[i::3]) for i in range(3)]
        hi = [max(vals[i::3]) for i in range(3)]
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(pos)})
        binary += pos
        doc["accessors"].append(
            {
                "bufferView": len(doc["bufferViews"]) - 1,
                "componentType": 5126,
                "count": 8,
                "type": "VEC3",
                "min": lo,
                "max": hi,
            }
        )
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": 72})
        binary += idx
        doc["accessors"].append(
            {
                "bufferView": len(doc["bufferViews"]) - 1,
                "componentType": 5123,
                "count": len(_BOX_TRIS),
                "type": "SCALAR",
            }
        )
        prim = {
            "attributes": {"POSITION": len(doc["accessors"]) - 2},
            "indices": len(doc["accessors"]) - 1,
        }
        if one_mesh:
            prims.append(prim)
            continue
        mesh: dict[str, Any] = {"name": part["name"], "primitives": [prim]}
        doc["meshes"].append(mesh)
        node: dict[str, Any] = {"name": part["name"], "mesh": len(doc["meshes"]) - 1}
        if part.get("extras"):
            node["extras"] = part["extras"]
        doc["nodes"].append(node)
        doc["scenes"][0]["nodes"].append(len(doc["nodes"]) - 1)
    if one_mesh:
        doc["meshes"].append({"primitives": prims})
        doc["nodes"].append({"name": "Mesh", "mesh": 0})
        doc["scenes"][0]["nodes"].append(0)
    doc["buffers"][0]["byteLength"] = len(binary)
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    body = (
        struct.pack("<II", len(raw), 0x4E4F534A)
        + raw
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


#: A lamp in model units: a base, an arm and a shade, as Tripo would name them.
LAMP_PARTS = [
    {"name": "tripo_part_0", "centre": (0.0, 0.05, 0.0), "size": (0.5, 0.1, 0.5)},
    {"name": "tripo_part_1", "centre": (0.0, 0.45, 0.0), "size": (0.05, 0.7, 0.05)},
    {"name": "tripo_part_2", "centre": (0.0, 0.9, 0.0), "size": (0.4, 0.2, 0.4)},
]
#: The L1 block layout in mm (x width, y depth, z up), the same lamp.
LAMP_LAYOUT = [
    {"name": "Base", "position_mm": [0, 0, 0], "size_mm": [200, 200, 40], "shape": "box"},
    {"name": "Arm", "position_mm": [90, 90, 40], "size_mm": [20, 20, 280], "shape": "cylinder"},
    {"name": "Shade", "position_mm": [20, 20, 320], "size_mm": [160, 160, 80], "shape": "box"},
]
SEGMENTED = make_glb(LAMP_PARTS)
WHOLE = make_glb(LAMP_PARTS, one_mesh=True)
NEW_PART = make_glb([{"name": "shade_v2", "centre": (3.0, 3.0, 3.0), "size": (2.0, 1.0, 2.0)}])


class FakeTripo:
    """A Tripo stand-in recording each call; never a paid request."""

    provider = "tripo"

    def __init__(self, segmented: bytes = SEGMENTED, completed: bytes | None = None) -> None:
        self.segmented = segmented
        self.completed = completed
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self,
        image: bytes | None,
        mime: str,
        prompt: str | None,
        *,
        views: Any = None,
        options: dict[str, Any] | None = None,
    ) -> GeneratedMesh:
        self.calls.append(("generate", {"image": image, "prompt": prompt, "options": options}))
        return GeneratedMesh(
            glb_bytes=NEW_PART,
            provider="tripo",
            model_version="v-test",
            task_id="task-part",
            mode="image_to_model" if image else "text_to_model",
            options=dict(options or {}),
        )

    async def segment(self, **kwargs: Any) -> GeneratedMesh:
        self.calls.append(("segment", kwargs))
        return GeneratedMesh(
            glb_bytes=self.segmented,
            provider="tripo",
            model_version=kwargs["model"],
            task_id="task-seg",
            mode="mesh_segment",
            options={"model": kwargs["model"]},
            credits=40.0,
            output={"rendered_image_url": "https://cdn.example/seg.png"},
        )

    async def complete(self, **kwargs: Any) -> GeneratedMesh:
        self.calls.append(("complete", kwargs))
        return GeneratedMesh(
            glb_bytes=self.completed or self.segmented,
            provider="tripo",
            model_version="v-test",
            task_id="task-complete",
            mode="mesh_complete",
            options=dict(kwargs),
            credits=100.0,
        )


class World:
    def __init__(self, tripo: FakeTripo | None = None) -> None:
        self.twin = InMemoryTwinAPI.create()
        self.blobs: dict[str, bytes] = {}
        self.tripo = tripo or FakeTripo()
        self.service = ConceptPartsService(
            self.twin, None, generator=self.tripo, blob_reader=self.read, blob_writer=self.write
        )

    def write(self, node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        key = f"work-products/{node_id}/{filename}"
        self.blobs[key] = content
        return key

    def read(self, wp: Any) -> tuple[bytes, str]:
        return self.blobs[(wp.metadata or {})["minio_object_key"]], str(wp.name)

    async def _wp(
        self, wp_type: WorkProductType, name: str, metadata: dict[str, Any], fmt: str = ""
    ) -> str:
        wp_id = uuid4()
        now = datetime.now(UTC)
        await self.twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=name,
                type=wp_type,
                domain="mechanical",
                file_path="",
                content_hash="c" * 64,
                format=fmt,
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by="test",
                project_id=UUID(PROJECT),
            )
        )
        return str(wp_id)

    async def lamp(
        self,
        *,
        layout: list[dict[str, Any]] | None = LAMP_LAYOUT,
        names: tuple[str, ...] = ("Base", "Arm", "Shade"),
    ) -> str:
        """An accepted concept assembly, its block layout and an original concept mesh."""
        await self._wp(
            WorkProductType.DESIGN_DECISION,
            "Concept assembly",
            {"assembly": {"parts": [{"name": n, "source": "made"} for n in names]}},
        )
        if layout is not None:
            await self._wp(
                WorkProductType.DESIGN_SKETCH,
                "Lamp layout",
                {"concept_layout": {"concept_name": "Lamp", "parts": layout}},
            )
        key = "work-products/original/concept.glb"
        self.blobs[key] = make_glb([{"name": "Mesh", "centre": (0, 0.5, 0), "size": (0.5, 1, 0.5)}])
        return await self._wp(
            WorkProductType.RENDER,
            "Reference model: Lamp",
            {
                "generated_by": "twin.generate_concept_mesh",
                "provider": "tripo",
                "task_id": "task-original",
                "mode": "image_to_model",
                "authority": "illustrative",
                "illustrative_label": "AI 3D reference model, illustrative only",
                "envelope_mm": [200.0, 200.0, 400.0],
                "fit_to_envelope": True,
                "concept_name": "Lamp",
                "minio_object_key": key,
            },
            fmt="glb",
        )

    async def node(self, node_id: str) -> Any:
        return await self.twin.get_work_product(UUID(node_id))

    async def glb(self, node_id: str) -> bytes:
        return self.read(await self.node(node_id))[0]


@pytest.fixture
def world() -> World:
    return World()


async def _segmented_and_mapped(world: World) -> tuple[str, dict[str, str]]:
    original = await world.lamp()
    seg = await world.service.segment(node_id=original)
    comps = {
        c["name"]: c["component_id"] for c in (await world.service.state(PROJECT))["components"]
    }
    await world.service.confirm_mapping(
        node_id=seg["node_id"], assignments={}, accept_proposals=True, actor="person:a"
    )
    return seg["node_id"], comps


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


async def test_parser_reads_one_part_per_node() -> None:
    reading = read_parts(SEGMENTED)
    assert reading.status == "segmented"
    assert [p.name for p in reading.parts] == ["tripo_part_0", "tripo_part_1", "tripo_part_2"]
    # Generic names are not labels.
    assert all(p.label is None for p in reading.parts)
    assert reading.labelled is False
    assert reading.parts[1].size == pytest.approx((0.05, 0.7, 0.05), abs=1e-6)
    assert reading.parts[0].triangle_count == 12


async def test_parser_reads_semantic_labels_from_extras_and_names() -> None:
    glb = make_glb(
        [
            {**LAMP_PARTS[0], "extras": {"label": "base"}},
            {**LAMP_PARTS[1], "name": "lamp arm"},
            {**LAMP_PARTS[2], "extras": {"semantic_label": "Shade"}},
        ]
    )
    reading = read_parts(glb)
    assert [(p.label, p.label_source) for p in reading.parts] == [
        ("base", "node.extras.label"),
        ("lamp arm", "node_name"),
        ("Shade", "node.extras.semantic_label"),
    ]
    assert reading.labelled is True


async def test_parser_reports_a_single_mesh_as_not_segmented() -> None:
    reading = read_parts(WHOLE)
    assert reading.status == "not_segmented"
    assert len(reading.parts) == 1
    assert "3 primitives" in reading.note and "not returned as separate nodes" in reading.note


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


async def test_segmentation_uses_the_task_id_and_records_provenance(world: World) -> None:
    original = await world.lamp()
    out = await world.service.segment(node_id=original)
    kind, call = world.tripo.calls[0]
    assert kind == "segment" and call["task_id"] == "task-original" and call["glb"] is None
    node = await world.node(out["node_id"])
    meta = node.metadata
    seg = meta["segmentation"]
    assert seg["task_id"] == "task-seg" and seg["model_version"] == "v1.0-20250506"
    assert seg["credits"] == 40.0 and seg["input_kind"] == "task_id"
    assert seg["status"] == "segmented" and seg["part_count"] == 3
    assert seg["rendered_image_url"] == "https://cdn.example/seg.png"
    # A new revision: still L0, still illustrative, with lineage.
    assert meta["generated_by"] == SEGMENT_TOOL
    assert meta["authority"] == "illustrative" and part_label_ok(meta["illustrative_label"])
    assert meta["representation"]["level"] == "L0"
    assert meta["representation"]["authority"] == "illustrative"
    assert meta["concept_revision"] == 2 and meta["concept_status"] == "proposed"
    assert meta["concept_lineage"]["operation"] == "segmented"
    assert meta["concept_lineage"]["previous_node_id"] == original
    edges = await world.twin.get_edges(node.id, direction="outgoing")
    assert any(
        e.edge_type == EdgeType.GENERATED_FROM and str(e.target_id) == original for e in edges
    )
    # Fitted to the source's envelope: about 400 mm tall.
    assert glb_bounds(await world.glb(out["node_id"])).size[1] == pytest.approx(0.4, rel=0.25)


async def test_segmentation_uploads_the_glb_when_there_is_no_task(world: World) -> None:
    original = await world.lamp()
    first = await world.service.segment(node_id=original)
    await world.service.decide(node_id=first["node_id"], decision="accept", actor="person:a")
    await world.service.segment(
        node_id=first["node_id"], model=SEGMENT_MODEL_SEMANTIC, granularity="detailed"
    )
    _kind, call = world.tripo.calls[-1]
    assert call["task_id"] is None and call["glb"] is not None and call["glb"][:4] == b"glTF"
    assert call["model"] == SEGMENT_MODEL_SEMANTIC and call["granularity"] == "detailed"


async def test_segmentation_refuses_bad_options_and_unaccepted_sources(world: World) -> None:
    original = await world.lamp()
    with pytest.raises(ConceptPartsError, match="semantic"):
        await world.service.segment(node_id=original, granularity="balanced")
    with pytest.raises(ConceptPartsError):
        await world.service.segment(node_id=original, model="v9")
    seg = await world.service.segment(node_id=original)
    with pytest.raises(ConceptPartsError, match="proposed"):
        await world.service.segment(node_id=seg["node_id"])


async def test_a_model_returned_whole_is_stored_as_not_segmented(world: World) -> None:
    world.tripo.segmented = WHOLE
    original = await world.lamp()
    out = await world.service.segment(node_id=original)
    meta = (await world.node(out["node_id"])).metadata
    assert meta["segmentation_status"] == "not_segmented"
    assert meta["part_mapping"]["status"] == "not_segmented" and meta["part_mapping"]["parts"] == []
    with pytest.raises(ConceptPartsError, match="whole"):
        await world.service.confirm_mapping(
            node_id=out["node_id"], assignments={}, actor="person:a"
        )


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


async def test_mapping_proposes_by_geometry_with_confidence(world: World) -> None:
    original = await world.lamp()
    out = await world.service.segment(node_id=original)
    mapping = out["mapping"]
    comps = {
        c["name"]: c["component_id"] for c in (await world.service.state(PROJECT))["components"]
    }
    proposed = {p["part"]: p["proposed_component_id"] for p in mapping["parts"]}
    assert proposed == {
        "tripo_part_0": comps["Base"],
        "tripo_part_1": comps["Arm"],
        "tripo_part_2": comps["Shade"],
    }
    assert all(p["confidence"] >= 0.6 and "geometry" in p["basis"] for p in mapping["parts"])
    assert mapping["questions"] == []


async def test_mirror_symmetric_parts_are_asked_not_guessed() -> None:
    parts = [
        {"name": "tripo_part_0", "bbox_min": [-0.5, 0, -0.1], "bbox_max": [-0.4, 0.5, 0.1]},
        {"name": "tripo_part_1", "bbox_min": [0.4, 0, -0.1], "bbox_max": [0.5, 0.5, 0.1]},
        {"name": "tripo_part_2", "bbox_min": [-0.5, 0.5, -0.1], "bbox_max": [0.5, 0.6, 0.1]},
    ]
    comps = [
        {"component_id": "CMP-leg-left", "name": "Leg left"},
        {"component_id": "CMP-leg-right", "name": "Leg right"},
        {"component_id": "CMP-top", "name": "Top"},
    ]
    blocks = {
        "CMP-leg-left": {"position_mm": [0, 80, 0], "size_mm": [100, 40, 500]},
        "CMP-leg-right": {"position_mm": [900, 80, 0], "size_mm": [100, 40, 500]},
        "CMP-top": {"position_mm": [0, 80, 500], "size_mm": [1000, 40, 100]},
    }
    mapping = propose_mapping(parts, comps, blocks)
    by_part = {p["part"]: p for p in mapping["parts"]}
    assert by_part["tripo_part_2"]["proposed_component_id"] == "CMP-top"
    for leg in ("tripo_part_0", "tripo_part_1"):
        assert by_part[leg]["state"] == "question" and by_part[leg]["proposed_component_id"] is None
    asked = {q["part"] for q in mapping["questions"]}
    assert asked == {"tripo_part_0", "tripo_part_1"}
    # Labels settle what geometry cannot.
    labelled = [dict(parts[0], label="leg left"), dict(parts[1], label="leg right"), parts[2]]
    settled = {
        p["part"]: p["proposed_component_id"]
        for p in propose_mapping(labelled, comps, blocks)["parts"]
    }
    assert settled["tripo_part_0"] == "CMP-leg-left" and settled["tripo_part_1"] == "CMP-leg-right"


async def test_without_a_layout_or_labels_every_part_is_a_question() -> None:
    parts = [
        {"name": f"tripo_part_{i}", "bbox_min": [i, 0, 0], "bbox_max": [i + 1, 1, 1]}
        for i in range(2)
    ]
    comps = [{"component_id": "CMP-a", "name": "A"}, {"component_id": "CMP-b", "name": "B"}]
    mapping = propose_mapping(parts, comps, None)
    assert all(p["state"] == "question" for p in mapping["parts"])
    assert len(mapping["questions"]) == 2


async def test_component_ids_are_written_only_after_confirmation(world: World) -> None:
    original = await world.lamp()
    out = await world.service.segment(node_id=original)
    before = split_glb(await world.glb(out["node_id"]))[0]
    assert not any("component_id" in (n.get("extras") or {}) for n in before["nodes"])

    comps = {
        c["name"]: c["component_id"] for c in (await world.service.state(PROJECT))["components"]
    }
    # Correct one part, leave the rest unconfirmed.
    view = await world.service.confirm_mapping(
        node_id=out["node_id"], assignments={"tripo_part_2": comps["Shade"]}, actor="person:a"
    )
    assert view["mapping"]["status"] == "partially_confirmed"
    doc = split_glb(await world.glb(out["node_id"]))[0]
    tagged = {
        n["name"]: n.get("extras", {}).get("component_id") for n in doc["nodes"] if "mesh" in n
    }
    assert tagged == {"tripo_part_0": None, "tripo_part_1": None, "tripo_part_2": comps["Shade"]}
    assert (await world.node(out["node_id"])).metadata["representation"]["component_ids"] == [
        comps["Shade"]
    ]

    # Accepting the rest writes the rest, and a wrong component id is refused.
    with pytest.raises(ConceptPartsError):
        await world.service.confirm_mapping(
            node_id=out["node_id"], assignments={"tripo_part_0": "CMP-nope"}, actor="a"
        )
    view = await world.service.confirm_mapping(
        node_id=out["node_id"], assignments={}, accept_proposals=True, actor="person:a"
    )
    assert view["mapping"]["status"] == "confirmed"
    assert confirmed_parts(view["mapping"]) == {
        "tripo_part_0": comps["Base"],
        "tripo_part_1": comps["Arm"],
        "tripo_part_2": comps["Shade"],
    }


async def test_a_question_is_never_confirmed_by_accepting_proposals(world: World) -> None:
    original = await world.lamp(layout=None)
    out = await world.service.segment(node_id=original)
    assert all(p["state"] == "question" for p in out["mapping"]["parts"])
    view = await world.service.confirm_mapping(
        node_id=out["node_id"], assignments={}, accept_proposals=True, actor="a"
    )
    assert confirmed_parts(view["mapping"]) == {}
    assert len(view["mapping"]["questions"]) == 3


# ---------------------------------------------------------------------------
# Part regeneration
# ---------------------------------------------------------------------------


async def test_regenerating_a_part_swaps_only_that_node(world: World) -> None:
    seg_id, comps = await _segmented_and_mapped(world)
    before = await world.glb(seg_id)
    out = await world.service.regenerate_part(
        node_id=seg_id, component_id=comps["Shade"], source="text", prompt="a conical shade"
    )
    after = await world.glb(out["node_id"])
    reading = read_parts(before)
    shade = next(p for p in reading.parts if p.name == "tripo_part_2")
    for part in reading.parts:
        if part.name != "tripo_part_2":
            assert node_mesh_bytes(after, part.node_index) == node_mesh_bytes(
                before, part.node_index
            )
            assert node_mesh_bytes(after, part.node_index)
    # The JSON for the other nodes is unchanged too, and their binary bytes keep their offsets.
    bdoc, bbin, _ = split_glb(before)
    adoc, abin, _ = split_glb(after)
    assert abin[: len(bbin)] == bbin
    for i, node in enumerate(bdoc["nodes"]):
        if i != shade.node_index:
            assert adoc["nodes"][i] == node
    # The swapped node keeps its id and pose, and the new part fills its box.
    swapped = adoc["nodes"][shade.node_index]
    assert swapped["extras"]["component_id"] == comps["Shade"] and "mesh" not in swapped
    assert REGENERATED_KEY in swapped["extras"]
    new = next(p for p in read_parts(after).parts if p.name == "tripo_part_2")
    # The new part (2 x 1 x 2) is fitted uniformly to the node's own box (0.4 x 0.2 x 0.4
    # before the stored copy's envelope scale): the scale is the log-least-squares one,
    # it is centred on the old box, and the envelope scale above it still applies.
    local = LAMP_PARTS[2]["size"]
    envelope_scale = shade.size[1] / local[1]
    assert new.centre == pytest.approx(shade.centre, abs=1e-6)
    factor = out["fit"]["scale_factor"]
    expected = (local[0] * local[1] * local[2] / (2.0 * 1.0 * 2.0)) ** (1 / 3)
    assert factor == pytest.approx(expected, rel=1e-6)
    assert new.size == pytest.approx(
        tuple(v * factor * envelope_scale for v in (2.0, 1.0, 2.0)), rel=1e-6
    )
    assert out["fit"]["other_nodes_unchanged"] == 2


async def test_regeneration_records_lineage_and_keeps_the_label_illustrative(world: World) -> None:
    seg_id, comps = await _segmented_and_mapped(world)
    out = await world.service.regenerate_part(
        node_id=seg_id, component_id=comps["Arm"], source="text", prompt="a thinner arm"
    )
    meta = (await world.node(out["node_id"])).metadata
    assert meta["generated_by"] == REGENERATE_TOOL
    assert meta["concept_lineage"] == {
        "operation": "regenerated_part",
        "previous_node_id": seg_id,
        "previous_revision": 2,
        "root_node_id": meta["concept_root_node_id"],
        "component_id": comps["Arm"],
        "part": "tripo_part_1",
    }
    assert meta["concept_revision"] == 3 and meta["concept_status"] == "proposed"
    assert meta["authority"] == "illustrative" and part_label_ok(meta["illustrative_label"])
    assert meta["regenerated_parts"][0]["prompt"] == "a thinner arm"
    assert "one part only: Arm" in world.tripo.calls[-1][1]["prompt"]
    # The mapping travels with the revision, so the part is still selectable.
    assert confirmed_parts(meta["part_mapping"])["tripo_part_1"] == comps["Arm"]


async def test_regeneration_from_an_image_and_refusals(world: World) -> None:
    import base64
    import io

    from PIL import Image

    seg_id, comps = await _segmented_and_mapped(world)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="PNG")
    out = await world.service.regenerate_part(
        node_id=seg_id,
        component_id=comps["Base"],
        source="image",
        image_base64=base64.b64encode(buf.getvalue()).decode(),
        image_mime="image/png",
    )
    assert world.tripo.calls[-1][1]["image"] == buf.getvalue()
    assert out["regenerated_parts"][0]["source"] == "image"
    with pytest.raises(ConceptPartsError, match="prompt"):
        await world.service.regenerate_part(
            node_id=seg_id, component_id=comps["Base"], source="text"
        )
    with pytest.raises(ConceptPartsError, match="confirm"):
        await world.service.regenerate_part(
            node_id=seg_id, component_id="CMP-unknown", source="text", prompt="x"
        )


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


async def test_completion_names_the_part_and_swaps_it(world: World) -> None:
    completed = make_glb(
        [
            {"name": "tripo_part_0", "centre": (0.0, 0.05, 0.0), "size": (0.5, 0.1, 0.5)},
            {"name": "tripo_part_2", "centre": (0.0, 0.9, 0.0), "size": (0.42, 0.22, 0.42)},
        ]
    )
    world.tripo.completed = completed
    seg_id, comps = await _segmented_and_mapped(world)
    before = await world.glb(seg_id)
    out = await world.service.complete_part(
        node_id=seg_id, component_id=comps["Shade"], completion_mode="quick_cap"
    )
    kind, call = world.tripo.calls[-1]
    assert kind == "complete"
    assert call == {
        "segment_task_id": "task-seg",
        "part_names": ["tripo_part_2"],
        "completion_mode": "quick_cap",
    }
    after = await world.glb(out["node_id"])
    for part in read_parts(before).parts:
        if part.name != "tripo_part_2":
            assert node_mesh_bytes(after, part.node_index) == node_mesh_bytes(
                before, part.node_index
            )
    meta = (await world.node(out["node_id"])).metadata
    assert (
        meta["generated_by"] == COMPLETE_TOOL
        and meta["concept_lineage"]["completion_mode"] == "quick_cap"
    )
    assert meta["completed_parts"][0]["credits"] == 100.0
    # A part redone after the split is no longer in Tripo's segmentation.
    redo = await world.service.regenerate_part(
        node_id=seg_id, component_id=comps["Arm"], source="text", prompt="x"
    )
    with pytest.raises(ConceptPartsError, match="redone"):
        await world.service.complete_part(node_id=redo["node_id"], component_id=comps["Arm"])


async def test_subset_keeps_only_one_node() -> None:
    glb = subset_glb(SEGMENTED, 1)
    reading = read_parts(glb)
    assert reading.status == "not_segmented" and reading.parts[0].name == "tripo_part_1"
    assert node_mesh_bytes(glb, 0) == node_mesh_bytes(SEGMENTED, 1)


# ---------------------------------------------------------------------------
# Accepting, and uses after confirmation
# ---------------------------------------------------------------------------


async def test_the_accepted_concept_stays_until_a_proposal_is_accepted(world: World) -> None:
    original = await world.lamp()
    seg = await world.service.segment(node_id=original)
    state = await world.service.state(PROJECT)
    assert state["current"]["node_id"] == original
    assert [p["node_id"] for p in state["proposals"]] == [seg["node_id"]]
    await world.service.decide(node_id=seg["node_id"], decision="accept", actor="person:a")
    state = await world.service.state(PROJECT)
    assert state["current"]["node_id"] == seg["node_id"] and state["proposals"] == []
    assert (await world.node(original)).metadata["concept_status"] == "superseded"
    with pytest.raises(ConceptPartsError):
        await world.service.decide(node_id=seg["node_id"], decision="accept", actor="a")


async def test_per_part_extents_are_estimated_and_faces_are_per_node(world: World) -> None:
    seg_id, comps = await _segmented_and_mapped(world)
    extents = part_extents_mm(await world.node(seg_id))
    assert set(extents) == set(comps.values())
    assert all(
        e["confidence"] == "estimated" and e["provenance"] == "open" for e in extents.values()
    )
    base = extents[comps["Base"]]
    assert base["size_mm"][0] == pytest.approx(base["size_mm"][1], rel=0.01)
    faces = node_face_samples(await world.glb(seg_id), 0)
    assert faces and all(abs(sum(v * v for v in f["normal"]) - 1) < 1e-6 for f in faces)


async def test_dfm_reads_confirmed_parts_of_the_accepted_concept(world: World) -> None:
    from api_gateway.twin.dfm_findings import gather_components

    seg_id, comps = await _segmented_and_mapped(world)
    await world.service.decide(node_id=seg_id, decision="accept", actor="person:a")
    gathered = {g.component_id: g for g in await gather_components(world.twin, PROJECT)}
    shade = gathered[comps["Shade"]]
    assert shade.geometry.level == "L0"
    assert shade.geometry.faces and shade.geometry.faces_confidence == "estimated"
    assert all(m.confidence == "estimated" for m in shade.geometry.measures)


async def test_reconstruction_reads_part_extents_as_estimates(world: World) -> None:
    from api_gateway.twin import reconstruction as rc

    original = await world.lamp(layout=None)
    seg = await world.service.segment(node_id=original)
    comps = {
        c["name"]: c["component_id"] for c in (await world.service.state(PROJECT))["components"]
    }
    await world.service.confirm_mapping(
        node_id=seg["node_id"],
        assignments={"tripo_part_0": comps["Base"], "tripo_part_2": comps["Shade"]},
        actor="person:a",
    )
    await world.service.decide(node_id=seg["node_id"], decision="accept", actor="person:a")
    out = await rc.propose(
        world.twin, PROJECT, rc.ReconstructionProposeRequest(component_ids=[comps["Base"]])
    )
    [view] = out.proposals
    proposal = view.proposal
    assert proposal["inputs"]["layout_ref"] == f"node:{seg['node_id']}"
    params = [p for f in proposal["features"] for p in f["parameters"].values()]
    numeric = [p for p in params if isinstance(p.get("value"), (int, float))]
    assert numeric and all(p["status"] == "estimated" for p in numeric)


async def test_tripo_segment_and_complete_requests() -> None:
    sent: list[tuple[str, Any]] = []
    glb = SEGMENTED

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(
            (
                request.url.path,
                json.loads(request.content)
                if request.content and request.method == "POST" and b"{" in request.content[:1]
                else None,
            )
        )
        if request.url.path.endswith("/mesh/segment") or request.url.path.endswith(
            "/mesh/complete"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-1"}})
        if request.url.path.endswith("/tasks/t-1"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": "success",
                        "output": {
                            "model_url": "https://cdn.example/m.glb",
                            "rendered_image_url": "https://cdn.example/r.png",
                        },
                        "consumed_credit": 40,
                    },
                },
            )
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=glb)
        return httpx.Response(404)

    async def no_sleep(_s: float) -> None:
        return None

    tripo = TripoMeshGenerator(api_key="k", transport=httpx.MockTransport(handler), sleep=no_sleep)
    seg = await tripo.segment(
        task_id="task-0",
        model=SEGMENT_MODEL_SEMANTIC,
        granularity="simple",
        split_by_connectivity=False,
    )
    assert sent[0][0].endswith("/mesh/segment")
    assert sent[0][1] == {
        "model": SEGMENT_MODEL_SEMANTIC,
        "input": "task-0",
        "segmentation_granularity": "simple",
        "split_by_connectivity": False,
    }
    assert seg.credits == 40.0 and seg.output["rendered_image_url"] == "https://cdn.example/r.png"
    sent.clear()
    done = await tripo.complete(
        segment_task_id="t-seg", part_names=["tripo_part_2"], completion_mode="quick_cap"
    )
    assert sent[0][1] == {
        "input": "t-seg",
        "completion_mode": "quick_cap",
        "part_names": ["tripo_part_2"],
    }
    assert done.mode == "mesh_complete" and done.glb_bytes == glb
