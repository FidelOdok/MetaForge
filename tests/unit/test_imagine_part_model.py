"""The approximation rung: the closest 3D we can justify for a part (plan step C1).

Whatever comes out must say it is an approximation or a placeholder, name its basis,
and pass the studio checker. It must never claim to be exact, and never replace a
better model with a worse one.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine.formgraph import fg_context
from api_gateway.imagine.part_model import approximate_geometry


def _check(geometry: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    fg_context.validate_geometry("part", {"geometry": geometry}, errors, warnings)
    return errors, warnings


def _box(g: dict[str, Any]) -> list[float]:
    assert len(g["body"]) == 1 and g["body"][0]["shape"] == "box"
    return g["body"][0]["size"]


def test_a_size_that_was_read_is_drawn_as_a_box_and_its_source_is_named() -> None:
    part = {
        "id": "s",
        "role": "fn:e_th",
        "geometry": {"envelope": [4.0, 3.0, 1.2], "source": "datasheet page 12, outline drawing"},
    }
    g = approximate_geometry(part)
    assert g["envelope"] == [4.0, 3.0, 1.2] and _box(g) == [4.0, 3.0, 1.2]
    assert g["fidelity"] == "approximation"
    assert "datasheet page 12" in g["fidelity_basis"] and "box" in g["fidelity_basis"]
    assert g["body"][0]["at"] == [0, 0, 0.6]


def test_an_envelope_with_no_stated_source_says_so() -> None:
    g = approximate_geometry({"id": "s", "role": "other", "geometry": {"envelope": [5, 5, 2]}})
    assert "source not stated" in g["fidelity_basis"]


@pytest.mark.parametrize("fidelity", ["exact", "generated"])
def test_a_better_model_is_never_replaced_by_a_worse_one(fidelity: str) -> None:
    geometry = {
        "envelope": [2.0, 2.5, 0.8],
        "source": "KiCad library",
        "fidelity": fidelity,
        "fidelity_basis": "read from the library's own model",
        "body": [{"shape": "box", "at": [0, 0, 0.4], "size": [2.0, 2.5, 0.8]}],
    }
    before = copy.deepcopy(geometry)
    assert approximate_geometry({"id": "s", "role": "other", "geometry": geometry}) == before


def test_a_package_name_with_its_size_gives_the_footprint_and_a_typical_height() -> None:
    part = {
        "id": "bmp",
        "role": "fn:e_th",
        "geometry": {"footprint": "Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering"},
    }
    g = approximate_geometry(part)
    assert g["envelope"][:2] == [2.0, 2.5]
    assert g["envelope"][2] > 0
    assert g["fidelity"] == "approximation"
    assert "2 x 2.5" in g["fidelity_basis"] and "typical" in g["fidelity_basis"]
    assert "LGA" in g["fidelity_basis"] and "not read" in g["fidelity_basis"]


def test_a_chip_code_gives_a_standard_size() -> None:
    g = approximate_geometry(
        {"id": "r", "role": "other", "geometry": {"footprint": "R_0603_1608Metric"}}
    )
    assert g["envelope"][:2] == [1.6, 0.8]
    assert "0603" in g["fidelity_basis"]


def test_a_named_class_of_part_gets_a_typical_size_and_the_basis_says_it_is_not_this_part() -> None:
    g = approximate_geometry({"id": "b", "role": "fn:e_btn", "name": "Tactile switch"})
    assert g["fidelity"] == "approximation"
    assert "typical" in g["fidelity_basis"] and "not this part" in g["fidelity_basis"]
    assert all(x > 0 for x in g["envelope"])


def test_a_part_we_know_nothing_about_is_a_generic_block_called_a_placeholder() -> None:
    g = approximate_geometry({"id": "mystery", "role": "other", "name": "Widget"})
    assert g["fidelity"] == "placeholder"
    assert g["envelope"] == [10.0, 10.0, 5.0]
    assert "generic" in g["fidelity_basis"]


_INPUTS: list[dict[str, Any]] = [
    {"id": "a", "role": "fn:e_btn"},
    {"id": "b", "role": "fn:e_usbc", "name": "USB-C receptacle"},
    {"id": "c", "role": "battery", "name": "CR2032 coin cell"},
    {"id": "d", "role": "board", "geometry": {"footprint": "Package_QFN:QFN-32_5x5mm_P0.5mm"}},
    {"id": "e", "role": "other"},
    {"id": "f", "role": "fn:e_led", "geometry": {"footprint": "LED_SMD:LED_0603_1608Metric"}},
    {"id": "g", "role": "other", "geometry": {"envelope": [7, 3, 2], "source": "a drawing"}},
]


@pytest.mark.parametrize("part", _INPUTS, ids=[p["id"] for p in _INPUTS])
def test_every_result_passes_the_studio_checker_and_never_claims_to_be_exact(
    part: dict[str, Any],
) -> None:
    before = copy.deepcopy(part)
    g = approximate_geometry(part)
    errors, warnings = _check(g)
    assert not errors, errors
    assert g["fidelity"] in ("approximation", "placeholder")
    assert g["fidelity_basis"].strip()
    assert any("not the real part" in w for w in fg_context_warnings(g))
    assert part == before, "the input was changed"
    assert approximate_geometry(part) == g, "the same part gave a different answer"


def fg_context_warnings(g: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []
    fg_context.validate_fidelity("part", g, errors, warnings)
    return warnings


def test_existing_openings_and_keepouts_are_kept() -> None:
    access = [{"kind": "button", "face": "+z", "size": [4, 4]}]
    g = approximate_geometry(
        {"id": "b", "role": "fn:e_btn", "geometry": {"access": access, "place": "wall"}}
    )
    assert g["access"] == access and g["place"] == "wall"
