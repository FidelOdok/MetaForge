"""Variants of an authored form (MetaForge, 2026-11-11).

The agent can offer two to four variants of the idea (sporty, premium, rugged): each sets
other sizes and other makes on the same parts. The studio shows them side by side and the
person picks one. These tests pin the write-time checks: a variant may only move declared
sizes within their ranges and give real parts a make from the catalogue.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.imagine.formgraph import authored, build_page
from tests.unit.test_imagine_authored_form import _catalogue, _config, lamp_form

SPORTY = {"id": "sporty", "name": "Sporty", "why": "a lighter arm", "params": {"arm_len": 300}}
PREMIUM = {
    "id": "premium",
    "name": "Premium",
    "params": {"base_r": 60},
    "makes": {
        "arm": {
            "process": "cnc",
            "material": "al6061",
            "finish": "anod",
            "color": "#A7ABB0",
            "source": "proposed",
        }
    },
}


def varied(*variants: dict[str, Any]) -> dict[str, Any]:
    form = lamp_form()
    form["authored"]["variants"] = list(variants)
    return form


def _errors(form: dict[str, Any]) -> list[str]:
    cfg = _config(form)
    cfg["process"] = {"id": "fdm", "material": "petg", "finish": "asp", "qty": 1}
    errors, _ = build_page.validate(cfg, _catalogue())
    return errors


def test_two_good_variants_pass() -> None:
    assert not _errors(varied(SPORTY, PREMIUM)), _errors(varied(SPORTY, PREMIUM))


@pytest.mark.parametrize(
    ("variant", "word"),
    [
        ({**SPORTY, "params": {"wing": 3}}, "wing"),
        ({**SPORTY, "params": {"arm_len": 900}}, "range"),
        ({**SPORTY, "params": {"arm_len": "long"}}, "number"),
        ({**SPORTY, "name": ""}, "name"),
        ({**SPORTY, "id": "Sporty One"}, "id"),
        ({**SPORTY, "colour": "red"}, "colour"),
        ({**PREMIUM, "makes": {"wing": PREMIUM["makes"]["arm"]}}, "wing"),
        ({**PREMIUM, "makes": {"arm": {**PREMIUM["makes"]["arm"], "material": "petg"}}}, "petg"),
    ],
)
def test_a_bad_variant_is_refused_naming_it(variant: dict[str, Any], word: str) -> None:
    errors = _errors(varied(variant))
    assert any(f'"{variant["id"]}"' in e and word in e for e in errors), errors


def test_ids_are_unique_and_at_most_four() -> None:
    assert any("duplicate" in e for e in _errors(varied(SPORTY, SPORTY)))
    many = [{**SPORTY, "id": f"v{i}"} for i in range(authored.MAX_VARIANTS + 1)]
    assert any("variants" in e and str(authored.MAX_VARIANTS) in e for e in _errors(varied(*many)))


def test_a_variant_must_change_something() -> None:
    errors = _errors(varied({"id": "same", "name": "Same"}))
    assert any('"same"' in e and "change" in e for e in errors), errors
