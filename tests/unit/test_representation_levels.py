"""Representation levels, component ids, per-component maturity and the L1 mesh (C3D slice 2).

Over ``InMemoryTwinAPI``: real items and revisions through the item service,
concept assemblies recorded through the real decision recorder, check results
pinned with the real pin helpers. The L1 GLB is read back with the repository's
own glTF reader.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from api_gateway.twin import representation_routes, structured_mesh
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.glb_bounds import glb_bounds, gltf_json
from api_gateway.twin.structured_mesh import (
    NoLayoutError,
    make_structured_mesh_recorder,
)
from tool_registry.tools.twin.adapter import TwinServer
from tool_registry.tools.twin.concept_sketch import parse_layout
from tool_registry.tools.twin.layout_mesh import ROOT_NODE_NAME, build_structured_mesh
from twin_core.api import InMemoryTwinAPI
from twin_core.component_identity import (
    issue_ids,
    match_name,
    project_identity,
    slug,
    stamp_project_cad,
    tag_core,
    tag_with_component,
)
from twin_core.consistency.record_pins import link_record, pin_metadata, resolve_pins
from twin_core.items import commit_revision, plan_revision
from twin_core.maturity import ComponentMaturity, Maturity, aggregate, compute_maturity
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct
from twin_core.representation import (
    REPRESENTATION_KEY,
    RepresentationAuthority,
    RepresentationLevel,
    descriptor,
    representation_of,
)

pytestmark = pytest.mark.asyncio

PROJECT = UUID("80380380-3803-4803-8803-803803803803")

LAYOUT: dict[str, Any] = {
    "concept_name": "Desk lamp",
    "parts": [
        {
            "name": "Base",
            "shape": "cylinder",
            "size_mm": [140, 140, 24],
            "position_mm": [0, 0, 0],
            "provenance": "intended",
        },
        {"name": "Arm", "size_mm": [16, 16, 320], "position_mm": [62, 62, 24],
         "provenance": "assumption"},
        {"name": "Shade", "shape": "cylinder", "axis": "x", "size_mm": [120, 90, 90],
         "position_mm": [40, 25, 330], "provenance": "open"},
    ],
    "connections": [{"a": "Base", "b": "Arm", "joint": "threaded boss"}],
}  # fmt: skip

ASSEMBLY: dict[str, Any] = {
    "parts": [
        {"name": "Base"},
        {"name": "Arm"},
        {"name": "Shade"},
        {"name": "Cushion"},
        {"name": "M4 screw", "source": "standard", "quantity": 4},
    ],
    "connections": [{"parts": ["Base", "Arm"], "joint": "threaded boss"}],
}


class Blobs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        key = f"work-products/{node_id}/{filename}"
        self.files[key] = content
        return key


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def default_blobs() -> Any:
    """The mesh drawn after an assembly is recorded goes to an in-memory store."""
    blobs = Blobs()
    structured_mesh.set_blob_writer(blobs.write)
    yield blobs
    structured_mesh.set_blob_writer(None)


async def _sketch(twin: Any, layout: dict[str, Any] = LAYOUT) -> UUID:
    wp = WorkProduct(
        name="Concept sketch: Desk lamp",
        type=WorkProductType.DESIGN_SKETCH,
        domain="mechanical",
        file_path="",
        content_hash=hashlib.sha256(repr(layout).encode()).hexdigest(),
        format="svg",
        metadata={"concept_layout": layout, "source_tool": "twin.render_concept_sketch"},
        created_by="twin.render_concept_sketch",
        project_id=PROJECT,
    )
    await twin.create_work_product(wp)
    return wp.id


async def _cad(
    twin: Any, name: str, meta: dict[str, Any] | None = None, *, key: str | None = None
) -> tuple[str, int, UUID]:
    plan = await plan_revision(
        twin, item_type="cad_model", name=name, project_id=PROJECT, author="test", item_key=key
    )
    assert plan is not None
    wp = WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash=hashlib.sha256(f"{name}{uuid4()}".encode()).hexdigest(),
        format="step",
        metadata={**(meta or {}), **plan.stamp()},
        created_by="test",
        project_id=PROJECT,
    )
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name=name)
    return plan.key, plan.revision, wp.id


async def _check(twin: Any, ref: str, result: dict[str, Any]) -> UUID:
    pins = await resolve_pins(twin, refs=[ref], project_id=PROJECT)
    meta = {
        "evidence_type": "test",
        "producer": {"tool": "calculix.run_fea"},
        "result": result,
        **pin_metadata(pins),
    }
    entity = EngineeringEntity(
        entity_type="evidence", statement="FEA", project_id=PROJECT, metadata=meta
    )
    created = await twin.create_engineering_entity(entity)
    await link_record(twin, created.id, pins, record_type="evidence", name="FEA", metadata=meta)
    return created.id


async def _assembly(
    twin: Any, assembly: dict[str, Any] = ASSEMBLY, title: str = "Concept A"
) -> Any:
    record = make_decision_recorder(twin)
    return await record(title=title, rationale="chosen", assembly=assembly, project_id=str(PROJECT))


# ---------------------------------------------------------------------------
# The descriptor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wp_type", "meta", "fmt", "level", "authority", "label"),
    [
        ("cad_model", {"geometry_state": "validated"}, "step", "L3", "validated", "Validated"),
        ("cad_model", {"geometry_state": "released"}, "step", "L3", "validated", "Validated"),
        ("cad_model", {}, "step", "L2", "exact", "Exact"),
        ("cad_model", {"geometry_state": "validation_pending"}, "step", "L2", "exact", "Exact"),
        ("cad_model", {"geometry_state": "failed"}, "step", "L2", "preview", "Preview"),
        ("render", {"generated_by": "twin.generate_concept_mesh"}, "glb", "L0", "illustrative",
         "Illustrative"),
        ("render", {"generated_by": "twin.render_concept_image"}, "png", "illustration_image",
         "illustrative", "Illustrative"),
        ("render", {"generated_by": "twin.render_structured_mesh"}, "glb", "L1", "structured",
         "Preview"),
        ("image", {}, "png", "illustration_image", "illustrative", "Illustrative"),
        ("design_sketch", {}, "svg", "illustration_image", "illustrative", "Illustrative"),
        ("generated_sketch", {}, "png", "illustration_image", "illustrative", "Illustrative"),
        ("sketch_strokes", {}, "json", "illustration_image", "illustrative", "Illustrative"),
    ],
)  # fmt: skip
async def test_each_type_derives_its_descriptor(
    wp_type: str, meta: dict[str, Any], fmt: str, level: str, authority: str, label: str
) -> None:
    rep = representation_of(wp_type, {**meta, "item_key": "K", "item_revision": 2}, fmt=fmt)
    assert rep is not None
    assert (rep["level"], rep["authority"], rep["label"]) == (level, authority, label)
    assert rep["derived"] is True
    assert rep["source_revision"] == "K@2"


async def test_types_that_are_not_displayable_have_none() -> None:
    assert representation_of("design_decision", {}) is None
    assert representation_of("simulation_result", {}) is None


async def test_l3_only_from_a_validated_exact_revision() -> None:
    claim = {"level": "L3", "authority": "validated"}
    # An unvalidated CAD revision claiming L3 reads L2.
    cad = representation_of("cad_model", {REPRESENTATION_KEY: claim})
    assert cad is not None and (cad["level"], cad["authority"]) == ("L2", "exact")
    assert cad["guarded"]
    # A render claiming L3 reads what it is.
    mesh = representation_of(
        "render", {REPRESENTATION_KEY: claim, "generated_by": "twin.generate_concept_mesh"}
    )
    assert mesh is not None and (mesh["level"], mesh["authority"]) == ("L0", "illustrative")
    # A validated revision may.
    ok = representation_of("cad_model", {REPRESENTATION_KEY: claim, "geometry_state": "validated"})
    assert ok is not None and ok["level"] == "L3" and not ok["guarded"]
    with pytest.raises(ValueError, match="L3"):
        descriptor(
            RepresentationLevel.L3,
            RepresentationAuthority.VALIDATED,
            source_revision=None,
            component_ids=[],
            tool="t",
        )


async def test_l0_and_l1_are_never_labelled_exact_or_validated() -> None:
    for level, ceiling in (("L0", "illustrative"), ("L1", "structured")):
        for claim in ("exact", "validated"):
            rep = representation_of(
                "render", {REPRESENTATION_KEY: {"level": level, "authority": claim}}
            )
            assert rep is not None
            assert rep["authority"] == ceiling
            assert rep["label"] not in ("Exact", "Validated")
    with pytest.raises(ValueError, match="cannot be exact"):
        descriptor(
            RepresentationLevel.L1,
            RepresentationAuthority.EXACT,
            source_revision=None,
            component_ids=[],
            tool="t",
        )  # fmt: skip


# ---------------------------------------------------------------------------
# Component ids
# ---------------------------------------------------------------------------


async def test_ids_are_issued_once_and_kept() -> None:
    assert slug("Chair Leg (front)") == "chair-leg-front"
    first = issue_ids(["Leg", "leg", "Seat"])
    assert first == {"Leg": "CMP-leg", "leg": "CMP-leg", "Seat": "CMP-seat"}
    # A clash between two names with the same slug but different keys.
    clash = issue_ids(["Leg 1", "Leg-1!"], {"Other": "CMP-leg-1"})
    assert clash["Leg 1"] == "CMP-leg-1-2"
    # Prior ids are kept, new names get new ids.
    again = issue_ids(["Seat", "Back"], first)
    assert again == {"Seat": "CMP-seat", "Back": "CMP-back"}
    assert match_name("Chair Leg 2", {"Leg": "CMP-leg", "Seat": "CMP-seat"}) == "CMP-leg"
    assert match_name("Leg and seat", {"Leg": "CMP-leg", "Seat": "CMP-seat"}) is None


async def test_a_concept_assembly_issues_ids_and_a_later_one_keeps_them(twin: Any) -> None:
    first = await _assembly(twin)
    assert first["component_ids"]["Base"] == "CMP-base"
    wp = await twin.get_work_product(UUID(first["node_id"]))
    parts = wp.metadata["assembly"]["parts"]
    assert {p["name"]: p["component_id"] for p in parts}["Shade"] == "CMP-shade"
    assert wp.metadata["component_identity"]["Arm"] == "CMP-arm"

    revised = dict(ASSEMBLY, parts=[*ASSEMBLY["parts"][:3], {"name": "Diffuser"}])
    second = await _assembly(twin, revised, title="Concept A, revised")
    assert second["component_ids"]["Base"] == "CMP-base"
    assert second["component_ids"]["Diffuser"] == "CMP-diffuser"
    ident = await project_identity(twin, PROJECT)
    assert ident["components"]["CMP-cushion"]["in_current"] is False
    assert ident["components"]["CMP-diffuser"]["in_current"] is True


async def test_cad_revisions_carry_the_component_id_and_keep_it(twin: Any) -> None:
    await _assembly(twin)
    key, _rev, node_id = await _cad(twin, "Lamp Base")
    node = await twin.graph.get_node(node_id)
    assert node.metadata["component_id"] == "CMP-base"
    # Renamed in the next revision: the id travels with the item, not the name.
    _k, rev2, node2 = await _cad(twin, "Weighted foot", key=key)
    assert rev2 == 2
    assert (await twin.graph.get_node(node2)).metadata["component_id"] == "CMP-base"


async def test_cad_built_before_the_assembly_is_stamped_when_it_is_recorded(twin: Any) -> None:
    _key, _rev, node_id = await _cad(twin, "Arm tube")
    assert "component_id" not in (await twin.graph.get_node(node_id)).metadata
    await _assembly(twin)
    assert (await twin.graph.get_node(node_id)).metadata["component_id"] == "CMP-arm"
    # Idempotent.
    assert await stamp_project_cad(twin, PROJECT) == 0


async def test_face_tags_carry_the_component_but_compare_without_it() -> None:
    tagged = tag_with_component("CMP-base", "sketch:pad:top")
    assert tagged == "CMP-base::sketch:pad:top"
    assert tag_with_component("CMP-base", tagged) == tagged
    assert tag_core(tagged) == "sketch:pad:top"
    assert tag_core("sketch:pad:top") == "sketch:pad:top"


# ---------------------------------------------------------------------------
# The L1 structured mesh
# ---------------------------------------------------------------------------


def _signed_volume(doc: dict[str, Any], glb: bytes, mesh_index: int) -> float:
    json_len = struct.unpack_from("<I", glb, 12)[0]
    bin_start = 20 + json_len + 8
    prim = doc["meshes"][mesh_index]["primitives"][0]

    def read(acc_index: int, fmt: str, width: int) -> list[tuple[Any, ...]]:
        acc = doc["accessors"][acc_index]
        view = doc["bufferViews"][acc["bufferView"]]
        out = []
        for i in range(acc["count"]):
            off = bin_start + view["byteOffset"] + i * width
            out.append(struct.unpack_from(fmt, glb, off))
        return out

    pos = read(prim["attributes"]["POSITION"], "<3f", 12)
    idx = [i[0] for i in read(prim["indices"], "<I", 4)]
    vol = 0.0
    for t in range(0, len(idx), 3):
        a, b, c = pos[idx[t]], pos[idx[t + 1]], pos[idx[t + 2]]
        vol += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6
    return vol


async def test_the_l1_generator_makes_a_valid_glb_with_named_nodes() -> None:
    layout = parse_layout(LAYOUT)
    ids = {"Base": "CMP-base", "Arm": "CMP-arm", "Shade": "CMP-shade"}
    mesh = build_structured_mesh(layout, ids)
    glb = mesh.glb
    assert glb[:4] == b"glTF"
    assert struct.unpack_from("<II", glb, 4) == (2, len(glb))
    doc = gltf_json(glb)
    by_name = {n["name"]: n for n in doc["nodes"]}
    assert set(by_name) == {"CMP-base", "CMP-arm", "CMP-shade", ROOT_NODE_NAME}
    extras = by_name["CMP-arm"]["extras"]
    assert extras["component_id"] == "CMP-arm"
    assert extras["part_name"] == "Arm"
    assert extras["dimensions"] == "estimated"
    assert extras["size_mm"] == [16.0, 16.0, 320.0]
    root = by_name[ROOT_NODE_NAME]["extras"]
    assert (root["level"], root["authority"], root["dimensions"]) == (
        "L1",
        "structured",
        "estimated",
    )
    # Every part is closed and wound outward (positive signed volume), and the
    # volume matches its block (a 24-gon cylinder is a little under pi/4).
    for i, part in enumerate(layout.parts):
        vol_mm3 = _signed_volume(doc, glb, i) * 1e9
        box = part.size[0] * part.size[1] * part.size[2]
        expected = box * (math.pi / 4 if part.shape == "cylinder" else 1.0)
        assert vol_mm3 > 0
        assert vol_mm3 == pytest.approx(expected, rel=0.02)
    # glTF +Y is the layout's height: 24 + 320 for the arm's top, in metres.
    bounds = glb_bounds(glb)
    assert bounds.max[1] == pytest.approx(0.420, abs=1e-6)
    assert bounds.approximate is False
    # Deterministic.
    assert build_structured_mesh(layout, ids).glb == glb


async def test_the_recorder_stores_l1_with_ids_and_reuses_an_unchanged_one(twin: Any) -> None:
    blobs = Blobs()
    await _assembly(twin)
    sketch_id = await _sketch(twin)
    render = make_structured_mesh_recorder(twin, blob_writer=blobs.write)
    out = await render(project_id=str(PROJECT))
    assert out["level"] == "L1" and out["reused"] is False
    assert out["component_ids"] == {"Base": "CMP-base", "Arm": "CMP-arm", "Shade": "CMP-shade"}
    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp.type == WorkProductType.RENDER and wp.format == "glb"
    assert wp.metadata["source_layout_node_id"] == str(sketch_id)
    rep = representation_of("render", wp.metadata, fmt="glb")
    assert rep is not None
    assert (rep["level"], rep["authority"], rep["derived"]) == ("L1", "structured", False)
    assert set(rep["component_ids"]) == {"CMP-base", "CMP-arm", "CMP-shade"}
    glb = blobs.files[wp.metadata["minio_object_key"]]
    names = {n["name"] for n in gltf_json(glb)["nodes"]}
    assert {"CMP-base", "CMP-arm", "CMP-shade"} <= names

    again = await render(project_id=str(PROJECT))
    assert again["reused"] is True and again["node_id"] == out["node_id"]
    assert len(blobs.files) == 1


async def test_recording_an_assembly_draws_the_l1_mesh_once(twin: Any) -> None:
    await _sketch(twin)
    first = await _assembly(twin)
    assert first["structured_mesh"]["level"] == "L1"
    renders = await twin.list_work_products(
        work_product_type=WorkProductType.RENDER, project_id=PROJECT
    )
    assert len(renders) == 1
    # The same assembly again (a deduplicated decision) and an unchanged layout:
    # no second mesh.
    await _assembly(twin, title="Concept A again")
    renders = await twin.list_work_products(
        work_product_type=WorkProductType.RENDER, project_id=PROJECT
    )
    assert len(renders) == 1


async def test_no_layout_is_a_clear_error(twin: Any) -> None:
    with pytest.raises(NoLayoutError):
        await make_structured_mesh_recorder(twin)(project_id=str(PROJECT))


async def test_the_twin_tool_draws_the_mesh(twin: Any) -> None:
    blobs = Blobs()
    server = TwinServer(
        twin=twin,
        structured_mesh_renderer=make_structured_mesh_recorder(twin, blob_writer=blobs.write),
    )
    assert "twin.render_structured_mesh" in server.tool_ids
    out = await server.render_structured_mesh({"layout": LAYOUT, "project_id": str(PROJECT)})
    assert out["level"] == "L1"
    assert out["component_ids"]["Shade"] == "CMP-shade"
    with pytest.raises(ValueError, match="give project_id"):
        await server.render_structured_mesh({})


# ---------------------------------------------------------------------------
# Maturity
# ---------------------------------------------------------------------------


async def test_a_mixed_assembly_is_not_declared_exact(twin: Any) -> None:
    """3D-RC-010: verified, parametric, structured and illustrative at once."""
    await _sketch(twin, {**LAYOUT, "parts": LAYOUT["parts"][2:], "connections": []})  # shade
    await _assembly(twin)
    base_key, base_rev, _ = await _cad(twin, "Base", {"geometry_state": "validated"})
    await _check(twin, f"{base_key}@{base_rev}", {"passed": True})
    await _cad(twin, "Arm")  # exact_ready, not validated

    result = await compute_maturity(twin, PROJECT)
    by_id = {c["component_id"]: c for c in result["components"]}
    assert by_id["CMP-base"]["maturity"] == "Verified"
    assert by_id["CMP-arm"]["maturity"] == "Parametric"
    assert by_id["CMP-shade"]["maturity"] == "Structured"
    assert by_id["CMP-cushion"]["maturity"] == "Illustrative"
    # A standard part is listed but not required.
    assert by_id["CMP-m4-screw"]["required"] is False
    product = result["product"]
    assert product["maturity"] == "Illustrative"
    assert product["claims"] == {"exact": False, "verified": False, "released": False}
    assert product["limited_by"] == ["CMP-cushion"]
    assert set(product["by_maturity"]["Verified"]) == {"CMP-base"}
    # Every claim names its evidence.
    kinds = {e["kind"] for e in by_id["CMP-base"]["evidence"]}
    assert {"concept_assembly", "cad_revision", "geometry_validity", "check"} <= kinds
    assert any(e["ref"] == f"{base_key}@{base_rev}" for e in by_id["CMP-base"]["evidence"])


async def test_a_failing_check_holds_a_component_at_exact(twin: Any) -> None:
    await _assembly(twin, {"parts": [{"name": "Base"}]})
    key, rev, _ = await _cad(twin, "Base", {"geometry_state": "validated"})
    await _check(twin, f"{key}@{rev}", {"passed": True})
    await _check(twin, f"{key}@{rev}", {"passed": False})
    result = await compute_maturity(twin, PROJECT)
    assert result["components"][0]["maturity"] == "Exact"
    assert result["product"]["maturity"] == "Exact"
    assert result["product"]["claims"]["exact"] is True


async def test_acceptance_is_never_validation(twin: Any) -> None:
    """SYNC-013: an accepted visual proposal and an approved CAD revision stay unvalidated."""
    await _assembly(twin, {"parts": [{"name": "Base"}]})
    concept = WorkProduct(
        name="Concept image",
        type=WorkProductType.RENDER,
        domain="mechanical",
        file_path="",
        content_hash="img",
        format="png",
        metadata={
            "generated_by": "twin.render_concept_image",
            "authority": "illustrative",
            "accepted": True,
            "approved": True,
            "acceptance": {"accepted_by": "person"},
        },
        created_by="test",
        project_id=PROJECT,
    )
    await twin.create_work_product(concept)
    await _cad(twin, "Base", {"accepted": True, "approved": True, "approval": "accepted"})
    result = await compute_maturity(twin, PROJECT)
    assert result["components"][0]["maturity"] == "Parametric"
    assert result["product"]["claims"]["exact"] is False
    rep = representation_of("render", concept.metadata, fmt="png")
    assert rep is not None and rep["label"] == "Illustrative"


async def test_aggregate_never_rises_above_its_least_mature_required_component() -> None:
    comps = [
        ComponentMaturity("CMP-a", "A", required=True, maturity=Maturity.RELEASED),
        ComponentMaturity("CMP-b", "B", required=True, maturity=Maturity.EXACT),
        ComponentMaturity("CMP-c", "C", required=False, maturity=Maturity.ILLUSTRATIVE),
    ]
    agg = aggregate(comps)
    assert agg["maturity"] == "Exact"
    assert agg["claims"] == {"exact": True, "verified": False, "released": False}
    assert aggregate([])["maturity"] is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_the_routes(twin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    blobs = Blobs()
    monkeypatch.setattr(twin_routes, "_twin", twin)
    representation_routes.set_recorder(make_structured_mesh_recorder(twin, blob_writer=blobs.write))
    app = FastAPI()
    app.include_router(representation_routes.router)
    app.include_router(twin_routes.router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            missing = await c.post(f"/v1/twin/projects/{PROJECT}/structured-mesh")
            assert missing.status_code == 404
            await _sketch(twin)
            await _assembly(twin)
            made = await c.post(f"/v1/twin/projects/{PROJECT}/structured-mesh", json={})
            assert made.status_code == 200, made.text
            body = made.json()
            assert body["level"] == "L1" and body["authority"] == "structured"
            assert body["reused"] is True  # the assembly already drew it

            node = (await c.get(f"/v1/twin/nodes/{body['node_id']}")).json()
            assert node["representation"]["level"] == "L1"
            assert node["representation"]["label"] == "Preview"
            assert "CMP-arm" in node["representation"]["component_ids"]

            maturity = await c.get(f"/v1/twin/projects/{PROJECT}/maturity")
            assert maturity.status_code == 200
            data = maturity.json()
            assert data["has_assembly"] is True
            assert data["product"]["maturity"] == "Illustrative"
            assert {c["component_id"]: c["maturity"] for c in data["components"]}[
                "CMP-base"
            ] == "Structured"
            bad = await c.get("/v1/twin/projects/not-a-uuid/maturity")
            assert bad.status_code == 400
    finally:
        representation_routes.set_recorder(None)
