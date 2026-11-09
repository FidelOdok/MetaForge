"""An agent-authored form: the write-time checks (spec 23.6, IMG-FORM-001, 003, 006).

The agent writes the body of a geometry function. The engine runs it in a
sandbox, but the first line of defence is here: a config that carries code with
a banned token, or too much code, is refused before it is stored, and the
refusal names the part so the agent can fix that part and no other.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine.formgraph import authored, build_page
from tests.unit.imagine_fixtures import base_config

LAMP_BASE = "return [g.cylinder(p.base_r, p.base_h).at(0, p.base_h / 2, 0).name('base')];"
LAMP_ARM = "return [g.cylinder(4, p.arm_len).at(0, p.base_h + p.arm_len / 2, 0).name('arm')];"
LAMP_HEAD = (
    "return [g.cone(p.head_r, p.head_h)"
    ".at(0, p.base_h + p.arm_len + p.head_h / 2, 0).name('head')];"
)


def lamp_form() -> dict[str, Any]:
    return {
        "template": "authored",
        "params": {"base_r": 55, "base_h": 12, "arm_len": 260, "head_r": 45, "head_h": 60},
        "ranges": {"arm_len": [120, 420]},
        "authored": {
            "parts": [
                {
                    "id": "base",
                    "name": "Weighted base",
                    "kind": "generated",
                    "code": LAMP_BASE,
                    "uses": ["base_r", "base_h"],
                    "body": True,
                },
                {
                    "id": "arm",
                    "name": "Arm",
                    "kind": "generated",
                    "code": LAMP_ARM,
                    "uses": ["base_h", "arm_len"],
                },
                {
                    "id": "head",
                    "name": "Lamp head",
                    "kind": "primitive",
                    "code": LAMP_HEAD,
                    "uses": ["base_h", "arm_len", "head_r", "head_h"],
                },
            ]
        },
    }


def with_code(code: str, part: int = 1) -> dict[str, Any]:
    form = lamp_form()
    form["authored"]["parts"][part]["code"] = code
    return form


# -- the module on its own --------------------------------------------------------


def test_a_lamp_made_of_a_base_an_arm_and_a_head_passes() -> None:
    errors, warnings = authored.check(lamp_form())
    assert errors == [] and warnings == []


@pytest.mark.parametrize(
    "token",
    [
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "importScripts",
        "import",
        "eval",
        "Function",
        "document",
        "window",
        "self",
        "globalThis",
        "postMessage",
        "constructor",
        "prototype",
        "__proto__",
        "require",
        "process",
        "this",
    ],
)
def test_code_that_names_a_banned_token_is_refused_and_the_part_is_named(token: str) -> None:
    errors, _ = authored.check(with_code(f"const x = {token}; return [];"))
    assert errors, f"{token} was allowed"
    assert any("arm" in e and token in e for e in errors), errors


def test_a_banned_word_inside_a_longer_word_is_not_a_false_alarm() -> None:
    errors, _ = authored.check(with_code("const important = 1, myself = 2; return [];"))
    assert errors == []


def test_escape_sequences_and_template_strings_are_refused_because_they_hide_names() -> None:
    for code in ("return ['\\u0066etch'];", "return [`${1}`];", "return ['\\x66etch'];"):
        errors, _ = authored.check(with_code(code))
        assert any("arm" in e for e in errors), code


def test_one_bad_part_does_not_name_the_good_ones() -> None:
    errors, _ = authored.check(with_code("fetch('x'); return [];"))
    assert errors and all("arm" in e for e in errors)
    assert not any("base" in e or "head" in e for e in errors)


def test_code_over_the_byte_budget_is_refused_naming_the_part() -> None:
    big = "return [];" + " " * authored.MAX_PART_CODE_BYTES
    errors, _ = authored.check(with_code(big, part=2))
    assert any("head" in e and "bytes" in e for e in errors), errors


def test_the_total_code_budget_is_enforced_across_parts() -> None:
    form = lamp_form()
    pad = " " * (authored.MAX_PART_CODE_BYTES - 40)
    form["authored"]["parts"] = [
        {"id": f"p{i}", "name": f"P{i}", "kind": "generated", "code": "return [];" + pad}
        for i in range(authored.MAX_PARTS)
    ]
    errors, _ = authored.check(form)
    assert any("total" in e.lower() for e in errors), errors


def test_too_many_parts_is_refused() -> None:
    form = lamp_form()
    form["authored"]["parts"] = [
        {"id": f"p{i}", "name": f"P{i}", "kind": "generated", "code": "return [];"}
        for i in range(authored.MAX_PARTS + 1)
    ]
    errors, _ = authored.check(form)
    assert any("at most" in e for e in errors), errors


def test_declared_triangles_over_the_budget_are_refused_naming_the_part() -> None:
    form = lamp_form()
    form["authored"]["parts"][0]["max_triangles"] = authored.MAX_PART_TRIANGLES + 1
    errors, _ = authored.check(form)
    assert any("base" in e and "triangles" in e for e in errors), errors


def test_the_shape_of_a_part_is_checked() -> None:
    form = lamp_form()
    form["authored"]["parts"][1].pop("code")
    form["authored"]["parts"][2]["kind"] = "magic"
    form["authored"]["parts"].append(dict(form["authored"]["parts"][0]))  # duplicate id
    errors, _ = authored.check(form)
    joined = " | ".join(errors)
    assert "arm" in joined and "code" in joined
    assert "head" in joined and "kind" in joined
    assert "duplicate" in joined.lower() and "base" in joined


def test_there_must_be_parts_and_at_most_one_body() -> None:
    form = lamp_form()
    form["authored"]["parts"] = []
    assert any("at least one part" in e for e in authored.check(form)[0])
    form = lamp_form()
    form["authored"]["parts"][1]["body"] = True
    assert any("body" in e for e in authored.check(form)[0])


def test_a_part_may_only_use_declared_parameters() -> None:
    form = lamp_form()
    form["authored"]["parts"][0]["uses"] = ["base_r", "nope"]
    errors, _ = authored.check(form)
    assert any("base" in e and "nope" in e for e in errors), errors


def test_parameters_are_numbers_with_safe_names() -> None:
    form = lamp_form()
    form["params"]["bad name"] = 3
    form["params"]["text"] = "x"  # type: ignore[assignment]
    errors, _ = authored.check(form)
    assert any("bad name" in e for e in errors) and any("text" in e for e in errors)


def test_a_form_without_a_body_part_is_allowed_but_says_what_it_cannot_do() -> None:
    form = lamp_form()
    form["authored"]["parts"][0].pop("body")
    errors, warnings = authored.check(form)
    assert errors == []
    assert any("body" in w and "not_assessed" in w for w in warnings), warnings


# -- through the config validator the studio uses ------------------------------------


def _config(form: dict[str, Any]) -> dict[str, Any]:
    cfg = copy.deepcopy(base_config())
    cfg["form"] = form
    return cfg


def _catalogue() -> dict[str, Any]:
    data: dict[str, Any] = build_page.load_json(build_page.CATALOGUE)
    return data


def test_the_config_validator_accepts_an_authored_lamp() -> None:
    errors, _ = build_page.validate(_config(lamp_form()), _catalogue())
    assert not [e for e in errors if e.startswith("form")], errors


def test_the_config_validator_refuses_a_banned_token_in_an_authored_part() -> None:
    errors, _ = build_page.validate(_config(with_code("fetch('x'); return [];")), _catalogue())
    assert any("arm" in e and "fetch" in e for e in errors), errors


def test_the_enclosure_template_still_works_and_the_unknown_template_lists_both() -> None:
    errors, _ = build_page.validate(base_config(), _catalogue())
    assert not [e for e in errors if e.startswith("form")], errors
    bad = copy.deepcopy(base_config())
    bad["form"]["template"] = "nope"
    errors, _ = build_page.validate(bad, _catalogue())
    assert any("enclosure" in e and "authored" in e for e in errors), errors


def test_an_enclosure_with_an_authored_block_is_refused() -> None:
    cfg = copy.deepcopy(base_config())
    cfg["form"]["authored"] = lamp_form()["authored"]
    errors, _ = build_page.validate(cfg, _catalogue())
    assert any("authored" in e for e in errors), errors
