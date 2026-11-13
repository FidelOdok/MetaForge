"""A drawn guess must not make a part look sized (follow-up to plan C5).

A part's geometry says how its size was obtained: ``read`` (from a record, a drawing or a
library model), ``typical`` (a class or family guess) or ``generic`` (a placeholder
block). Only a read size, or one that says nothing, counts as an envelope for the gap
checks and for the revision Verification judges, so a guess never passes as a datasheet.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine import service
from api_gateway.imagine.concept_revision import build_concept_revision
from api_gateway.imagine.formgraph import fg_context
from api_gateway.imagine.part_model import approximate_geometry, size_is_read
from tests.unit.imagine_fixtures import full_config


def _basis(part: dict[str, Any]) -> str | None:
    value = approximate_geometry(part).get("size_basis")
    return value if isinstance(value, str) else None


def test_a_size_taken_from_the_record_is_read() -> None:
    part = {"id": "a", "role": "other", "geometry": {"envelope": [3, 3, 1], "source": "page 4"}}
    assert _basis(part) == "read"


def test_a_footprint_with_a_guessed_height_is_typical_not_read() -> None:
    part = {"id": "a", "role": "other", "geometry": {"footprint": "LGA-8_2x2.5mm"}}
    assert _basis(part) == "typical"


def test_a_class_size_is_typical_and_a_generic_block_is_generic() -> None:
    assert _basis({"id": "b", "role": "fn:e_btn"}) == "typical"
    assert _basis({"id": "c", "role": "other"}) == "generic"


@pytest.mark.parametrize(
    ("geometry", "counts"),
    [
        ({"envelope": [1, 1, 1]}, True),  # an agent's own size says nothing: unchanged
        ({"envelope": [1, 1, 1], "size_basis": "read"}, True),
        ({"envelope": [1, 1, 1], "size_basis": "typical"}, False),
        ({"envelope": [1, 1, 1], "size_basis": "generic"}, False),
        ({}, False),
        ({"size_basis": "read"}, False),
    ],
)
def test_only_a_read_size_or_one_that_says_nothing_counts_as_an_envelope(
    geometry: dict[str, Any], counts: bool
) -> None:
    assert size_is_read(geometry) is counts


def test_the_checker_refuses_an_unknown_size_basis_with_the_list() -> None:
    errors: list[str] = []
    fg_context.validate_fidelity(
        "part",
        {"fidelity": "approximation", "fidelity_basis": "x", "size_basis": "vibes"},
        errors,
        [],
    )
    assert errors and all(w in errors[0] for w in ("read", "typical", "generic"))


def _part_with(geometry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "p",
        "role": "fn:e_th",
        "datasheet": "https://example.com/p.pdf",
        "retrieved": "2026-11-10",
        "interfaces": [{"id": "io", "bus": "i2c", "dir": "bi"}],
        "geometry": geometry,
    }


def test_a_guessed_size_leaves_the_envelope_gap_open() -> None:
    assert service._part_gaps(_part_with({"envelope": [3, 3, 1], "size_basis": "typical"})) == [
        "3D envelope"
    ]
    assert service._part_gaps(_part_with({"envelope": [3, 3, 1], "size_basis": "read"})) == []
    assert service._part_gaps(_part_with({"envelope": [3, 3, 1]})) == []


def _revision(geometry: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(full_config())
    for part in config["context"]["components"]["parts"]:
        if part["id"] == "th-a":
            part["geometry"] = {**part["geometry"], **geometry}
    return build_concept_revision("p", 1, config, {}, has_mesh=False)


def _th_part(revision: dict[str, Any]) -> dict[str, Any]:
    return next(p for p in revision["parts"] if p["id"].startswith("th-a"))


def test_the_revision_verification_judges_does_not_count_a_guess_as_an_envelope() -> None:
    assert _th_part(_revision({"size_basis": "read"}))["has_envelope"] is True
    assert _th_part(_revision({"size_basis": "typical"}))["has_envelope"] is False
    assert _th_part(_revision({"size_basis": "generic"}))["has_envelope"] is False
