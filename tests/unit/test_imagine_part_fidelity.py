"""A part says how its 3D was made, and the checker holds it to that (plan step C0).

The four words are exact, generated, approximation and placeholder. A part is never
shown as better than it is: anything short of exact needs the basis in words.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine import engine_page as ep
from api_gateway.imagine.formgraph import build_page
from tests.unit.imagine_fixtures import _part, full_config


def _check(mutate: Any) -> tuple[list[str], list[str]]:
    cfg = copy.deepcopy(full_config())
    part = next(p for p in cfg["context"]["components"]["parts"] if p["id"] == "led-a")
    mutate(part["geometry"])
    ctx = ep._inline_context(cfg.pop("context"))
    return build_page.validate(cfg, ep.catalogue(), ctx)


def _about_led(messages: list[str]) -> list[str]:
    return [m for m in messages if "led-a" in m]


def test_an_approximation_with_its_basis_passes_and_is_listed_as_one() -> None:
    def make(g: dict[str, Any]) -> None:
        g["fidelity"] = "approximation"
        g["fidelity_basis"] = (
            "package name 0603 gives the footprint; height is typical for the family"
        )

    errors, warnings = _check(make)
    assert not _about_led(errors), errors
    assert any("approximation" in m for m in _about_led(warnings)), warnings


def test_a_placeholder_passes_and_says_it_is_generic() -> None:
    def make(g: dict[str, Any]) -> None:
        g["fidelity"] = "placeholder"
        g["fidelity_basis"] = "no size found; a generic 10 x 10 x 5 block"

    errors, warnings = _check(make)
    assert not _about_led(errors), errors
    assert any("placeholder" in m for m in _about_led(warnings)), warnings


@pytest.mark.parametrize("word", ["exact", "generated"])
def test_exact_and_generated_pass_without_a_warning_about_fidelity(word: str) -> None:
    def make(g: dict[str, Any]) -> None:
        g["fidelity"] = word
        g["fidelity_basis"] = "read from the KiCad library model Package_LGA:Bosch_LGA-8"

    errors, warnings = _check(make)
    assert not _about_led(errors), errors
    assert not [m for m in _about_led(warnings) if "approximation" in m or "placeholder" in m]


def test_an_unknown_fidelity_word_is_refused_with_the_list() -> None:
    errors, _ = _check(lambda g: g.update(fidelity="roughly", fidelity_basis="a guess"))
    hit = [m for m in _about_led(errors) if "fidelity" in m]
    assert hit and all(w in hit[0] for w in ("exact", "generated", "approximation", "placeholder"))


@pytest.mark.parametrize("word", ["generated", "approximation", "placeholder", "exact"])
def test_every_fidelity_needs_its_basis_in_words(word: str) -> None:
    errors, _ = _check(lambda g: g.update(fidelity=word))
    assert any("fidelity_basis" in m for m in _about_led(errors)), errors


def test_a_part_that_does_not_say_how_its_3d_was_made_is_flagged_not_refused() -> None:
    errors, warnings = _check(lambda g: None)
    assert not _about_led(errors), errors
    assert any("fidelity" in m for m in _about_led(warnings)), warnings


def test_the_fixture_part_helper_still_validates() -> None:
    part = _part("x-a", "fn:e_led", "LED", [2.0, 2.0, 0.6], "gpio")
    assert part["geometry"]["envelope"] == [2.0, 2.0, 0.6]
