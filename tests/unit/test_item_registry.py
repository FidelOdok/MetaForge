"""Twin type registry: definitions vs records (FORGE-522)."""

from __future__ import annotations

import re
from pathlib import Path

from twin_core.items import (
    TWIN_TYPES,
    TwinTypeKind,
    classify,
    definition_types,
    family_of,
    is_definition,
)

_DOC = Path(__file__).resolve().parents[2] / "docs" / "twin_schema.md"


def test_definitions_are_the_versioned_types() -> None:
    assert set(definition_types()) == {
        "cad_model",
        "assembly",
        "constraint_set",
        "intent",
        "stakeholder_need",
        "objective",
        "bom",
        "component_selection",
    }


def test_records_are_append_only_and_never_definitions() -> None:
    for name in ("design_decision", "simulation_result", "approval", "session", "run"):
        spec = classify(name)
        assert spec is not None and spec.kind is TwinTypeKind.RECORD
        assert not is_definition(name)


def test_prd_is_derived() -> None:
    spec = classify("prd")
    assert spec is not None and spec.kind is TwinTypeKind.DERIVED
    assert not is_definition("prd")


def test_unclassified_type_is_not_a_definition() -> None:
    assert classify("assumption") is None
    assert not is_definition("assumption")


def test_every_definition_has_a_unique_key_prefix() -> None:
    prefixes = [TWIN_TYPES[n].key_prefix for n in definition_types()]
    assert all(prefixes)
    assert len(set(prefixes)) == len(prefixes)


def test_part_and_assembly_share_a_family() -> None:
    assert family_of("cad_model") == {"cad_model", "assembly"}
    assert family_of("assembly") == {"cad_model", "assembly"}
    assert family_of("intent") == {"intent"}


def test_docs_table_matches_registry() -> None:
    """docs/twin_schema.md's "Definitions vs records" table lists every row, same kind."""
    text = _DOC.read_text(encoding="utf-8")
    start = text.index("Definitions vs records")
    rows = re.findall(r"^\| `([a-z_]+)` \| (definition|record|derived) \|", text[start:], re.M)
    assert dict(rows) == {name: spec.kind.value for name, spec in TWIN_TYPES.items()}
