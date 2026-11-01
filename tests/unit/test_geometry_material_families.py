"""The material check compares material families, not sentences (FORGE-718).

Live on the sandbox: once commit_geometry could record a material (FORGE-717),
every chair part carried "plywood_birch_koskisen_18mm" and the design gate
still failed all of them. The requirements read "Strength class of materials
suitable for construction" and "durable materials like wood or metal". The old
check compared every word of that text with the recorded material and treated
"wood" as a word to ignore, so plywood could never match.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.runs.geometry_constraints import _material_matches, check_geometry_constraints

CHAIR_PLY = "plywood_birch_koskisen_18mm"


def _material(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        name="material_class",
        metric="material_class",
        operator="==",
        limit=None,
        unit="",
        severity=SimpleNamespace(value="error"),
        metadata={},
        acceptance_criteria=text,
        message="",
    )


def _part(material: str | None) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"bbox_mm": {"min": [0, 0, 0], "max": [450, 450, 18]}}
    if material is not None:
        meta["material"] = material
    return ("Chair Seat", meta)


def test_the_live_chair_parts_pass_a_wood_or_metal_requirement() -> None:
    text = "The chair should be constructed using durable materials like wood or metal."
    out = check_geometry_constraints([_material(text)], [_part(CHAIR_PLY)])
    assert out.violations == []
    assert out.evaluated == 1


def test_a_requirement_naming_no_material_is_not_evaluated() -> None:
    out = check_geometry_constraints(
        [_material("Strength class of materials suitable for construction")], [_part(CHAIR_PLY)]
    )
    assert out.violations == []
    assert out.evaluated == 0
    assert any("names no material" in n for n in out.not_evaluated)


def test_a_part_with_no_material_still_fails() -> None:
    out = check_geometry_constraints([_material("wood or metal")], [_part(None)])
    assert any("records no material" in v for v in out.violations)


@pytest.mark.parametrize(
    ("required", "recorded", "expected"),
    [
        ("wood or metal", CHAIR_PLY, True),
        ("wood or metal", "aluminium 6061-T6", True),
        ("wood or metal", "PETG", False),
        ("a plastic housing", "PETG", True),
        ("18 mm birch plywood", "Birch plywood 18 mm", True),
        ("18 mm birch plywood", "pine wood", False),  # a specific material stays specific
        ("ply", "18mm birch plywood", True),
        ("aluminum", "Aluminium 6061", True),
        ("stainless steel", "aluminium", False),
        ("suitable for construction", CHAIR_PLY, None),
    ],
)
def test_material_matching(required: str, recorded: str, expected: bool | None) -> None:
    assert _material_matches(required, recorded) is expected
