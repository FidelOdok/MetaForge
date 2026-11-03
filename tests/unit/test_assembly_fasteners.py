"""A fastener in its hole is not an interference (FORGE-752).

The Chair design gate's fourth attempt failed on six "overlaps", every one a
6 mm dowel in a 6.2 mm blind hole of the part it joins: a dowel's bounding box
always lies inside the joined part's, and the overlap check compares boxes.
With the retries used up, the run then needed a person to rework it.
"""

from __future__ import annotations

from typing import Any

from api_gateway.runs.geometry_constraints import check_assembly, is_fastener


def _part(name: str, lo: list[float], hi: list[float], **kw: Any) -> dict[str, Any]:
    return {"name": name, "position_bbox_mm": {"min": lo, "max": hi}} | kw


def _assembly(parts: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    return (
        "asm",
        "Chair Assembly",
        {"bbox_mm": {"min": [0, 0, 0], "max": [500, 500, 900]}, "parts": parts},
    )


LEG = _part("Chair Front Leg Left", [0, 0, 0], [36, 40, 450])
RAIL = _part("Chair Side Rail Left", [0, 40, 400], [36, 460, 436])


def test_a_dowel_in_its_hole_is_not_an_overlap() -> None:
    dowel = _part("Chair Dowel Rail-Leg", [15, 19, 410], [21, 61, 416])
    out = check_assembly([_assembly([LEG, RAIL, dowel])])
    assert out.violations == []


def test_a_part_marked_as_a_fastener_by_role_is_not_compared() -> None:
    screw = _part("Joint 3", [15, 19, 410], [21, 61, 416], role="fastener")
    assert is_fastener(screw)
    assert check_assembly([_assembly([LEG, RAIL, screw])]).violations == []


def test_two_real_parts_that_collide_still_fail() -> None:
    clash = _part("Chair Side Rail Right", [10, 10, 100], [30, 400, 136])
    out = check_assembly([_assembly([LEG, RAIL, clash])])
    assert any("overlap" in v and "Chair Side Rail Right" in v for v in out.violations)
