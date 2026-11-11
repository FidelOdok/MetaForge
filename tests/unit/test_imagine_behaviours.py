"""An authored form says how its parts behave (MetaForge, 2026-11-11).

The studio is a low-fidelity digital twin of the idea: besides its form and parts, it
shows how the product moves, lights up and switches. ``form.authored.behaviours`` names
each behaviour, the parts it acts on and its range; the page lets a person drive it. These
tests pin the write-time checks: a behaviour that names no real part, an axis of zeros or
a range that is not a range is refused, naming the behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine.formgraph import authored, build_page
from tests.unit.test_imagine_authored_form import _catalogue, _config, lamp_form

SCHEMA = Path(build_page.__file__).parent / "data" / "product.schema.json"

TILT = {
    "id": "tilt",
    "name": "Tilt the arm",
    "kind": "rotate",
    "parts": ["arm", "head"],
    "axis": [0, 0, 1],
    "origin": [0, 12, 0],
    "range": [-45, 45],
}


def behaved(*behaviours: dict[str, Any]) -> dict[str, Any]:
    form = lamp_form()
    form["authored"]["behaviours"] = list(behaviours)
    return form


def _errors(form: dict[str, Any]) -> list[str]:
    errors, _ = build_page.validate(_config(form), _catalogue())
    return errors


def test_a_lamp_that_tilts_slides_lights_and_switches_passes() -> None:
    form = behaved(
        TILT,
        {
            "id": "raise",
            "name": "Raise the head",
            "kind": "slide",
            "parts": ["head"],
            "axis": [0, 1, 0],
            "range": [0, 40],
        },
        {"id": "light", "name": "Light", "kind": "glow", "parts": ["head"], "color": "#FFD27A"},
        {"id": "base", "name": "Base shown", "kind": "toggle", "parts": ["base"], "default": True},
    )
    assert not _errors(form), _errors(form)


def test_a_behaviour_must_name_parts_of_the_form() -> None:
    errors = _errors(behaved({**TILT, "parts": ["arm", "wing"]}))
    assert any('"tilt"' in e and "wing" in e for e in errors), errors
    errors = _errors(behaved({**TILT, "parts": []}))
    assert any('"tilt"' in e and "parts" in e for e in errors), errors


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"kind": "spin"}, "kind"),
        ({"axis": [0, 0, 0]}, "axis"),
        ({"axis": [0, 1]}, "axis"),
        ({"origin": [0, "x", 0]}, "origin"),
        ({"range": [45, -45]}, "range"),
        ({"range": [-720, 0]}, "range"),
        ({"name": ""}, "name"),
        ({"id": "Tilt Arm"}, "id"),
        ({"speed": 3}, "speed"),
    ],
)
def test_a_bad_behaviour_is_refused_naming_it(change: dict[str, Any], word: str) -> None:
    errors = _errors(behaved({**TILT, **change}))
    assert any(word in e for e in errors), errors


def test_a_glow_needs_a_colour_and_ids_are_unique() -> None:
    glow = {"id": "light", "name": "Light", "kind": "glow", "parts": ["head"], "color": "warm"}
    assert any('"light"' in e and "color" in e for e in _errors(behaved(glow)))
    assert any("duplicate" in e for e in _errors(behaved(TILT, TILT)))


def test_a_slide_range_is_bounded_in_millimetres() -> None:
    slide = {
        "id": "s",
        "name": "S",
        "kind": "slide",
        "parts": ["head"],
        "axis": [0, 1, 0],
        "range": [0, 5000],
    }
    assert any('"s"' in e and "range" in e for e in _errors(behaved(slide)))


def test_too_many_behaviours_are_refused() -> None:
    many = [{**TILT, "id": f"b{i}"} for i in range(authored.MAX_BEHAVIOURS + 1)]
    assert any("behaviours" in e for e in _errors(behaved(*many)))


def test_a_form_without_behaviours_is_unchanged() -> None:
    assert authored.check_behaviours(lamp_form()) == ([], [])


def test_the_schema_names_the_behaviours() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    block = schema["properties"]["form"]["properties"]["authored"]["properties"]
    item = block["behaviours"]["items"]
    assert item["properties"]["kind"]["enum"] == ["rotate", "slide", "glow", "toggle", "light"]
