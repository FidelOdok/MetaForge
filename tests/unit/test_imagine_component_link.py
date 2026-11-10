"""A component in the form is the real part, not a stand-in (plan C7).

An authored part of kind ``component`` names its part in ``context.components.parts`` with
``component_ref``. Given a position and no code, the studio draws it from that part's
size, so the shape and the sourced part are one thing and cannot drift apart. A part with
its own code keeps it. A reference to a part that does not exist is refused.
"""

from __future__ import annotations

import copy
from typing import Any

from api_gateway.imagine import engine_page as ep
from api_gateway.imagine.component_link import expand_component_parts
from api_gateway.imagine.formgraph import build_page
from tests.unit.imagine_fixtures import authored_lamp_form, full_config


def _config(*extra_parts: dict[str, Any]) -> dict[str, Any]:
    cfg = copy.deepcopy(full_config())
    cfg["form"] = authored_lamp_form()
    cfg["form"]["authored"]["parts"].extend(copy.deepcopy(list(extra_parts)))
    return cfg


def _usb(**extra: Any) -> dict[str, Any]:
    return {
        "id": "usb",
        "name": "USB-C socket",
        "kind": "component",
        "component_ref": "usbc-a",
        "at": [1.5, 2.5, 3.5],
        **extra,
    }


def _check(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    cfg = copy.deepcopy(cfg)
    ctx = ep._inline_context(cfg.pop("context", None))
    return build_page.validate(cfg, ep.catalogue(), ctx)


def _part(cfg: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in cfg["form"]["authored"]["parts"] if p["id"] == pid)


def test_a_linked_component_is_drawn_from_its_parts_size_and_the_code_is_marked_derived() -> None:
    cfg = _config(_usb())
    out, notes = expand_component_parts(cfg)
    part = _part(out, "usb")
    # usbc-a is 9.0 x 7.5 x 3.2 (length, width, height). The form is y up, so height is y.
    assert part["code"] == "return [g.box(9, 3.2, 7.5).at(1.5, 2.5, 3.5)];"
    assert part["derived_from"] == "component_ref" and part["uses"] == []
    assert any("usb" in n and "usbc-a" in n for n in notes)
    errors, _ = _check(out)
    assert not [e for e in errors if "usb" in e], errors


def test_a_rotation_is_applied_after_the_box_is_made() -> None:
    out, _ = expand_component_parts(_config(_usb(rot=[0, 90, 0])))
    assert _part(out, "usb")["code"] == (
        "return [g.box(9, 3.2, 7.5).rot(0, 90, 0).at(1.5, 2.5, 3.5)];"
    )


def test_the_input_is_not_changed_and_other_parts_are_left_alone() -> None:
    cfg = _config(_usb())
    before = copy.deepcopy(cfg)
    out, _ = expand_component_parts(cfg)
    assert cfg == before
    for part in before["form"]["authored"]["parts"]:
        if part["id"] != "usb":
            assert _part(out, part["id"]) == part


def test_a_part_with_its_own_code_keeps_it() -> None:
    own = _usb(code="return [g.box(9, 3, 7).at(0, 0, 0)];")
    out, notes = expand_component_parts(_config(own))
    assert _part(out, "usb")["code"] == own["code"]
    assert "derived_from" not in _part(out, "usb") and notes == []


def test_a_derived_part_follows_the_part_when_its_size_changes() -> None:
    first, _ = expand_component_parts(_config(_usb()))
    changed = copy.deepcopy(first)
    next(p for p in changed["context"]["components"]["parts"] if p["id"] == "usbc-a")["geometry"][
        "envelope"
    ] = [10.0, 8.0, 4.0]
    again, _ = expand_component_parts(changed)
    assert _part(again, "usb")["code"] == "return [g.box(10, 4, 8).at(1.5, 2.5, 3.5)];"


def test_a_reference_to_a_part_that_does_not_exist_is_refused_with_the_known_ids() -> None:
    cfg = _config(_usb(component_ref="nope"))
    out, _ = expand_component_parts(cfg)
    errors, _ = _check(out)
    hit = [e for e in errors if "component_ref" in e and "nope" in e]
    assert hit and "usbc-a" in hit[0]


def test_a_reference_on_a_part_that_is_not_a_component_is_refused() -> None:
    bad = _usb(kind="generated", code="return [g.box(1, 1, 1)];")
    errors, _ = _check(_config(bad))
    assert any("component_ref" in e and "component" in e for e in errors), errors


def test_a_component_with_no_reference_is_flagged_as_a_stand_in() -> None:
    cfg = _config()
    stand_in = next(p for p in cfg["form"]["authored"]["parts"] if p["kind"] == "component")
    _, warnings = _check(cfg)
    assert any(stand_in["id"] in w and "stand-in" in w for w in warnings), warnings


def test_a_linked_component_is_not_flagged_as_a_stand_in() -> None:
    out, _ = expand_component_parts(_config(_usb()))
    _, warnings = _check(out)
    assert not [w for w in warnings if '"usb"' in w and "stand-in" in w], warnings


def test_a_part_with_no_size_cannot_be_drawn_and_says_so() -> None:
    cfg = _config(_usb())
    part = next(p for p in cfg["context"]["components"]["parts"] if p["id"] == "usbc-a")
    part["excluded"] = True
    part.pop("geometry")
    out, _ = expand_component_parts(cfg)
    errors, _ = _check(out)
    assert any("usb" in e and "no size" in e for e in errors), errors


async def test_through_the_real_write_the_linked_part_is_stored_derived() -> None:
    from api_gateway.imagine.service import ImagineService
    from twin_core.api import InMemoryTwinAPI

    svc = ImagineService(InMemoryTwinAPI.create())
    response = await svc.put_studio(
        "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11", _config(_usb()), actor="test"
    )
    stored = _part(response.config or {}, "usb")
    assert stored["derived_from"] == "component_ref" and "g.box(9, 3.2, 7.5)" in stored["code"]
