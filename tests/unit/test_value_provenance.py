"""Engineering values carry where they came from (FORGE-586)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
from twin_core.api import InMemoryTwinAPI
from twin_core.items.registry import is_definition
from twin_core.provenance import SOURCE_KINDS, describe_source, source_errors

REPO = Path(__file__).resolve().parents[2]


# ── the source schema ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "user"},
        {"kind": "project_document", "document": "PRD.md", "locator": "s2"},
        {"kind": "standard", "standard": "EN 12521:2015", "clause": "5.3"},
        {"kind": "past_work", "node_id": "abc"},
        {"kind": "past_work", "project_id": "p", "ref": "shelf bracket thickness"},
        {"kind": "component", "mpn": "ABC-123"},
        {"kind": "component", "stock": "25x25x2 mm steel box section"},
        {"kind": "analysis", "evidence": "ev-1"},
        {"kind": "analysis", "method": "beam bending", "inputs": {"span_mm": 1500}},
        {"kind": "measurement", "artefact": "old_table.step"},
        {"kind": "assumption", "rationale": "mid-range of common dining tables"},
    ],
)
def test_each_kind_with_its_fields_is_a_source(source: dict[str, Any]) -> None:
    assert source_errors(source) == []


@pytest.mark.parametrize(
    ("source", "says"),
    [
        (None, "must be an object"),
        ({"kind": "guess"}, "must be one of"),
        ({"kind": "standard"}, "'standard'"),
        ({"kind": "standard", "standard": "  "}, "'standard'"),
        ({"kind": "component"}, "'mpn'"),
        ({"kind": "past_work", "project_id": "p"}, "'node_id'"),
        ({"kind": "analysis", "method": "beam bending"}, "'inputs'"),
        ({"kind": "assumption"}, "'rationale'"),
    ],
)
def test_a_source_a_reviewer_cannot_find_is_refused(source: Any, says: str) -> None:
    errors = source_errors(source)
    assert errors and says in errors[0]


def test_kinds_are_ordered_strongest_first() -> None:
    assert SOURCE_KINDS[0] == "user"
    assert SOURCE_KINDS[-1] == "assumption"


def test_describe_source_reads_as_one_line() -> None:
    line = describe_source({"kind": "standard", "standard": "EN 12521:2015", "clause": "5.3"})
    assert line == "standard: EN 12521:2015, clause 5.3"


def test_design_value_is_a_revisable_definition() -> None:
    assert is_definition("design_value")


# ── recording a design value ─────────────────────────────────────────────


def _record(**kwargs: Any) -> dict[str, Any]:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    result: dict[str, Any] = asyncio.run(record(**kwargs))
    return result


def test_a_sourced_design_value_is_recorded() -> None:
    result = _record(
        entity_type="design_value",
        statement="Table height 740 mm",
        title="table_height",
        extra={
            "name": "table_height",
            "value": 740,
            "unit": "mm",
            "source": {"kind": "standard", "standard": "EN 527-1", "clause": "4"},
        },
    )
    assert result


def test_a_design_value_without_a_source_is_refused() -> None:
    with pytest.raises(ValueError, match="'source'"):
        _record(
            entity_type="design_value",
            statement="Table height 740 mm",
            extra={"name": "table_height", "value": 740, "unit": "mm"},
        )


def test_a_design_value_without_a_value_is_refused() -> None:
    with pytest.raises(ValueError, match="'value'"):
        _record(
            entity_type="design_value",
            statement="Table height",
            extra={"name": "table_height", "source": {"kind": "user"}},
        )


def test_a_source_missing_its_fields_is_refused() -> None:
    with pytest.raises(ValueError, match="'standard' source needs"):
        _record(
            entity_type="design_value",
            statement="Table height 740 mm",
            extra={"name": "h", "value": 740, "unit": "mm", "source": {"kind": "standard"}},
        )


def test_any_entity_carrying_a_source_is_checked() -> None:
    with pytest.raises(ValueError, match="'rationale'"):
        _record(
            entity_type="assumption",
            statement="Indoor use",
            extra={"source": {"kind": "assumption"}},
        )


def test_an_assumption_source_is_flagged_for_review() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_engineering_entity_recorder(twin)
    asyncio.run(
        record(
            entity_type="design_value",
            statement="Top 1600 x 800 mm",
            title="top_length",
            extra={
                "name": "top_length",
                "value": 1600,
                "unit": "mm",
                "source": {"kind": "assumption", "rationale": "seats four"},
            },
        )
    )
    entities = asyncio.run(twin.list_engineering_entities(entity_type="design_value"))
    assert len(entities) == 1
    meta = entities[0].metadata
    assert meta["needs_review"] is True
    assert meta["source_summary"] == "assumption: seats four"


# ── a requirement limit's source ─────────────────────────────────────────


def test_a_constraint_limit_source_is_checked_and_kept() -> None:
    twin = InMemoryTwinAPI.create()
    record = make_constraint_recorder(twin)
    asyncio.run(
        record(
            title="Table requirements",
            constraints=[
                {
                    "name": "fos_corner",
                    "metric": "safety_factor",
                    "operator": ">=",
                    "limit": 2,
                    "limit_source": {"kind": "user"},
                }
            ],
        )
    )
    with pytest.raises(ValueError, match="limit_source"):
        asyncio.run(
            record(
                title="Table requirements",
                constraints=[
                    {
                        "name": "fos_corner",
                        "metric": "safety_factor",
                        "limit": 2,
                        "limit_source": {"kind": "standard"},
                    }
                ],
            )
        )


# ── the plugin ───────────────────────────────────────────────────────────


def test_the_skill_ships_in_every_bundle() -> None:
    for bundle in ("claude-code", "claude-code-local", "codex"):
        skill = REPO / "integrations" / bundle / "skills" / "source-engineering-values" / "SKILL.md"
        text = skill.read_text()
        assert "design_value" in text
        assert "component.search_parametric" in text


def test_the_session_rule_asks_for_a_source() -> None:
    from mcp_core.plugin_hooks.metaforge_hook import SESSION_RULES

    assert "without a source" in SESSION_RULES
    assert "design_value" in SESSION_RULES
