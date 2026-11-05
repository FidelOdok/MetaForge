"""The browser render adapter (tool_registry/tools/render): ``render.model_views``.

The page-building half is pure and tested here without a browser. The last
test renders a real GLB in headless Chromium and runs only where Playwright,
its Chromium and three.js are installed (the render-adapter image, or a
developer machine with ``RENDER_THREE_DIR`` set); elsewhere it is skipped.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.unit.glb_box import box_glb
from tool_registry.tools.render import page
from tool_registry.tools.render.adapter import RenderServer
from tool_registry.tools.render.browser import playwright_available
from tool_registry.tools.render.config import RenderConfig

STATIC = Path(page.STATIC_DIR)


def _plan(views: Any = None, **kw: Any) -> page.RenderPlan:
    return page.build_plan(page.normalize_views(views), **kw)


def test_the_default_views_are_iso_front_side_top() -> None:
    plan = _plan()
    assert [v["name"] for v in plan.views] == ["iso", "front", "side", "top"]
    iso, front, side, top = plan.views
    assert iso["projection"] == "perspective" and front["projection"] == "orthographic"
    assert front["label"] == "Front" and side["label"] == "Side"
    assert plan.sheet == {"cols": 2, "rows": 2, "cell": 512, "width": 1024, "height": 1024}


def test_cad_directions_land_in_the_page_frame() -> None:
    # CAD is Z up and the page Y up: (x, y, z) -> (x, z, -y).
    assert page.cad_to_three((0.0, -1.0, 0.0)) == [0.0, 0.0, 1.0]
    assert page.cad_to_three((0.0, 0.0, 1.0)) == [0.0, 1.0, 0.0]
    views = {v["name"]: v for v in _plan(["front", "top", "side"]).views}
    # Front looks along +Y, so the camera sits at -Y: the page's +Z.
    assert views["front"]["direction"] == [0.0, 0.0, 1.0] and views["front"]["up"] == [0, 1, 0]
    assert views["top"]["direction"] == [0.0, 1.0, 0.0]
    assert views["side"]["direction"] == [1.0, 0.0, 0.0]
    assert views["front"]["dim_axes"] == ["x", "z"] and views["top"]["dim_axes"] == ["x", "y"]


def test_a_section_keeps_the_side_below_the_cut_and_looks_at_it() -> None:
    (sec,) = _plan([{"view": "section", "section": {"axis": "y", "offset": 0.25}}]).views
    assert sec["name"] == "section_y" and sec["label"] == "Section Y at 25%"
    # Keep CAD y <= cut: the plane normal is -Y in CAD, +Z in the page.
    assert sec["section"]["normal"] == [0.0, 0.0, 1.0]
    assert sec["section"]["axis_three"] == [0.0, 0.0, -1.0]
    # Seen from the side cut away (+Y in CAD, the back view).
    assert sec["base"] == "back"


def test_bad_view_specs_and_sizes_are_refused() -> None:
    with pytest.raises(ValueError):
        page.normalize_views(["sideways"])
    with pytest.raises(ValueError):
        page.normalize_views([{"view": "section"}])
    with pytest.raises(ValueError):
        page.normalize_views(["iso"] * (page.MAX_VIEWS + 1))
    with pytest.raises(ValueError):
        _plan(width=64)
    with pytest.raises(ValueError):
        _plan(up_axis="x")
    with pytest.raises(ValueError):
        _plan(background="red")


def test_dimensions_carry_their_source() -> None:
    plan = _plan(dimensions={"x": 40, "y": 20, "z": 10, "source": "cadquery bounding box"})
    assert plan.dimensions is not None and plan.dimensions.source == "cadquery bounding box"
    assert plan.model_dump(mode="json")["dimensions"]["x"] == 40.0


def test_the_page_loads_three_from_the_image_never_a_cdn() -> None:
    html = page.build_page_html()
    assert '"three":"/three/build/three.module.js"' in html
    assert '"three/addons/":"/three/examples/jsm/"' in html
    viewer = (STATIC / "viewer.js").read_text(encoding="utf-8")
    for text in (html, viewer):
        assert "http://" not in text.replace(page.ORIGIN, "") and "https://" not in text
    assert "window.renderViews = renderViews" in viewer


def test_only_the_page_files_are_served(tmp_path: Path) -> None:
    three = tmp_path / "three"
    (three / "build").mkdir(parents=True)
    (three / "build" / "three.module.js").write_text("export {}")
    assert (
        page.resolve_route("/three/build/three.module.js", three_dir=three)
        == (three / "build" / "three.module.js").resolve()
    )
    assert page.resolve_route("/viewer.js", three_dir=three) == (STATIC / "viewer.js").resolve()
    for bad in ("/three/../secret", "/three/build/../../x", "/etc/passwd", "/a/b.js", "/three/"):
        assert page.resolve_route(bad, three_dir=three) is None, bad


def test_png_data_urls_are_checked() -> None:
    png = b"\x89PNG\r\n\x1a\n1234"
    assert page.decode_png("data:image/png;base64," + base64.b64encode(png).decode()) == png
    with pytest.raises(ValueError):
        page.decode_png("data:image/png;base64," + base64.b64encode(b"GIF89a").decode())
    with pytest.raises(ValueError):
        page.decode_png(None)


def test_the_plan_is_safe_inside_a_script_element() -> None:
    assert "</script>" not in page._script_json({"t": "</script><b>"})  # noqa: SLF001


async def test_the_adapter_checks_its_input_before_any_browser(tmp_path: Path) -> None:
    server = RenderServer(RenderConfig(work_dir=str(tmp_path), three_dir=str(tmp_path)))
    assert server.tool_ids == ["render.model_views"]
    with pytest.raises(ValueError, match="required"):
        await server.model_views({})
    with pytest.raises(ValueError, match="not a binary glTF"):
        await server.model_views({"glb_base64": base64.b64encode(b"solid x").decode()})
    with pytest.raises(ValueError, match="inside the adapter workspace"):
        await server.model_views({"input_file": "../outside.glb"})


async def test_without_a_browser_the_call_says_where_it_runs(tmp_path: Path) -> None:
    from tool_registry.tools.render.browser import BrowserNotAvailableError

    server = RenderServer(RenderConfig(work_dir=str(tmp_path), three_dir=str(tmp_path / "none")))
    with pytest.raises(BrowserNotAvailableError):
        await server.model_views({"glb_base64": base64.b64encode(box_glb()).decode()})


def test_the_box_fixture_is_a_glb() -> None:
    data = box_glb(40, 20, 10)
    assert data[:4] == b"glTF"
    length = int.from_bytes(data[12:16], "little")
    doc = json.loads(data[20 : 20 + length])
    assert doc["accessors"][0]["max"] == [40.0, 20.0, 10.0]


_THREE = os.environ.get("RENDER_THREE_DIR", "/app/vendor/three")


@pytest.mark.skipif(
    not playwright_available() or not Path(_THREE, "build", "three.module.js").is_file(),
    reason="needs Playwright with Chromium and three.js (the render-adapter image)",
)
async def test_a_real_render_in_headless_chromium(tmp_path: Path) -> None:
    server = RenderServer(RenderConfig(work_dir=str(tmp_path), three_dir=_THREE))
    out = await server.model_views(
        {
            "glb_base64": base64.b64encode(box_glb(40, 20, 10)).decode(),
            "views": ["iso", "front", "side", "top", {"view": "section", "section": {"axis": "x"}}],
            "dimensions": {"x": 40, "y": 20, "z": 10, "source": "test"},
        }
    )
    assert [v["view"] for v in out["views"]] == ["iso", "front", "side", "top", "section_x"]
    for v in out["views"]:
        assert base64.b64decode(v["png_base64"]).startswith(b"\x89PNG")
    assert base64.b64decode(out["sheet_png_base64"]).startswith(b"\x89PNG")
    bbox = out["bbox_mm"]
    assert (bbox["max_x"] - bbox["min_x"], bbox["max_y"] - bbox["min_y"]) == (40.0, 20.0)
    assert bbox["max_z"] - bbox["min_z"] == 10.0
    front = next(v for v in out["views"] if v["view"] == "front")
    assert front["mm_per_px"] > 0
    assert out["triangles"] == 12 and "page_errors" not in out
