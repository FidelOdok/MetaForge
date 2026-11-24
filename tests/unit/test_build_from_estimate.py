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
