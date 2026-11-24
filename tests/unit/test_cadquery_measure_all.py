"""CadQuery measures every solid it is given, and returns a STEP inline only on request.

FORGE-930 (R10): ``Workplane.val()`` is the first object on the stack, so a STEP with
several top-level solids was measured as its first solid alone; and every STEP export
was returned inline as base64, a large response for a client that only commits it.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.cadquery.adapter import _step_inline_on_request


def test_the_step_is_inline_only_when_asked_for() -> None:
    result: dict[str, Any] = {"cad_file": "/workspace/output/board.step", "step_base64": "U1RFUA=="}
    out = _step_inline_on_request(result, {}, path_key="cad_file")
    assert "step_base64" not in out and out["cad_file"] == result["cad_file"]
    assert (
        "file_path" in out["step_inline"] and "/workspace/output/board.step" in out["step_inline"]
    )
    kept = _step_inline_on_request(result, {"include_step_base64": True}, path_key="cad_file")
    assert kept == result


def test_a_result_without_a_step_is_left_alone() -> None:
    result = {"output_file": "/workspace/output/part.stl", "format": "stl"}
    assert _step_inline_on_request(result, {}, path_key="output_file") == result


def test_every_solid_is_measured_not_only_the_first() -> None:
    cq = pytest.importorskip("cadquery")
    from tool_registry.tools.cadquery.operations import shape_properties, whole_shape

    # Two separate solids, as a STEP of an assembly or a board and its parts imports.
    left = cq.Workplane().box(10, 10, 10).val()
    right = cq.Workplane().box(10, 10, 10).translate((40, 0, 0)).val()
    pair = cq.Workplane().newObject([left, right])
    assert len(pair.vals()) == 2
    props = shape_properties(pair)
    box = props["bounding_box"]
    assert box["min_x"] == -5.0 and box["max_x"] == 45.0
    assert props["volume_mm3"] == pytest.approx(2000.0, rel=1e-6)
    single = cq.Workplane().box(10, 10, 10)
    assert whole_shape(single).Volume() == pytest.approx(1000.0, rel=1e-6)
