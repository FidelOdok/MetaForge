"""Each part of an authored form is made, and looks, its own way (MetaForge, 2026-11-11).

The studio drew every part of the smart desk lamp in the one product colour, and the
product had one process, so a printed base, a machined arm and a shade looked like one
piece made one way. A part now carries ``make``: the process it is manufactured with,
its material, finish and colour, and who chose them. The agent proposes one for each
part it makes; a person confirms it. These tests pin the write-time checks: every
value comes from the catalogue the page draws with, an invented one is refused
naming the part, and a
proposed look is reported as waiting for the person.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine.formgraph import authored, build_page
from tests.unit.test_imagine_authored_form import _catalogue, _config, lamp_form

SCHEMA = Path(build_page.__file__).parent / "data" / "product.schema.json"


def dressed() -> dict[str, Any]:
    form = lamp_form()
    base, arm, head = form["authored"]["parts"]
    base["make"] = {
        "material": "petg",
        "finish": "sanded",
        "color": "graphite",
        "source": "proposed",
        "why": "a heavy, dark base hides scuffs",
    }
    arm["make"] = {
        "process": "cnc",
        "material": "al6061",
        "finish": "anod",
        "color": "#A7ABB0",
        "source": "person",
    }
    head["make"] = {"material": "petg", "color": "arctic", "source": "proposed"}
    return form


def _fdm_config(form: dict[str, Any]) -> dict[str, Any]:
    cfg = _config(form)
    cfg["process"] = {"id": "fdm", "material": "petg", "finish": "asp", "qty": 1}
    return cfg


def _validate(form: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors, warnings = build_page.validate(_fdm_config(form), _catalogue())
    return errors, warnings


def _with(part: int, **make: Any) -> dict[str, Any]:
    form = dressed()
    form["authored"]["parts"][part]["make"] = make
    return form


def test_a_lamp_with_a_material_for_each_part_passes() -> None:
    errors, _ = _validate(dressed())
    assert not errors, errors


def test_a_proposed_look_is_reported_as_waiting_for_the_person_naming_the_parts() -> None:
    _, warnings = _validate(dressed())
    proposed = [w for w in warnings if "proposed" in w]
    assert proposed, warnings
    assert '"base"' in proposed[0] and '"head"' in proposed[0]
    assert '"arm"' not in proposed[0]


def test_a_made_part_with_no_look_is_named_when_others_have_one() -> None:
    form = dressed()
    del form["authored"]["parts"][2]["make"]
    errors, warnings = _validate(form)
    assert not errors, errors
    assert any('"head"' in w and "product colour" in w for w in warnings), warnings


def test_a_form_with_no_looks_at_all_says_nothing_new() -> None:
    errors, warnings = _validate(lamp_form())
    assert not [e for e in errors if "form.authored" in e and "make" in e]
    assert not [w for w in warnings if "product colour" in w or "proposed" in w]


def test_an_invented_material_is_refused_with_the_choices() -> None:
    errors, _ = _validate(_with(0, material="unobtainium", color="graphite", source="proposed"))
    msg = [e for e in errors if '"base"' in e and "unobtainium" in e]
    assert msg and "petg" in msg[0], errors


def test_a_material_must_belong_to_the_parts_own_process() -> None:
    errors, _ = _validate(
        _with(1, process="cnc", material="petg", color="graphite", source="person")
    )
    assert any('"arm"' in e and "petg" in e and "CNC" in e for e in errors), errors


def test_an_unknown_process_finish_or_colour_is_refused() -> None:
    errors, _ = _validate(
        _with(0, process="laser", material="petg", color="graphite", source="person")
    )
    assert any('"base"' in e and "laser" in e for e in errors), errors
    errors, _ = _validate(
        _with(0, material="petg", finish="anod", color="graphite", source="person")
    )
    assert any('"base"' in e and "anod" in e for e in errors), errors
    errors, _ = _validate(_with(0, material="petg", color="mauve", source="person"))
    assert any('"base"' in e and "mauve" in e for e in errors), errors


@pytest.mark.parametrize("color", ["#A7ABB0", "#a7abb0", "graphite"])
def test_a_colour_is_a_catalogue_colour_or_a_hex_value(color: str) -> None:
    errors, _ = _validate(_with(0, material="petg", color=color, source="person"))
    assert not errors, errors


def test_who_chose_the_look_must_be_said() -> None:
    errors, _ = _validate(_with(0, material="petg", color="graphite"))
    assert any('"base"' in e and "source" in e for e in errors), errors
    errors, _ = _validate(_with(0, material="petg", color="graphite", source="agent"))
    assert any('"base"' in e and "source" in e for e in errors), errors


def test_unknown_keys_and_a_long_reason_are_refused() -> None:
    errors, _ = _validate(_with(0, material="petg", color="graphite", source="person", gloss=1))
    assert any('"base"' in e and "gloss" in e for e in errors), errors
    errors, _ = _validate(
        _with(0, material="petg", color="graphite", source="person", why="x" * 300)
    )
    assert any('"base"' in e and "why" in e for e in errors), errors


def test_a_bought_component_keeps_its_own_look() -> None:
    form = dressed()
    form["authored"]["parts"].append(
        {
            "id": "usb",
            "name": "USB-C receptacle",
            "kind": "component",
            "code": "return [g.box(9, 3, 7)];",
            "make": {"material": "petg", "color": "graphite", "source": "proposed"},
        }
    )
    errors, _ = _validate(form)
    assert any('"usb"' in e and "component" in e for e in errors), errors


def test_the_module_check_needs_no_catalogue_to_pass_a_form_without_looks() -> None:
    errors, warnings = authored.check_make(lamp_form(), {}, [], None)
    assert errors == [] and warnings == []


def test_the_schema_names_how_each_part_is_made() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    item = schema["properties"]["form"]["properties"]["authored"]["properties"]["parts"]["items"]
    look = item["properties"]["make"]
    assert set(look["required"]) == {"material", "color", "source"}
    assert look["properties"]["source"]["enum"] == ["proposed", "person"]


def test_the_page_hands_the_runtime_its_process_material_and_colour_tables() -> None:
    js = (Path(build_page.__file__).parents[1] / "engine" / "engine.js").read_text(encoding="utf-8")
    mount = js[js.index("FGAuthored.mount(") :]
    mount = mount[: mount.index("\n")]
    assert "looks:()=>({process:S.proc,finish:S.finish," in mount, mount[:400]
    assert "materials:MATS,colors:CMF" in mount


def test_the_original_form_is_not_changed_by_the_check() -> None:
    form = dressed()
    before = copy.deepcopy(form)
    _validate(form)
    assert form == before


def test_parts_may_be_made_by_different_processes() -> None:
    form = dressed()
    errors, _ = _validate(form)
    assert not errors, errors
    made = {p["id"]: p["make"].get("process", "fdm") for p in form["authored"]["parts"]}
    assert made == {"base": "fdm", "arm": "cnc", "head": "fdm"}


def test_the_skills_example_makes_every_part_its_own_way_and_passes() -> None:
    from tests.unit.test_imagine_studio_playbook import _authored_example

    form = _authored_example()
    made = [p for p in form["authored"]["parts"] if p["kind"] != "component"]
    assert made and all("make" in p for p in made), [p["id"] for p in made if "make" not in p]
    assert not any("make" in p for p in form["authored"]["parts"] if p["kind"] == "component")
    assert len({p["make"].get("process", "fdm") for p in made}) > 1, "show parts made differently"
    errors, _ = _validate(form)
    assert not errors, errors
