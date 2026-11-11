"""The fit test's result travels with the page's measurements (MetaForge, 2026-11-11).

The page tests whether parts clash on their drawn shapes and puts the result in
``measured.fit``. A look returns it, says how the clashes moved between two looks, and the
service keeps it in the shape the page makes and nothing more. ``overlaps`` (boxes) is left
as it was, because the concept revision sends it to the verification service.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine import look
from api_gateway.imagine.service import _clean_measured
from tests.unit.test_imagine_look import _MEASURED, SUMMARY

CLASH = {
    "a": "case",
    "b": "display",
    "a_name": "Round case",
    "b_name": "Display",
    "depth_mm": 0.48,
    "points": 12,
    "region": {"min": [-19.9, 8.7, -4.9], "max": [-19.4, 13.1, 4.9]},
}
FIT = {
    "pairs": [CLASH],
    "checked_pairs": 6,
    "not_closed": [],
    "partial": False,
    "tolerance_mm": 0.02,
    "step_mm": 0.5,
}


def _with_fit(fit: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MEASURED), "fit": copy.deepcopy(fit)}


def test_a_look_carries_the_fit_and_says_how_the_clashes_moved() -> None:
    before = {**copy.deepcopy(SUMMARY), "measured": _with_fit({**FIT, "pairs": []})}
    after = {**copy.deepcopy(SUMMARY), "measured": _with_fit(FIT)}
    assert look.summarize(after)["measured"]["fit"] == FIT
    c = look.compare(look.summarize(before), look.summarize(after))
    assert c["measured_changed"] == {"clashing_pairs": [0, 1]}


def test_a_look_with_no_fit_has_no_fit_key() -> None:
    plain = {**copy.deepcopy(SUMMARY), "measured": copy.deepcopy(_MEASURED)}
    assert "fit" not in look.summarize(plain)["measured"]


def test_the_service_keeps_the_fit_in_the_pages_shape_and_drops_anything_else() -> None:
    raw = _with_fit({**FIT, "verified": True, "pairs": [{**CLASH, "authority": "me"}]})
    out = _clean_measured(raw)
    assert out["fit"] == FIT
    assert "overlaps" in out


def test_the_service_stores_no_fit_when_the_page_sent_none() -> None:
    assert _clean_measured(copy.deepcopy(_MEASURED)).get("fit") is None


@pytest.mark.parametrize(
    "bad",
    [
        "not an object",
        {**FIT, "pairs": "x"},
        {**FIT, "pairs": [{**CLASH, "depth_mm": "deep"}]},
        {**FIT, "pairs": [{**CLASH, "region": {"min": [0, 0], "max": [1, 1, 1]}}]},
    ],
)
def test_a_malformed_fit_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        _clean_measured(_with_fit(bad))


def test_at_most_24_pairs_are_kept() -> None:
    many = {**FIT, "pairs": [{**CLASH, "b": f"p{i}"} for i in range(40)]}
    assert len(_clean_measured(_with_fit(many))["fit"]["pairs"]) == 24
