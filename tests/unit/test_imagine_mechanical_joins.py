"""A part's mechanical joins (screws, clips, slots) are recorded apart from its wiring.

An agent tried to record "Bracket B clicks into Bracket A" and "the base plate
screws to Bracket B" as part interfaces and was refused, because interfaces
are electrical buses. They now go in the part's ``mechanical`` list.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.imagine.formgraph import fg_context


def _check(mech: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    fg_context.validate_mechanical('context part "bracket"', {"mechanical": mech}, errors, warnings)
    return errors, warnings


def test_screws_and_a_clip_are_accepted() -> None:
    errors, warnings = _check(
        [
            {"id": "to_bracket_a", "kind": "clip", "to": "bracket-a", "note": "clicks in"},
            {
                "id": "base_screws",
                "kind": "screw",
                "fastener": "M2 x 6",
                "count": 4,
                "to": "base-plate",
                "at": [[-30, 0, -30], [30, 0, -30], [-30, 0, 30], [30, 0, 30]],
            },
            {"id": "one_hole", "kind": "bolt", "fastener": "M3", "at": [0, 0, 0]},
        ]
    )
    assert errors == [] and warnings == []


@pytest.mark.parametrize(
    ("mech", "says"),
    [
        ("screws", "must be a list"),
        ([{"kind": "clip"}], '"id"'),
        ([{"id": "a-b", "kind": "clip"}], '"id"'),
        ([{"id": "a", "kind": "glue"}], "kind must be one of"),
        ([{"id": "a", "kind": "clip"}, {"id": "a", "kind": "clip"}], "appears twice"),
        ([{"id": "a", "kind": "screw", "fastener": "M3", "count": 0}], "count"),
        ([{"id": "a", "kind": "screw", "fastener": "M3", "count": True}], "count"),
        ([{"id": "a", "kind": "screw", "fastener": "M3", "at": [1, 2]}], "at is"),
        ([{"id": "a", "kind": "screw", "fastener": ""}], "fastener is text"),
        ([{"id": "a", "kind": "clip", "to": 7}], "to is text"),
    ],
)
def test_a_malformed_join_is_refused_with_the_reason(mech: Any, says: str) -> None:
    errors, _ = _check(mech)
    assert any(says in e for e in errors), errors


def test_a_screw_without_a_size_is_flagged_not_refused() -> None:
    errors, warnings = _check([{"id": "a", "kind": "screw", "count": 2}])
    assert errors == []
    assert any("fastener" in w for w in warnings)


def test_a_mechanical_bus_points_to_the_mechanical_list() -> None:
    errors: list[str] = []
    fg_context.validate_interfaces(
        "part x",
        {"interfaces": [{"id": "mount", "bus": "mech", "dir": "bi"}]},
        errors,
        [],
    )
    assert any('"mechanical" list' in e for e in errors)


async def test_a_studio_write_keeps_the_joins_and_leaves_the_wiring_alone() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI

    ops.init_operations(None)
    try:
        cfg = full_config()
        parts = cfg["context"]["components"]["parts"]
        joins = [{"id": "lid_screws", "kind": "screw", "fastener": "M2 x 6", "count": 4}]
        parts[0]["mechanical"] = joins
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        stored = out.config["context"]["components"]["parts"][0]
        assert stored["mechanical"] == joins
        assert not any("lid_screws" in w for w in out.warnings)
    finally:
        ops.init_operations(None)


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [("to-bracket-a", "to_bracket_a"), ("M3 screws", "M3_screws"), ("3v3", "_3v3")],
)
def test_a_refused_id_names_the_id_that_would_pass(raw: str, fixed: str) -> None:
    errors: list[str] = []
    fg_context.validate_interfaces(
        "part x", {"interfaces": [{"id": raw, "bus": "gpio", "dir": "in"}]}, errors, []
    )
    assert any(f'"{raw}" would be "{fixed}"' in e for e in errors), errors
    mech_errors, _ = _check([{"id": raw, "kind": "clip"}])
    assert any(f'"{fixed}"' in e for e in mech_errors), mech_errors
    assert fg_context.NAME.match(fixed)
