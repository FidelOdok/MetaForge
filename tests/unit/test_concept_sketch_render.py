"""The procedural concept sketch renderer and its tool (FORGE-760).

The concept phase draws a labelled sketch per concept. The renderer turns a
block layout into one self-contained SVG with orthographic, isometric and
exploded views, so the views agree, the dimension text matches the layout,
and every label says how sure anyone is of the part it names.
"""

from __future__ import annotations

import copy
import re
import xml.dom.minidom
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.claim_recorder import is_illustrative
from api_gateway.twin.design_sketch_recorder import make_design_sketch_recorder
from tool_registry.tools.twin.adapter import TwinServer
from tool_registry.tools.twin.concept_sketch import (
    DISCLAIMER,
    VIEWS,
    explode_offsets,
    fmt_mm,
    parse_layout,
    render_concept_sketch,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType


def _chair() -> dict[str, Any]:
    leg = {"shape": "cylinder", "size_mm": [30, 30, 430], "provenance": "assumption"}
    return {
        "concept_name": "Concept A: four-leg frame",
        "parts": [
            {
                "name": "Seat",
                "size_mm": [420, 400, 18],
                "position_mm": [0, 0, 430],
                "provenance": "intended",
            },
            {**leg, "name": "Leg FL", "position_mm": [10, 10, 0]},
            {**leg, "name": "Leg FR", "position_mm": [380, 10, 0], "provenance": "verified"},
            {**leg, "name": "Leg BL", "position_mm": [10, 360, 0]},
            {**leg, "name": "Leg BR", "position_mm": [380, 360, 0]},
            {"name": "Backrest", "bbox_mm": [[0, 380, 448], [420, 400, 850]], "provenance": "open"},
        ],
        "connections": [
            {"a": "Seat", "b": "Leg FL", "joint": "8 mm dowel"},
            {"a": "Seat", "b": "Backrest", "joint": "screw", "provenance": "open"},
        ],
        "notes": "Birch ply, 18 mm",
    }


def _svg(data: dict[str, Any] | None = None) -> str:
    return render_concept_sketch(parse_layout(data or _chair()))


def _dimension_texts(svg: str) -> list[str]:
    return re.findall(r'<text class="dim"[^>]*>([^<]*)</text>', svg)


# -- the drawing ------------------------------------------------------------


def test_the_same_layout_gives_byte_identical_svg() -> None:
    assert _svg() == _svg()
    assert _svg(copy.deepcopy(_chair())) == _svg()


def test_a_changed_layout_gives_a_changed_svg() -> None:
    data = _chair()
    data["parts"][0]["size_mm"] = [450, 400, 18]
    assert _svg(data) != _svg()


def test_the_svg_is_well_formed_and_self_contained() -> None:
    svg = _svg()
    xml.dom.minidom.parseString(svg)
    assert svg.startswith("<svg xmlns=")
    for external in ("<script", "href", "url(", "@import", "<image", "<foreignObject"):
        assert external not in svg


def test_every_view_is_drawn() -> None:
    svg = _svg()
    for view in VIEWS:
        assert f'data-view="{view}"' in svg
    assert VIEWS == ("front", "side", "top", "isometric", "exploded")


def test_the_title_block_says_it_is_not_a_design_record() -> None:
    svg = _svg()
    assert DISCLAIMER == "Illustrative concept sketch, not a design record"
    assert DISCLAIMER in svg
    assert 'class="title-block"' in svg
    assert "Concept A: four-leg frame" in svg


def test_overall_and_main_part_dimensions_are_printed() -> None:
    dims = _dimension_texts(_svg())
    # Overall: 420 wide, 400 deep, 850 tall (legs 430 plus seat up to the
    # backrest top at 850).
    assert dims.count("420") >= 2  # front and top views
    assert dims.count("400") >= 2  # side and top views
    assert dims.count("850") >= 2  # front and side views
    # The main parts by volume are the seat and the backrest.
    assert "Seat 420" in dims
    assert "Seat 18" in dims
    assert "Seat 400" in dims
    assert "Backrest 402" in dims


def test_dimension_values_are_formatted_to_a_tenth_of_a_mm() -> None:
    assert fmt_mm(420.0) == "420"
    assert fmt_mm(12.25) == "12.2" or fmt_mm(12.25) == "12.3"
    assert fmt_mm(-0.01) == "0"
    data = _chair()
    data["parts"][0]["size_mm"] = [420.5, 400, 18]
    assert "420.5" in _dimension_texts(_svg(data))


def _labels(svg: str) -> list[tuple[str, str]]:
    return re.findall(r'<g class="label" data-part="([^"]*)" data-provenance="([^"]*)">', svg)


def test_every_part_is_labelled_with_its_provenance_in_both_isometric_views() -> None:
    svg = _svg()
    labels = _labels(svg)
    expected = {(p["name"], p.get("provenance", "assumption")) for p in _chair()["parts"]}
    assert set(labels) == expected
    assert len(labels) == 2 * len(_chair()["parts"])
    assert 'Seat <tspan class="tag">[intended]</tspan>' in svg
    assert 'Backrest <tspan class="tag">[open]</tspan>' in svg


def test_each_provenance_is_styled_differently() -> None:
    svg = _svg()
    style = svg[svg.index("<style>") : svg.index("</style>")]
    rules = {
        tag: re.search(rf"\.s-{tag}\{{([^}}]*)\}}", style).group(1)  # type: ignore[union-attr]
        for tag in ("verified", "intended", "assumption", "open")
    }
    assert len(set(rules.values())) == 4
    assert "dasharray" not in rules["verified"]
    assert "dasharray" in rules["assumption"]
    assert "#c62828" in rules["open"]
    # The open part carries a red question mark next to its label.
    open_label = svg[svg.index('data-provenance="open"') :]
    assert '<text class="open-mark"' in open_label[: open_label.index("</g>")]
    seat_label = svg[svg.index('data-part="Seat"') :]
    assert "open-mark" not in seat_label[: seat_label.index("</g>")]


def test_the_exploded_view_shows_the_joints() -> None:
    svg = _svg()
    exploded = svg[svg.index('data-view="exploded"') :]
    assert 'data-joint="8 mm dowel"' in exploded
    assert 'data-joint="screw"' in exploded
    assert 'class="leader"' in exploded


def test_parts_explode_along_their_assembly_axis() -> None:
    layout = parse_layout(_chair())
    offsets = dict(zip((p.name for p in layout.parts), explode_offsets(layout), strict=True))
    # The seat sits high: it lifts. The backrest sits at the back: it moves back.
    assert offsets["Seat"][2] > 0 and offsets["Seat"][:2] == (0.0, 0.0)
    assert offsets["Backrest"][2] > 0 or offsets["Backrest"][1] > 0
    data = _chair()
    data["parts"][0]["explode"] = "none"
    data["parts"][1]["explode"] = "-x"
    layout = parse_layout(data)
    seat, leg = explode_offsets(layout)[:2]
    assert seat == (0.0, 0.0, 0.0)
    assert leg[0] < 0 and leg[1:] == (0.0, 0.0)


def test_user_text_is_escaped() -> None:
    data = _chair()
    data["concept_name"] = 'Frame <b>&"bold"'
    data["parts"][0]["name"] = "Seat <top>"
    data["connections"][0]["a"] = "Seat <top>"
    data["connections"][1]["a"] = "Seat <top>"
    svg = _svg(data)
    xml.dom.minidom.parseString(svg)
    assert "<b>" not in svg and "<top>" not in svg


def test_a_cad_bounding_box_and_a_size_and_position_draw_the_same_part() -> None:
    as_size = _chair()
    as_bbox = _chair()
    as_bbox["parts"][0] = {
        "name": "Seat",
        "bbox_mm": [[0, 0, 430], [420, 400, 448]],
        "provenance": "intended",
    }
    assert _svg(as_size) == _svg(as_bbox)


# -- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["parts"][0].update(provenance="measured"), "provenance must be one of"),
        (lambda d: d["parts"][0].pop("provenance"), "provenance must be one of"),
        (lambda d: d["parts"][0].update(size_mm=[420, 0, 18]), "must be positive"),
        (lambda d: d["parts"][0].update(size_mm=[420, -5, 18]), "must be positive"),
        (lambda d: d["parts"][0].update(size_mm=[420, 400]), "three numbers"),
        (lambda d: d["parts"][0].update(size_mm=[420, True, 18]), "finite numbers"),
        (lambda d: d["parts"][0].pop("size_mm"), "size_mm is required"),
        (lambda d: d["parts"][0].update(shape="sphere"), "shape must be one of"),
        (lambda d: d["parts"][1].update(axis="w"), "axis must be one of"),
        (lambda d: d["parts"][1].update(explode="up"), "explode must be one of"),
        (lambda d: d["parts"][1].update(name="Seat"), "unique"),
        (lambda d: d["parts"][5].update(bbox_mm=[[0, 0, 0], [10, 0, 10]]), "max must exceed"),
        (lambda d: d["connections"][0].update(b="Armrest"), "not in the layout: 'Armrest'"),
        (lambda d: d["connections"][0].update(b="Seat"), "to itself"),
        (lambda d: d["connections"][0].update(joint=""), "joint must be"),
        (lambda d: d["connections"][0].update(provenance="sure"), "provenance must be one of"),
        (lambda d: d.update(parts=[]), "non-empty list"),
        (lambda d: d.update(concept_name=" "), "concept_name"),
    ],
)
def test_a_bad_layout_is_refused_with_a_clear_error(mutate: Any, message: str) -> None:
    data = _chair()
    mutate(data)
    with pytest.raises(ValueError, match=re.escape(message)):
        parse_layout(data)


# -- the tool ---------------------------------------------------------------


def _server(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TwinServer, InMemoryTwinAPI, dict[str, bytes]]:
    import digital_twin.storage.work_product_blobs as blobs

    stored: dict[str, bytes] = {}

    def store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        stored[node_id] = content
        return f"work-products/{node_id}/{filename}"

    monkeypatch.setattr(blobs, "store_work_product_blob", store)
    twin = InMemoryTwinAPI.create()
    server = TwinServer(twin=twin, design_sketch_recorder=make_design_sketch_recorder(twin, None))
    return server, twin, stored


def test_the_tool_is_registered_with_the_sketch_recorder() -> None:
    twin = InMemoryTwinAPI.create()
    server = TwinServer(twin=twin, design_sketch_recorder=make_design_sketch_recorder(twin, None))
    assert "twin.render_concept_sketch" in server.tool_ids
    assert "twin.render_concept_sketch" not in TwinServer(twin=twin).tool_ids


@pytest.mark.asyncio
async def test_the_tool_commits_an_illustrative_design_sketch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, twin, stored = _server(monkeypatch)
    out = await server.render_concept_sketch(_chair())
    assert out["views"] == list(VIEWS)
    assert out["part_count"] == 6
    assert out["connection_count"] == 2
    assert out["overall_mm"] == {"width": 420.0, "depth": 400.0, "height": 850.0}

    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.type == WorkProductType.DESIGN_SKETCH
    assert wp.name == "Concept sketch: Concept A: four-leg frame"
    assert wp.created_by == "twin.render_concept_sketch"
    assert wp.metadata["authority"] == "illustrative"
    assert wp.metadata["approved"] is False
    assert is_illustrative(wp)
    # What was stored is exactly the renderer's SVG.
    assert stored[out["node_id"]] == _svg().encode()


@pytest.mark.asyncio
async def test_the_tool_writes_nothing_for_a_bad_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    server, twin, stored = _server(monkeypatch)
    data = _chair()
    data["connections"][0]["b"] = "Armrest"
    with pytest.raises(ValueError, match="Armrest"):
        await server.render_concept_sketch(data)
    sketches = await twin.list_work_products(work_product_type=WorkProductType.DESIGN_SKETCH)
    assert sketches == [] and stored == {}


def test_the_concept_phase_gets_the_renderer() -> None:
    from mcp_core.annotations import ADDITIVE
    from mcp_core.profiles import DELIVERABLE_TOOLS, deliverable_tools

    assert "twin.render_concept_sketch" in DELIVERABLE_TOOLS["design_sketch"]
    assert "twin.render_concept_sketch" in ADDITIVE
    # Not refused for the design-flow service caller, so a phase can use it.
    assert "twin.render_concept_sketch" in deliverable_tools(["design_sketch"])
