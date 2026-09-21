"""New graph primitives for the Engineering Intent & Requirements Harness
(FORGE-43): NodeType.ENGINEERING_ENTITY + the relation-vocabulary EdgeType
additions. No behavior yet -- this just locks in the enum surface the rest
of Phase 1 (FORGE-44..49) builds on.
"""

from __future__ import annotations

import pytest

from twin_core.models.enums import EdgeType, NodeType

# Relations already covered by pre-existing EdgeType members -- must NOT be
# duplicated by new ones (see the docstring comment in enums.py).
_ALREADY_COVERED = {
    EdgeType.IMPLEMENTS,
    EdgeType.VALIDATES,
    EdgeType.CONFLICTS_WITH,
    EdgeType.SUPERSEDES,
    EdgeType.DEPENDS_ON,
    EdgeType.CONSTRAINED_BY,
}

_NEW_EDGE_TYPES = {
    "DERIVES_FROM": "derives_from",
    "SATISFIES": "satisfies",
    "MOTIVATES": "motivates",
    "REFINES": "refines",
    "DECOMPOSES_INTO": "decomposes_into",
    "ALLOCATED_TO": "allocated_to",
    "VERIFIED_BY": "verified_by",
    "SUPPORTED_BY": "supported_by",
    "ASSUMES": "assumes",
    "RISKS": "risks",
    "INVALIDATES": "invalidates",
    "GENERATED_FROM": "generated_from",
    "AFFECTED_BY": "affected_by",
    "OWNED_BY": "owned_by",
}


def test_node_type_has_engineering_entity() -> None:
    assert NodeType.ENGINEERING_ENTITY == "engineering_entity"
    assert NodeType("engineering_entity") is NodeType.ENGINEERING_ENTITY


@pytest.mark.parametrize(("member", "value"), sorted(_NEW_EDGE_TYPES.items()))
def test_new_edge_types_have_the_expected_string_value(member: str, value: str) -> None:
    edge = getattr(EdgeType, member)
    assert edge == value
    # StrEnum round-trip: constructing from the raw string returns the same member.
    assert EdgeType(value) is edge


def test_new_edge_types_do_not_duplicate_already_covered_relations() -> None:
    new_values = set(_NEW_EDGE_TYPES.values())
    covered_values = {e.value for e in _ALREADY_COVERED}
    assert new_values.isdisjoint(covered_values)


def test_new_edge_types_are_distinct_members() -> None:
    values = [getattr(EdgeType, name) for name in _NEW_EDGE_TYPES]
    assert len(values) == len(set(values))
