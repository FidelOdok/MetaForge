"""A twin model goes into the studio as a see-through mesh reference.

An agent had DJI's M30 STEP in the twin and no way to show it in the studio:
a hosted studio reads no files, so a mesh reference must arrive packed.
``twin.imagine_add_mesh_reference`` reads the work product, converts a STEP
with the viewer's converter, reduces the mesh and writes the reference.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine.service import ImagineError, ImagineService
from tests.unit.imagine_fixtures import full_config
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12"

#: A 100 x 20 x 10 box, X long, Y deep, Z up: the corners and 12 triangles.
_V = [(x, y, z) for x in (0.0, 100.0) for y in (0.0, 20.0) for z in (0.0, 10.0)]
_T = [
    (0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
    (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3),
]  # fmt: skip


def _stl() -> bytes:
    lines = ["solid box"]
    for a, b, c in _T:
        lines.append(" facet normal 0 0 0\n  outer loop")
        lines += [f"   vertex {_V[i][0]} {_V[i][1]} {_V[i][2]}" for i in (a, b, c)]
        lines.append("  endloop\n endfacet")
    lines.append("endsolid box")
    return "\n".join(lines).encode()


def _glb() -> bytes:
    """The same box as a GLB in millimetres, Z up: what the OCCT converter writes."""
    pos = b"".join(struct.pack("<3f", *v) for v in _V)
    idx = b"".join(struct.pack("<3H", *t) for t in _T)
    idx += b"\x00" * (-len(idx) % 4)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(pos) + len(idx)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": 36 * 2},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
             "min": [0, 0, 0], "max": [100, 20, 10]},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }  # fmt: skip
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js
    body += struct.pack("<I4s", len(pos) + len(idx), b"BIN\x00") + pos + idx
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


async def _studio_with(tmp_path: Path, name: str, fmt: str, content: bytes) -> tuple[Any, str]:
    twin = InMemoryTwinAPI.create()
    path = tmp_path / f"{name}.{fmt}"
    path.write_bytes(content)
    wp = await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=str(path),
            content_hash="abc",
            format=fmt,
            created_by="test",
            project_id=PROJECT,
        )
    )
    svc = ImagineService(twin)
    await svc.put_studio(PROJECT, full_config(), actor="test")
    return svc, str(wp.id)


def _ref(out: Any, ref_id: str) -> dict[str, Any]:
    refs = out.config["context"]["references"]["references"]
    return next(r for r in refs if r["id"] == ref_id)


async def test_an_stl_in_the_twin_becomes_a_context_reference(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "airframe", "stl", _stl())
    out = await svc.add_mesh_reference(
        PROJECT, work_product_id=wp_id, ref_id="m30", title="DJI M30", actor="test"
    )
    assert out.revision == 2
    ref = _ref(out, "m30")
    assert ref["kind"] == "mesh" and ref["role"] == "context"
    # Studio axes are Y up: 100 wide, 10 tall, 20 deep.
    assert ref["stats"]["size"] == [100.0, 10.0, 20.0]
    assert ref["stats"]["triangles"] == 12
    assert ref["mesh"]["pos"] and ref["mesh"]["idx"]
    assert ref["source"] == f"twin work product {wp_id}"


async def test_a_step_is_converted_in_millimetres_z_up(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "airframe", "step", b"ISO-10303-21;")
    seen: list[str] = []

    def convert(content: bytes, filename: str) -> bytes:
        seen.append(filename)
        return _glb()

    real = svc.add_mesh_reference

    async def call(**kw: Any) -> Any:
        return await real(PROJECT, step_converter=convert, actor="test", **kw)

    out = await call(work_product_id=wp_id, ref_id="m30", title="M30", role="envelope")
    ref = _ref(out, "m30")
    assert seen == ["airframe.step"]
    assert (ref["units"], ref["up"], ref["role"]) == ("mm", "z", "envelope")
    assert ref["stats"]["size"] == [100.0, 10.0, 20.0]
    # The same id again replaces it rather than adding a second.
    again = await call(work_product_id=wp_id, ref_id="m30", title="M30 again")
    refs = again.config["context"]["references"]["references"]
    assert [r["title"] for r in refs if r["id"] == "m30"] == ["M30 again"]


@pytest.mark.parametrize(
    ("kwargs", "says"),
    [
        ({"ref_id": "Bad Id"}, "slug"),
        ({"role": "backdrop"}, "role must be"),
        ({"work_product_id": "not-a-uuid"}, "invalid work_product_id"),
        ({"work_product_id": "00000000-0000-0000-0000-000000000000"}, "no work product"),
    ],
)
async def test_a_bad_request_is_refused_with_the_reason(
    tmp_path: Path, kwargs: dict[str, Any], says: str
) -> None:
    svc, wp_id = await _studio_with(tmp_path, "airframe", "stl", _stl())
    args: dict[str, Any] = {"work_product_id": wp_id, "ref_id": "m30", "title": "M30"}
    args.update(kwargs)
    with pytest.raises(ImagineError, match=says):
        await svc.add_mesh_reference(PROJECT, actor="test", **args)


async def test_a_file_that_is_not_a_model_is_refused(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "datasheet", "pdf", b"%PDF-1.4")
    with pytest.raises(ImagineError, match="STEP, GLB, STL or OBJ"):
        await svc.add_mesh_reference(
            PROJECT, work_product_id=wp_id, ref_id="ds", title="Datasheet", actor="test"
        )


async def test_the_tool_returns_the_reference_without_the_mesh(tmp_path: Path) -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    svc, wp_id = await _studio_with(tmp_path, "airframe", "stl", _stl())
    srv = TwinServer(twin=svc.twin, imagine_studio=svc)
    assert "twin.imagine_add_mesh_reference" in srv.tool_ids
    got = await srv.imagine_add_mesh_reference(
        {"project_id": PROJECT, "work_product_id": wp_id, "id": "m30", "title": "M30"}
    )
    assert got["revision"] == 2
    assert got["reference"]["stats"]["size"] == [100.0, 10.0, 20.0]
    assert "mesh" not in got["reference"]
    with pytest.raises(ValueError, match="'id' is required"):
        await srv.imagine_add_mesh_reference({"project_id": PROJECT, "work_product_id": wp_id})


async def test_placement_and_colour_are_stored_with_the_origin(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "airframe", "stl", _stl())
    out = await svc.add_mesh_reference(
        PROJECT,
        work_product_id=wp_id,
        ref_id="m30",
        title="M30",
        at=[0, -120.5, 0],
        rotate=[0, 90, 0],
        color="#3A3F44",
        opacity=0.6,
        actor="test",
    )
    ref = _ref(out, "m30")
    assert ref["at"] == [0, -120.5, 0] and ref["rotate"] == [0, 90, 0]
    assert ref["color"] == "#3A3F44" and ref["opacity"] == 0.6
    # The box spans x 0..100, height 0..10, depth -20..0 in studio axes, so the point
    # the engine moves to `at` is its X/Z centre at its base.
    assert ref["origin"] == [50.0, 0.0, -10.0]


@pytest.mark.parametrize(
    ("kwargs", "says"),
    [
        ({"at": [1, 2]}, "at must be"),
        ({"rotate": [0, "90", 0]}, "rotate must be"),
        ({"color": "grey"}, "hex colour"),
        ({"opacity": 0}, "opacity"),
        ({"opacity": True}, "opacity"),
    ],
)
async def test_a_bad_placement_or_colour_is_refused(
    tmp_path: Path, kwargs: dict[str, Any], says: str
) -> None:
    svc, wp_id = await _studio_with(tmp_path, "airframe", "stl", _stl())
    with pytest.raises(ImagineError, match=says):
        await svc.add_mesh_reference(
            PROJECT, work_product_id=wp_id, ref_id="m30", title="M30", actor="test", **kwargs
        )


async def test_a_patch_moves_and_recolours_the_reference(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "airframe", "stl", _stl())
    out = await svc.add_mesh_reference(
        PROJECT, work_product_id=wp_id, ref_id="m30", title="M30", actor="test"
    )
    base = "/context/references/references/m30"
    patched = await svc.patch_studio(
        PROJECT,
        out.revision,
        [
            {"op": "add", "path": f"{base}/at", "value": [10, 0, 5]},
            {"op": "add", "path": f"{base}/color", "value": "#202428"},
        ],
        actor="test",
    )
    ref = _ref(patched, "m30")
    assert ref["at"] == [10, 0, 5] and ref["color"] == "#202428"
    with pytest.raises(ImagineError):
        await svc.patch_studio(
            PROJECT,
            patched.revision,
            [{"op": "add", "path": f"{base}/opacity", "value": 5}],
            actor="test",
        )


def _two_colour_glb() -> bytes:
    """The box as a GLB with two materials, half its faces red and half green."""
    pos = b"".join(struct.pack("<3f", *v) for v in _V)
    first = b"".join(struct.pack("<3H", *t) for t in _T[:6])
    second = b"".join(struct.pack("<3H", *t) for t in _T[6:])
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(pos) + len(first) + len(second)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(first)},
            {"buffer": 0, "byteOffset": len(pos) + len(first), "byteLength": len(second)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 18, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5123, "count": 18, "type": "SCALAR"},
        ],
        "materials": [
            {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}},
            {"pbrMetallicRoughness": {"baseColorFactor": [0, 0.2140, 0, 1]}},
        ],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0}, "indices": 2, "material": 1},
            {"attributes": {"POSITION": 0}, "indices": 1, "material": 0},
        ]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }  # fmt: skip
    bin_ = pos + first + second
    bin_ += b"\x00" * (-len(bin_) % 4)
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js
    body += struct.pack("<I4s", len(bin_), b"BIN\x00") + bin_
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


async def test_a_models_own_colours_survive_as_groups(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "board", "step", b"ISO-10303-21;")
    out = await svc.add_mesh_reference(
        PROJECT,
        step_converter=lambda content, filename: _two_colour_glb(),
        actor="test",
        work_product_id=wp_id,
        ref_id="board",
        title="Board",
    )
    groups = _ref(out, "board")["mesh"]["groups"]
    # One run per colour, ordered by colour, in sRGB (linear 0.214 green reads as #007f00).
    assert [g[0] for g in groups] == ["#007f00", "#ff0000"]
    assert [g[1:] for g in groups] == [[0, 18], [18, 18]]


def test_a_mesh_with_no_colours_has_no_groups() -> None:
    from api_gateway.imagine.formgraph import fg_refs

    tri = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert "groups" not in fg_refs.reduce_mesh([tri])
    assert fg_refs.reduce_mesh([tri], colors=["#123456"])["groups"] == [["#123456", 0, 3]]


async def test_a_board_shows_as_its_3d_preview(tmp_path: Path, monkeypatch: Any) -> None:
    from api_gateway.twin import blob_store

    twin = InMemoryTwinAPI.create()
    wp = await twin.create_work_product(
        WorkProduct(
            name="Board",
            type=WorkProductType.PCB_LAYOUT,
            domain="electronics",
            file_path="",
            content_hash="abc",
            format="kicad_pcb",
            created_by="test",
            project_id=PROJECT,
            metadata={"image_files": {"preview/fc-3d.glb": "work-products/x/preview_fc-3d.glb"}},
        )
    )
    asked: list[str] = []
    monkeypatch.setattr(
        blob_store, "fetch_work_product_blob", lambda key: asked.append(key) or _two_colour_glb()
    )
    svc = ImagineService(twin)
    await svc.put_studio(PROJECT, full_config(), actor="test")
    out = await svc.add_mesh_reference(
        PROJECT, actor="test", work_product_id=str(wp.id), ref_id="board", title="Board", units="mm"
    )
    assert asked == ["work-products/x/preview_fc-3d.glb"]
    assert len(_ref(out, "board")["mesh"]["groups"]) == 2


async def test_a_board_with_no_preview_is_refused(tmp_path: Path) -> None:
    twin = InMemoryTwinAPI.create()
    wp = await twin.create_work_product(
        WorkProduct(
            name="Board",
            type=WorkProductType.PCB_LAYOUT,
            domain="electronics",
            file_path="",
            content_hash="abc",
            format="kicad_pcb",
            created_by="test",
            project_id=PROJECT,
        )
    )
    svc = ImagineService(twin)
    await svc.put_studio(PROJECT, full_config(), actor="test")
    with pytest.raises(ImagineError, match="no 3D preview"):
        await svc.add_mesh_reference(
            PROJECT, actor="test", work_product_id=str(wp.id), ref_id="board", title="Board"
        )


def _ref_in(config: dict[str, Any], ref_id: str) -> dict[str, Any]:
    refs = config["context"]["references"]["references"]
    return next(r for r in refs if r["id"] == ref_id)


def _box_stl(length: float) -> bytes:
    """The box, ``length`` mm long in X: a different revision of the same part."""
    return _stl().replace(b"vertex 100.0", f"vertex {length}".encode())


async def _revision(twin: Any, tmp_path: Path, length: float, key: str | None = None) -> Any:
    from uuid import UUID

    from twin_core.items import commit_revision, plan_revision

    plan = await plan_revision(
        twin,
        item_type="cad_model",
        name="Case",
        project_id=UUID(PROJECT),
        author="test",
        item_key=key,
    )
    path = tmp_path / f"case-{length}.stl"
    path.write_bytes(_box_stl(length))
    wp = await twin.create_work_product(
        WorkProduct(
            name="Case",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=str(path),
            content_hash=str(length),
            format="stl",
            created_by="test",
            project_id=UUID(PROJECT),
            metadata=plan.stamp(),
        )
    )
    await commit_revision(twin, plan, wp.id, name="Case")
    return plan.key, wp


async def test_a_followed_reference_shows_the_items_current_revision(tmp_path: Path) -> None:
    """FORGE-913: the studio picture follows Develop without a rework."""
    twin = InMemoryTwinAPI.create()
    key, first = await _revision(twin, tmp_path, 100.0)
    svc = ImagineService(twin)
    await svc.put_studio(PROJECT, full_config(), actor="test")
    out = await svc.add_mesh_reference(
        PROJECT,
        actor="test",
        work_product_id=str(first.id),
        ref_id="case",
        title="Case",
        at=[5.0, 0.0, 0.0],
        follow=True,
    )
    ref = _ref(out, "case")
    assert ref["follow"] == {"item_key": key, "revision": 1, "node_id": str(first.id)}

    # Unchanged item: the page shows the packed copy as it is.
    same = await svc._live_references(PROJECT, out.config)
    assert _ref_in(same, "case")["stats"] == ref["stats"]

    # Develop makes the part longer: the page shows revision 2, in the same frame.
    await _revision(twin, tmp_path, 160.0, key=key)
    live = _ref_in(await svc._live_references(PROJECT, out.config), "case")
    assert live["stats"]["size"][0] == 160.0
    assert live["title"] == f"Case (live: {key}@2)"
    # The X centre moved by 30 mm, so `at` moves with it: the file's own frame stays put.
    assert live["at"][0] == 35.0
    # The stored studio is untouched.
    assert _ref(out, "case")["stats"]["size"][0] == 100.0


async def test_follow_needs_an_item(tmp_path: Path) -> None:
    svc, wp_id = await _studio_with(tmp_path, "loose", "stl", _stl())
    with pytest.raises(ImagineError, match="nothing to follow"):
        await svc.add_mesh_reference(
            PROJECT,
            actor="test",
            work_product_id=wp_id,
            ref_id="loose",
            title="Loose",
            follow=True,
        )
