"""geometry.build_from_estimate: package templates with a size check (FORGE-928, R6).

The templates are pure (script + the box it must make), so they are tested here
without CadQuery; each was also built in the CadQuery container and matched its box
exactly (one solid each, the 48-lead QFP in under 4 s).
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.cadquery import adapter as cq_adapter
from tool_registry.tools.cadquery.package_templates import TemplateError, build

QFN56 = {"length": 7, "width": 7, "height": 0.9, "pitch": 0.4, "exposed_pad": 3.2}


def test_each_template_says_the_box_it_must_make() -> None:
    assert build("CHIP", {"length": 1.6, "width": 0.8, "height": 0.45}, {}, 2).bbox == (
        1.6,
        0.8,
        0.45,
    )
    qfn = build("NOLEAD", QFN56, {e: 14 for e in ("top", "bottom", "left", "right")}, 56)
    assert qfn.bbox == (7, 7, 0.9) and qfn.pins == 56 and "exposed pad 3.2 mm square" in qfn.notes
    soic = build(
        "GULL2",
        {"length": 4.9, "width": 3.9, "height": 1.5, "pitch": 1.27, "span": 6.0, "standoff": 0.1},
        {"per_side": 4},
        8,
    )
    assert soic.bbox == (4.9, 6.0, 1.6)
    bga = build(
        "BGA",
        {"length": 5, "width": 5, "height": 0.8, "pitch": 0.8, "ball_diameter": 0.4},
        {"rows": 5, "cols": 5},
        25,
    )
    assert bga.bbox == (5, 5, 1.12) and bga.pins == 25
    assert "import cadquery as cq" in soic.script and "result = " in soic.script


@pytest.mark.parametrize(
    ("template", "dims", "pins", "pin_count", "why"),
    [
        ("NOLEAD", QFN56, {e: 14 for e in ("top", "bottom", "left", "right")}, 48, "add up to 56"),
        ("CHIP", {"length": 1.6, "width": 0.8, "height": 0.45}, {}, 3, "pin_count is 3"),
        ("NOLEAD", {**QFN56, "pitch": 0.6}, {"top": 14}, None, "do not fit along the top"),
        (
            "GULL2",
            {"length": 4.9, "width": 3.9, "height": 1.5, "pitch": 1.27, "span": 3.0},
            {"per_side": 4},
            8,
            "wider than the body",
        ),
        (
            "BGA",
            {"length": 2, "width": 2, "height": 0.8, "pitch": 0.8, "ball_diameter": 0.4},
            {"rows": 5, "cols": 5},
            25,
            "does not fit",
        ),
        ("CHIP", {"length": -1, "width": 0.8, "height": 0.45}, {}, None, "positive number"),
        ("TORUS", {}, {}, None, "template is one of"),
    ],
)
def test_what_cannot_be_built_says_why(
    template: str, dims: dict[str, Any], pins: dict[str, Any], pin_count: int | None, why: str
) -> None:
    with pytest.raises(TemplateError, match=why):
        build(template, dims, pins, pin_count)


class _Ops:
    """The script runner, returning a measured box as CadQuery would."""

    made: tuple[float, float, float] = (1.6, 0.8, 0.45)

    def __init__(self, **_: Any) -> None:
        pass

    assemblies: list[list[dict[str, Any]]] = []

    def create_assembly(
        self, parts: list[dict[str, Any]], constraints: Any, out: str
    ) -> dict[str, Any]:
        self.assemblies.append(parts)
        return {"assembly_file": f"/workspace/output/{out}", "interference_check_passed": True}

    def execute_script(self, script: str, output_path: str, timeout: Any) -> dict[str, Any]:
        x, y, z = self.made
        return {
            "cad_file": f"/workspace/output/{output_path}",
            "bounding_box": {
                "min_x": -x / 2,
                "max_x": x / 2,
                "min_y": -y / 2,
                "max_y": y / 2,
                "min_z": 0,
                "max_z": z,
            },
            "volume_mm3": x * y * z,
            "step_base64": "U1RFUA==",
        }


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Any:
    import tool_registry.tools.cadquery.operations as ops

    monkeypatch.setattr(ops, "CadqueryOperations", _Ops)
    monkeypatch.setattr(ops, "script_isolation_mode", lambda: "in_process")
    return cq_adapter.CadqueryServer()


async def test_a_part_that_matches_is_returned_by_path(server: Any) -> None:
    _Ops.made = (1.6, 0.8, 0.45)
    out = await server.build_from_estimate(
        {
            "template": "CHIP",
            "dims": {"length": 1.6, "width": 0.8, "height": 0.45},
            "name": "R 0603",
        }
    )
    assert out["cad_file"].endswith("r-0603-estimate.step") and out["size_check"]["passed"]
    assert out["geometry"]["fidelity"] == "photo_estimate" and "step_base64" not in out


async def test_a_part_off_its_size_is_not_saved(server: Any) -> None:
    _Ops.made = (1.6, 0.8, 0.5)  # 0.05 mm too tall
    with pytest.raises(ValueError, match="was not saved"):
        await server.build_from_estimate(
            {"template": "CHIP", "dims": {"length": 1.6, "width": 0.8, "height": 0.45}}
        )


BREAKOUT = {
    "length": 25.4,
    "width": 17.78,
    "corner_radius": 1.0,
    "holes": [{"x": 2.54, "y": 15.24, "diameter": 2.5}, {"x": 22.86, "y": 15.24, "diameter": 2.5}],
}


def test_a_board_lists_its_subparts_and_stands_its_headers_on_top() -> None:
    """Built in the CadQuery container: 25.4 x 17.78 x 10.1 exactly, one solid."""
    built = build("BOARD", BREAKOUT, {"headers": [{"x": 2.54, "y": 1.27, "count": 9}]}, 9)
    assert built.bbox == (25.4, 17.78, 10.1) and built.pins == 9
    (tree,) = built.parts
    assert tree["kind"] == "pcb" and [c["name"] for c in tree["children"]] == ["H1", "H2", "J1"]
    j1 = tree["children"][2]
    assert j1["kind"] == "connector" and j1["pose"][2] == 1.6  # stands on the board
    assert j1["size"] == [pytest.approx(9 * 2.54), 2.54, 8.5]
    assert [s["name"] for s in built.solids] == ["board", "J1"]
    assert built.script.count(".cut(") == 2 and "fillet(1.0)" in built.script


def test_a_header_flush_with_the_edge_fits() -> None:
    edge = {"x": 46.99, "y": 6.35, "count": 4, "rows": 2, "axis": "y"}
    assert build("BOARD", {"length": 50.8, "width": 22.86}, {"headers": [edge]}, 8).pins == 8


@pytest.mark.parametrize(
    ("dims", "headers", "pin_count", "why"),
    [
        (
            {"length": 20, "width": 10},
            [{"x": 2, "y": 1.27, "count": 9}],
            None,
            "header 1 does not fit",
        ),
        ({**BREAKOUT, "holes": [{"x": 0.5, "y": 5, "diameter": 2.5}]}, [], None, "hole 1"),
        (BREAKOUT, [{"x": 2.54, "y": 1.27, "count": 9}], 8, "pin_count is 8"),
        (BREAKOUT, [{"x": 2.54, "y": 1.27, "count": 3, "rows": 3}], None, "1 or 2 rows"),
        (BREAKOUT, [{"x": 2.54, "y": 14, "count": 9}], None, "overlaps hole H1"),
        (
            {"length": 30, "width": 20},
            [{"x": 2.54, "y": 2.54, "count": 5}, {"x": 7.62, "y": 2.54, "count": 5, "name": "J2"}],
            None,
            "header J2 overlaps header J1",
        ),
        ({**BREAKOUT, "corner_radius": 9}, [], None, "corner_radius"),
    ],
)
def test_a_board_that_cannot_be_built_says_why(
    dims: dict[str, Any], headers: list[dict[str, Any]], pin_count: int | None, why: str
) -> None:
    with pytest.raises(TemplateError, match=why):
        build("BOARD", dims, {"headers": headers}, pin_count)


async def test_a_board_is_also_a_named_assembly_with_its_fidelity(server: Any) -> None:
    _Ops.made, _Ops.assemblies = (25.4, 17.78, 10.1), []
    out = await server.build_from_estimate(
        {
            "template": "BOARD",
            "name": "breakout",
            "dims": BREAKOUT,
            "pins": {"headers": [{"x": 2.54, "y": 1.27, "count": 9}]},
        }
    )
    assert out["cad_file"].endswith("breakout-estimate-assembly.step")
    assert out["merged_cad_file"].endswith("breakout-estimate.step")
    assert [p["name"] for p in _Ops.assemblies[0]] == ["board", "J1"]
    assert out["assembly"]["children"] == ["board", "J1"]
    assert out["parts"][0]["children"][2]["fidelity"] == "photo_estimate"
    assert out["geometry"]["fidelity"] == "photo_estimate"


def test_a_part_is_as_good_as_its_worst_mechanical_subpart() -> None:
    from tool_registry.tools.cadquery.package_templates import part_fidelity

    tree = {
        "kind": "pcb",
        "fidelity": "maker_cad",
        "children": [
            {"kind": "connector", "fidelity": "datasheet_built"},
            {"kind": "passive", "fidelity": "outline"},  # a capacitor does not decide fit
        ],
    }
    assert part_fidelity(tree) == "datasheet_built"
