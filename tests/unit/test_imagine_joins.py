"""Joins the form means to have (MetaForge, 2026-11-11).

The studio tests whether two parts clash on their drawn shapes. Some parts are meant to
meet: a button through a hole in the wall, a glass seated in a rim. ``form.authored.joins``
lets the agent say so, with a reason, and the page then shows that pair as joined, as
declared, not as a clash. These tests pin the write-time checks.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.imagine.formgraph import build_page
from tests.unit.test_imagine_authored_form import _catalogue, _config, lamp_form

JOIN = {"parts": ["arm", "base"], "why": "the arm is pressed into a socket in the base"}


def form_with(joins: Any) -> dict[str, Any]:
    form = lamp_form()
    form["authored"]["joins"] = joins
    return form


def _errors(form: dict[str, Any]) -> list[str]:
    errors, _ = build_page.validate(_config(form), _catalogue())
    return errors


def test_a_join_between_two_parts_with_a_reason_passes() -> None:
    assert not _errors(form_with([JOIN])), _errors(form_with([JOIN]))


def test_no_joins_is_the_same_as_none() -> None:
    assert not _errors(form_with([]))


@pytest.mark.parametrize(
    ("join", "word"),
    [
        ({**JOIN, "parts": ["arm", "wing"]}, "wing"),
        ({**JOIN, "parts": ["arm", "arm"]}, "two different parts"),
        ({**JOIN, "parts": ["arm"]}, "two"),
        ({**JOIN, "why": ""}, "why"),
        ({**JOIN, "kind": "glue"}, "kind"),
    ],
)
def test_a_bad_join_is_refused(join: dict[str, Any], word: str) -> None:
    errors = _errors(form_with([join]))
    assert any(word in e and "join" in e for e in errors), errors


def test_the_same_pair_declared_twice_is_refused() -> None:
    again = {"parts": ["base", "arm"], "why": "said again the other way round"}
    errors = _errors(form_with([JOIN, again]))
    assert any("twice" in e for e in errors), errors


def test_joins_must_be_a_list() -> None:
    errors = _errors(form_with({"arm": "base"}))
    assert any("joins" in e for e in errors), errors
