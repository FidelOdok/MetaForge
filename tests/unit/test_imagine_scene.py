"""An authored scene for the twin's behaviours (MetaForge, 2026-11-11).

A lamp's light needs a desk to fall on and a watch a wrist to sit on. ``form.authored.scene``
lists the things the product lives with, written as code like parts and run in the same
sandbox, but never measured, costed or handed over. Behaviours may act on them, and a
``light`` behaviour casts real light from a part. These tests pin the write-time checks.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.imagine.formgraph import authored, build_page
from tests.unit.test_imagine_authored_form import _catalogue, _config, lamp_form

DESK = {
    "id": "desk",
    "name": "Desk",
    "role": "surface",
    "code": "return [g.box(600, 20, 400).at(0, -10.5, 0)];",
}
WRIST = {
    "id": "wrist",
    "name": "Wrist",
    "role": "body",
    "uses": ["base_r"],
    "code": "return [g.cylinder(p.base_r, 160, 32).at(0, -30, 0)];",
}
LIGHT = {
    "id": "lamp",
    "name": "Lamp light",
    "kind": "light",
    "parts": ["head"],
    "at": [0, 300, 0],
    "direction": [0, -1, 0],
    "angle": 40,
    "color": "#FFE2B0",
    "intensity": 2,
}


def form_with(
    scene: list[dict[str, Any]] | None = None, behaviours: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    form = lamp_form()
    if scene is not None:
        form["authored"]["scene"] = scene
    if behaviours is not None:
        form["authored"]["behaviours"] = behaviours
    return form


def _errors(form: dict[str, Any]) -> list[str]:
    errors, _ = build_page.validate(_config(form), _catalogue())
    return errors


def test_a_desk_and_a_wrist_and_a_light_pass() -> None:
    tilt = {
        "id": "turn",
        "name": "Turn the wrist",
        "kind": "rotate",
        "parts": ["wrist"],
        "axis": [0, 1, 0],
        "origin": [0, 0, 0],
        "range": [-60, 60],
    }
    assert not _errors(form_with([DESK, WRIST], [LIGHT, tilt])), _errors(
        form_with([DESK, WRIST], [LIGHT, tilt])
    )


def test_scene_code_meets_the_same_safety_checks_naming_the_scene_object() -> None:
    errors = _errors(form_with([{**DESK, "code": "fetch('x'); return [];"}]))
    assert any('scene object "desk"' in e and "fetch" in e for e in errors), errors


@pytest.mark.parametrize(
    ("obj", "word"),
    [
        ({**DESK, "id": "base"}, "same id"),
        ({**DESK, "role": "planet"}, "role"),
        ({**DESK, "kind": "component"}, "kind"),
        ({**DESK, "name": ""}, "name"),
        ({**WRIST, "uses": ["wing"]}, "wing"),
    ],
)
def test_a_bad_scene_object_is_refused(obj: dict[str, Any], word: str) -> None:
    errors = _errors(form_with([obj]))
    assert any(word in e for e in errors), errors


def test_too_many_scene_objects_are_refused() -> None:
    many = [{**DESK, "id": f"d{i}"} for i in range(authored.MAX_SCENE + 1)]
    assert any("scene" in e and str(authored.MAX_SCENE) in e for e in _errors(form_with(many)))


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"color": "warm"}, "color"),
        ({"angle": 170}, "angle"),
        ({"direction": [0, 0, 0]}, "direction"),
        ({"intensity": 50}, "intensity"),
    ],
)
def test_a_bad_light_is_refused(change: dict[str, Any], word: str) -> None:
    errors = _errors(form_with([DESK], [{**LIGHT, **change}]))
    assert any('"lamp"' in e and word in e for e in errors), errors


def test_a_form_without_a_scene_is_unchanged() -> None:
    assert authored.check_scene(lamp_form()) == ([], [])


def test_the_skill_teaches_the_scene_and_light() -> None:
    from tests.unit.test_imagine_studio_playbook import PLAYBOOK

    text = PLAYBOOK.read_text(encoding="utf-8")
    for needle in ("form.authored.scene", "a wrist for a watch", "`light` behaviour", "Night"):
        assert needle in text, needle
