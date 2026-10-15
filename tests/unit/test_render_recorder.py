"""twin.render_work_product: a work product rendered by Blender and kept (FORGE-630).

The OCCT converter, Blender and MinIO are all faked; what is checked is the
pipeline around them: a STEP is converted and a mesh is not, the render
lands as a ``render`` work product linked to its model, nothing falls back
quietly, and the MCP reply carries the images as image content.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from api_gateway.twin.render_recorder import (
    RenderError,
    convert_step_to_glb,
    make_work_product_renderer,
)
from metaforge.mcp.server import split_image_content
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

PNG_ISO = b"\x89PNG iso"
PNG_TOP = b"\x89PNG top"
PROJECT = "11111111-2222-3333-4444-555555555555"


class _Twin:
    def __init__(self, *wps: WorkProduct) -> None:
        self.wps = {wp.id: wp for wp in wps}
        self.created: list[WorkProduct] = []
        self.edges: list[tuple[Any, Any, Any]] = []

    async def get_work_product(self, uid: UUID) -> WorkProduct | None:
        return self.wps.get(uid)

    async def create_work_product(self, wp: WorkProduct) -> WorkProduct:
        self.created.append(wp)
        return wp

    async def add_edge(self, source: Any, target: Any, edge_type: Any) -> None:
        self.edges.append((source, target, edge_type))


class _Projects:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(self, project_id: str, node_id: str, name: str, kind: str) -> None:
        self.links.append((project_id, node_id, name, kind))


class _Blender:
    def __init__(self, views: tuple[str, ...] = ("iso",)) -> None:
        self.calls: list[tuple[str, dict[str, Any], Any]] = []
        self.views = views

    async def invoke(self, tool_id: str, params: dict[str, Any], timeout: Any = None) -> Any:
        self.calls.append((tool_id, params, timeout))
        pngs = {"iso": PNG_ISO, "top": PNG_TOP}
        return {
            "images": [
                {
                    "view": v,
                    "format": "png",
                    "width": 800,
                    "height": 600,
                    "png_base64": base64.b64encode(pngs[v]).decode(),
                }
                for v in self.views
            ],
            "samples": 32,
            "engine": "cycles-cpu",
            "blender_version": "5.2.2 LTS",
            "coloured_objects": [],
        }


def _wp(tmp_path: Path, name: str, fmt: str, content: bytes = b"model") -> WorkProduct:
    path = tmp_path / f"{name}.{fmt}"
    path.write_bytes(content)
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path=str(path),
        content_hash="abc123",
        format=fmt,
        created_by="test",
        project_id=PROJECT,
    )


def _writer(store: dict[str, bytes]) -> Any:
    def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        assert content_type == "image/png"
        key = f"work-products/{node_id}/{filename}"
        store[key] = content
        return key

    return write


async def test_a_step_is_converted_rendered_and_kept(tmp_path: Path) -> None:
    source = _wp(tmp_path, "Upper Arm Link", "step", b"ISO-10303-21;")
    twin, projects, blender, blobs = _Twin(source), _Projects(), _Blender(("iso", "top")), {}
    converted: list[tuple[bytes, str]] = []

    def convert(content: bytes, filename: str) -> bytes:
        converted.append((content, filename))
        return b"glTF-binary"

    render = make_work_product_renderer(
        twin, projects, mcp_bridge=blender, step_converter=convert, blob_writer=_writer(blobs)
    )
    out = await render(work_product_id=str(source.id), views=["iso", "top"], samples=32)

    assert converted == [(b"ISO-10303-21;", "Upper Arm Link.step")]
    tool_id, params, _ = blender.calls[0]
    assert tool_id == "blender.render"
    assert base64.b64decode(params["model_base64"]) == b"glTF-binary"
    assert params["model_format"] == "glb"
    assert params["views"] == ["iso", "top"] and params["samples"] == 32

    (node,) = twin.created
    assert node.type == WorkProductType.RENDER
    assert node.format == "png"
    assert node.name == "Upper Arm Link renders"
    assert str(node.project_id) == PROJECT
    meta = node.metadata
    assert meta["source_work_product_id"] == str(source.id)
    assert meta["source_content_hash"] == "abc123"
    assert meta["original_filename"] == "upper-arm-link-iso.png"
    assert set(meta["image_files"]) == {"upper-arm-link-iso.png", "upper-arm-link-top.png"}
    assert blobs[meta["image_files"]["upper-arm-link-top.png"]] == PNG_TOP
    assert meta["minio_object_key"] == meta["image_files"]["upper-arm-link-iso.png"]
    assert twin.edges == [(node.id, source.id, EdgeType.GENERATED_FROM)]
    # Both project representations: the node's project_id and the junction.
    assert projects.links == [(PROJECT, str(node.id), "Upper Arm Link renders", "render")]

    assert out["stored"] is True and out["node_id"] == str(node.id)
    assert [i["view"] for i in out["images"]] == ["iso", "top"]
    assert out["blender_version"] == "5.2.2 LTS"


async def test_a_mesh_is_rendered_without_conversion(tmp_path: Path) -> None:
    source = _wp(tmp_path, "bracket", "stl", b"solid bracket")
    blender = _Blender()

    def convert(content: bytes, filename: str) -> bytes:
        raise AssertionError("a mesh needs no conversion")

    render = make_work_product_renderer(
        _Twin(source), mcp_bridge=blender, step_converter=convert, blob_writer=_writer({})
    )
    await render(work_product_id=str(source.id))
    _, params, _ = blender.calls[0]
    assert params["model_format"] == "stl"
    assert base64.b64decode(params["model_base64"]) == b"solid bracket"


async def test_store_false_records_nothing(tmp_path: Path) -> None:
    source = _wp(tmp_path, "bracket", "glb")
    twin, blobs = _Twin(source), {}
    render = make_work_product_renderer(twin, mcp_bridge=_Blender(), blob_writer=_writer(blobs))
    out = await render(work_product_id=str(source.id), store=False)
    assert out["stored"] is False and "node_id" not in out
    assert twin.created == [] and blobs == {}
    assert out["images"][0]["png_base64"]


async def test_storage_failure_refuses_rather_than_record_an_empty_node(tmp_path: Path) -> None:
    source = _wp(tmp_path, "bracket", "glb")
    twin = _Twin(source)

    def broken(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        raise ConnectionError("minio down")

    render = make_work_product_renderer(twin, mcp_bridge=_Blender(), blob_writer=broken)
    with pytest.raises(RenderError, match="could not be stored"):
        await render(work_product_id=str(source.id))
    assert twin.created == []


@pytest.mark.parametrize(
    ("fmt", "message"),
    [("kicad_pcb", "renders need a STEP"), ("csv", "renders need a STEP")],
)
async def test_other_formats_are_refused(tmp_path: Path, fmt: str, message: str) -> None:
    source = _wp(tmp_path, "board", fmt)
    blender = _Blender()
    render = make_work_product_renderer(_Twin(source), mcp_bridge=blender, blob_writer=_writer({}))
    with pytest.raises(RenderError, match=message):
        await render(work_product_id=str(source.id))
    assert blender.calls == []


async def test_unknown_and_fileless_work_products_are_refused(tmp_path: Path) -> None:
    fileless = _wp(tmp_path, "ghost", "step")
    fileless.file_path = ""
    render = make_work_product_renderer(
        _Twin(fileless), mcp_bridge=_Blender(), blob_writer=_writer({})
    )
    with pytest.raises(RenderError, match="no work product"):
        await render(work_product_id=str(uuid4()))
    with pytest.raises(RenderError, match="invalid work_product_id"):
        await render(work_product_id="not-a-uuid")
    with pytest.raises(RenderError, match="no readable model file"):
        await render(work_product_id=str(fileless.id))


async def test_a_blender_failure_is_reported(tmp_path: Path) -> None:
    source = _wp(tmp_path, "bracket", "glb")

    class _Down:
        async def invoke(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("adapter blender unavailable")

    render = make_work_product_renderer(_Twin(source), mcp_bridge=_Down(), blob_writer=_writer({}))
    with pytest.raises(RenderError, match="blender.render failed: adapter blender unavailable"):
        await render(work_product_id=str(source.id))


def test_a_converter_stub_is_refused_and_not_left_in_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api_gateway.convert.service as service

    monkeypatch.setattr(service, "_DEFAULT_CACHE_DIR", tmp_path / "cache")

    def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(service.httpx, "post", unreachable)
    with pytest.raises(RenderError, match="OCCT converter is unreachable"):
        convert_step_to_glb(b"ISO-10303-21;", "part.step")
    # The stub would otherwise answer every later request from the cache.
    assert list((tmp_path / "cache").rglob("model.glb")) == []


def test_images_become_mcp_image_content() -> None:
    result = {
        "node_id": "n1",
        "images": [
            {"view": "iso", "png_base64": "aXNv", "width": 8},
            {"view": "top", "png_base64": "dG9w", "width": 8},
        ],
    }
    lean, blocks = split_image_content(result)
    assert blocks == [
        {"type": "image", "data": "aXNv", "mimeType": "image/png"},
        {"type": "image", "data": "dG9w", "mimeType": "image/png"},
    ]
    assert lean["images"] == [
        {"view": "iso", "width": 8, "content_index": 1},
        {"view": "top", "width": 8, "content_index": 2},
    ]
    assert lean["node_id"] == "n1"
    assert "png_base64" in result["images"][0], "the original result is not mutated"


@pytest.mark.parametrize(
    "result", [{"node_id": "n1"}, {"images": [{"view": "iso"}]}, ["x"], "text", None]
)
def test_results_without_images_are_unchanged(result: Any) -> None:
    assert split_image_content(result) == (result, [])


async def test_the_twin_tool_is_registered_and_passes_options_through() -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    calls: list[dict[str, Any]] = []

    async def renderer(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"stored": True, "node_id": "n1", "images": []}

    server = TwinServer(twin=_Twin(), work_product_renderer=renderer)
    assert "twin.render_work_product" in server.tool_ids
    out = await server.render_work_product(
        {"work_product_id": "wp-1", "views": ["turntable"], "turntable_frames": 8, "store": False}
    )
    assert out["node_id"] == "n1"
    assert calls == [
        {"work_product_id": "wp-1", "store": False, "views": ["turntable"], "turntable_frames": 8}
    ]
    with pytest.raises(ValueError, match="'work_product_id' is required"):
        await server.render_work_product({})

    assert "twin.render_work_product" not in TwinServer(twin=_Twin()).tool_ids


APPEARANCE = {
    "entry": {
        "id": "ap_0123456789ab",
        "name": "black_anodised_6061",
        "version": 3,
        "finish": "anodised",
        "colour": {
            "system": "ral",
            "code": "RAL 9005",
            "name": "Jet black",
            "srgb_hex": "#0E0E10",
            "needs_review": False,
        },
        "properties": {
            "render_roughness": {"value": 0.45, "unit": "-", "needs_review": False},
            "render_metallic": {"value": 0.0, "unit": "-", "needs_review": True},
        },
    }
}


class _Library(_Blender):
    """blender.render plus library.get_appearance, as the bridge serves both."""

    async def invoke(self, tool_id: str, params: dict[str, Any], timeout: Any = None) -> Any:
        if tool_id == "library.get_appearance":
            self.calls.append((tool_id, params, timeout))
            if params["ref"] != "black_anodised_6061":
                raise ValueError(f"no appearance '{params['ref']}'")
            return APPEARANCE
        result = await super().invoke(tool_id, params, timeout)
        result["unmatched_colours"] = ["hinge"] if "hinge" in params.get("colours", {}) else []
        return result


async def test_parts_render_in_their_library_appearance(tmp_path: Path) -> None:
    source = _wp(tmp_path, "enclosure", "glb")
    twin, bridge = _Twin(source), _Library()
    render = make_work_product_renderer(twin, mcp_bridge=bridge, blob_writer=_writer({}))
    out = await render(
        work_product_id=str(source.id),
        appearances={"base": "black_anodised_6061"},
        colours={"lid": [0.1, 0.3, 0.8, 1.0]},
    )
    assert bridge.calls[0][:2] == ("library.get_appearance", {"ref": "black_anodised_6061"})
    _, params, _ = bridge.calls[1]
    assert params["colours"]["lid"] == [0.1, 0.3, 0.8, 1.0]
    base = params["colours"]["base"]
    assert base["rgba"] == pytest.approx([14 / 255, 14 / 255, 16 / 255, 1.0])
    assert (base["roughness"], base["metallic"]) == (0.45, 0.0)

    used = out["appearances"]
    assert used == [
        {
            "part": "base",
            "appearance": "black_anodised_6061",
            "appearance_id": "ap_0123456789ab",
            "version": 3,
            "finish": "anodised",
            "colour": "RAL 9005 Jet black",
            "srgb_hex": "#0E0E10",
            "needs_review": ["render_metallic"],
        }
    ]
    (node,) = twin.created
    assert node.metadata["appearances"] == used


async def test_appearance_problems_are_refused_before_rendering(tmp_path: Path) -> None:
    source = _wp(tmp_path, "enclosure", "glb")
    bridge = _Library()
    render = make_work_product_renderer(_Twin(source), mcp_bridge=bridge, blob_writer=_writer({}))
    with pytest.raises(RenderError, match="appearance 'chrome' for 'base': no appearance"):
        await render(work_product_id=str(source.id), appearances={"base": "chrome"})
    with pytest.raises(RenderError, match="base given both a colour and an appearance"):
        await render(
            work_product_id=str(source.id),
            appearances={"base": "black_anodised_6061"},
            colours={"base": [0, 0, 0, 1]},
        )
    with pytest.raises(RenderError, match="appearances must map"):
        await render(work_product_id=str(source.id), appearances=["black_anodised_6061"])
    assert all(call[0] != "blender.render" for call in bridge.calls)


async def test_a_part_name_that_matches_nothing_is_reported(tmp_path: Path) -> None:
    source = _wp(tmp_path, "enclosure", "glb")
    render = make_work_product_renderer(
        _Twin(source), mcp_bridge=_Library(), blob_writer=_writer({})
    )
    out = await render(work_product_id=str(source.id), colours={"hinge": [1, 0, 0, 1]})
    assert out["unmatched_colours"] == ["hinge"]


def test_images_reach_a_client_as_image_content_over_tools_call() -> None:
    """Through the server's real tools/call path, where the adapter's output
    arrives wrapped as {tool_id, status, data}. The first live call on the
    sandbox returned the PNGs as 2.9 MB of base64 text because the split only
    looked at the top level."""
    import asyncio

    from metaforge.mcp.server import UnifiedMcpServer
    from tool_registry.mcp_server.handlers import ToolManifest
    from tool_registry.mcp_server.server import McpToolServer

    class _Stub(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="blender", version="0")
            self.register_tool(
                ToolManifest(
                    tool_id="blender.render",
                    adapter_id="blender",
                    name="blender.render",
                    description="stub",
                    capability="render",
                ),
                self._render,
            )

        async def _render(self, args: dict[str, Any]) -> dict[str, Any]:
            png = base64.b64encode(PNG_ISO).decode()
            return {"images": [{"view": "iso", "png_base64": png}], "engine": "cycles-cpu"}

    raw = asyncio.run(
        UnifiedMcpServer([_Stub()]).handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tools/call",
                    "params": {"name": "blender.render", "arguments": {}},
                }
            )
        )
    )
    content = json.loads(raw)["result"]["content"]
    assert [b["type"] for b in content] == ["text", "image"]
    assert base64.b64decode(content[1]["data"]) == PNG_ISO
    assert content[1]["mimeType"] == "image/png"
    assert "png_base64" not in content[0]["text"]
    text = json.loads(content[0]["text"])
    assert text["data"]["images"] == [{"view": "iso", "content_index": 1}]


async def test_a_converted_step_is_rendered_z_up(tmp_path: Path) -> None:
    """The converter keeps CAD's Z-up axes in its GLB (FORGE-621); the first
    live render showed a table lying on its side until this was passed."""
    step, mesh = _wp(tmp_path, "table", "step"), _wp(tmp_path, "scan", "glb")
    bridge = _Blender()
    render = make_work_product_renderer(
        _Twin(step, mesh),
        mcp_bridge=bridge,
        step_converter=lambda content, filename: b"glb",
        blob_writer=_writer({}),
    )
    await render(work_product_id=str(step.id), store=False)
    await render(work_product_id=str(mesh.id), store=False)
    assert bridge.calls[0][1]["model_up_axis"] == "z"
    assert "model_up_axis" not in bridge.calls[1][1]
