"""A dummy link is not a source (found by the first live smart lamp run).

An agent that had not read a part's datasheet filled the field with ``https://example.invalid/...``
to satisfy the format check, and said so. The name ``.invalid`` is reserved to mean "not a real
address", so it must not count as a source anywhere a source is counted, or a part with no source
would read as sourced.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from api_gateway.imagine import service
from api_gateway.imagine.concept_revision import build_concept_revision
from api_gateway.imagine.part_model import is_real_source
from tests.unit.imagine_fixtures import authored_lamp_form, full_config


@pytest.mark.parametrize(
    ("value", "real"),
    [
        ("https://example.invalid/no-datasheet-read", False),
        ("http://datasheets.INVALID/x.pdf", False),
        ("https://www.mouser.com/datasheet/x.pdf", True),
        ("https://example.com/x.pdf", True),  # a reserved name, but not one that says it is fake
        ("datasheet page 12, outline drawing", True),
        ("", False),
        ("   ", False),
        (None, False),
        (5, False),
    ],
)
def test_only_a_real_looking_source_counts(value: Any, real: bool) -> None:
    assert is_real_source(value) is real


def _part(datasheet: str, **over: Any) -> dict[str, Any]:
    return {
        "id": "p",
        "role": "fn:e_th",
        "datasheet": datasheet,
        "retrieved": "2026-11-10",
        "interfaces": [{"id": "io", "bus": "i2c", "dir": "bi"}],
        "geometry": {"envelope": [3, 3, 1]},
        **over,
    }


def test_a_dummy_link_leaves_the_source_gap_open() -> None:
    assert "source" in service._part_gaps(_part("https://example.invalid/no-datasheet-read"))
    assert "source" not in service._part_gaps(_part("https://www.mouser.com/datasheet/x.pdf"))


def test_another_real_source_still_counts_beside_a_dummy_link() -> None:
    part = _part(
        "https://example.invalid/none", geometry={"envelope": [3, 3, 1], "source": "datasheet p4"}
    )
    assert "source" not in service._part_gaps(part)


def test_the_revision_verification_judges_sends_no_source_for_a_dummy_link() -> None:
    cfg = copy.deepcopy(full_config())
    cfg["form"] = authored_lamp_form()
    for p in cfg["context"]["components"]["parts"]:
        if p["id"] == "th-a":
            p["datasheet"] = "https://example.invalid/no-datasheet-read"
            p["geometry"].pop("source", None)
    rev = build_concept_revision("73491e61-8421-425c-a751-aad37a21269c", 3, cfg, {}, has_mesh=False)
    th = next(p for p in rev["parts"] if p["id"].startswith("th-a"))
    assert not th["source"]
    other = next(p for p in rev["parts"] if p["id"].startswith("usbc-a"))
    assert other["source"]
