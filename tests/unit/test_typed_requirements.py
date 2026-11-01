"""Intent becomes a requirement something can actually check (FORGE-344).

D1 asks for "typed requirements with units, acceptance criteria and
verification method". Two of those three were already real typed fields on
``Constraint``, read by ``constraint_recorder`` and by the gate engine --
and absent from the MCP tool's input schema. Supported, undocumented,
invisible to any client reading ``tools/list``, so an agent could only
find them by guessing the key.

The other half is that only ``name`` is required, which is right: a
half-specified requirement is a normal step in a conversation. What was
missing is anyone saying so. An untyped requirement is written, appears in
the evidence matrix as ``no_data``, and looks exactly like a
properly-specified one whose evidence has not arrived yet.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tool_registry.tools.twin.adapter import TwinServer, undeclared_requirement_fields


def _complete(**over: Any) -> dict[str, Any]:
    entry = {
        "name": "payload_mass",
        "metric": "payload_mass",
        "operator": "<=",
        "limit": 1.0,
        "unit": "kg",
        "verification_method": "test",
        "acceptance_criteria": "Lifts 1 kg to full reach and holds for 60 s.",
    }
    entry.update(over)
    return entry


# ---------------------------------------------------------------------------
# What the schema lets a client discover
# ---------------------------------------------------------------------------


def _constraint_properties() -> dict[str, Any]:
    server = TwinServer(twin=None, constraint_recorder=lambda **kw: None)
    manifest = server._tools["twin.record_constraint_set"].manifest
    return manifest.input_schema["properties"]["constraints"]["items"]["properties"]


@pytest.mark.parametrize(
    "field", ["metric", "operator", "limit", "unit", "verification_method", "acceptance_criteria"]
)
def test_every_field_d1_names_is_declared(field: str) -> None:
    """A field the recorder reads but the schema does not declare may as
    well not exist: the only way to find it is to already know it."""
    assert field in _constraint_properties()


def test_the_recorder_reads_what_the_schema_declares() -> None:
    """The pair that actually matters. These were supported first and
    declared second, which is the direction that leaves a gap."""
    from pathlib import Path

    recorder = (
        Path(__file__).resolve().parents[2] / "api_gateway" / "twin" / "constraint_recorder.py"
    ).read_text()
    for field in ("acceptance_criteria", "verification_method", "unit"):
        assert f'entry.get("{field}")' in recorder


# ---------------------------------------------------------------------------
# Saying what is missing
# ---------------------------------------------------------------------------


def test_a_fully_typed_requirement_reports_nothing() -> None:
    assert undeclared_requirement_fields([_complete()]) == []


def test_a_bare_name_reports_everything() -> None:
    gaps = undeclared_requirement_fields([{"name": "be_light"}])
    assert gaps[0]["name"] == "be_light"
    assert set(gaps[0]["missing"]) == {
        "expression or metric+limit",
        "verification_method",
        "acceptance_criteria",
    }


def test_a_limit_without_a_unit_is_called_out() -> None:
    """A limit of 1 is not a requirement. The matrix compares margins and
    cannot compare a bare number to a quantity."""
    gaps = undeclared_requirement_fields([_complete(unit="")])
    assert gaps[0]["missing"] == ["unit"]


def test_an_expression_counts_as_a_binding() -> None:
    """Expression-based constraints predate the metric/limit form and are
    still checkable, so they must not be reported as unbound."""
    gaps = undeclared_requirement_fields(
        [
            {
                "name": "legacy",
                "expression": "all(x < 1 for x in ctx.work_products())",
                "verification_method": "analysis",
                "acceptance_criteria": "holds for every part",
            }
        ]
    )
    assert gaps == []


def test_an_unbound_constraint_is_not_also_asked_for_a_unit() -> None:
    """There is no limit to put a unit on. Two complaints about one
    omission reads as two problems."""
    gaps = undeclared_requirement_fields(
        [{"name": "x", "verification_method": "test", "acceptance_criteria": "y"}]
    )
    assert gaps[0]["missing"] == ["expression or metric+limit"]


def test_non_dict_entries_are_ignored_rather_than_crashing() -> None:
    """The recorder validates shape itself and raises a better error."""
    assert undeclared_requirement_fields(["nonsense", None]) == []


# ---------------------------------------------------------------------------
# It reaches the caller
# ---------------------------------------------------------------------------


def _server_recording(captured: list[dict[str, Any]]) -> TwinServer:
    async def recorder(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"node_id": "wp-1", "constraint_ids": ["c-1"], "project_linked": True}

    return TwinServer(twin=None, constraint_recorder=recorder)


def test_an_incomplete_write_says_so_in_the_result() -> None:
    server = _server_recording([])
    out = asyncio.run(
        server.record_constraint_set({"title": "Arm v1", "constraints": [{"name": "be_light"}]})
    )
    assert out["node_id"] == "wp-1"  # the write still succeeded
    assert out["incomplete"][0]["name"] == "be_light"


def test_a_complete_write_carries_no_incomplete_key() -> None:
    """Absent rather than an empty list: an empty list still invites the
    reader to wonder what is in it."""
    server = _server_recording([])
    out = asyncio.run(
        server.record_constraint_set({"title": "Arm v1", "constraints": [_complete()]})
    )
    assert "incomplete" not in out


def test_the_fields_are_passed_through_to_the_recorder() -> None:
    captured: list[dict[str, Any]] = []
    server = _server_recording(captured)
    asyncio.run(server.record_constraint_set({"title": "Arm v1", "constraints": [_complete()]}))
    entry = captured[0]["constraints"][0]
    assert entry["verification_method"] == "test"
    assert entry["acceptance_criteria"].startswith("Lifts 1 kg")


def test_reporting_gaps_does_not_swallow_the_write_result() -> None:
    """The caller still needs node_id and constraint_ids to do anything
    next -- a report that replaced them would be worse than none."""
    server = _server_recording([])
    out = asyncio.run(server.record_constraint_set({"title": "T", "constraints": [{"name": "x"}]}))
    assert out["constraint_ids"] == ["c-1"]
    assert out["project_linked"] is True
