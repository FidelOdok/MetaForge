"""Unit tests for ``digital_twin.memory.consolidation.neo4j_insight_store``.

Driver-level integration is gated on a live Neo4j (deferred). These
tests cover the pure-Python property mapping + the connect-guard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.neo4j_insight_store import (
    Neo4jInsightStore,
    Neo4jInsightStoreError,
    _insight_to_props,
    _node_to_insight,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.models import ConfidenceTier


def _insight(**overrides: object) -> Insight:
    defaults: dict[str, object] = {
        "theme": ConsolidationTheme.MECHANICAL_VALIDATION,
        "kind": InsightKind.PRINCIPLE,
        "narrative": "Stress tests pass under nominal load with margin.",
        "confidence": 0.85,
        "supporting_experience_ids": [uuid4(), uuid4()],
    }
    defaults.update(overrides)
    return Insight(**defaults)  # type: ignore[arg-type]


def test_insight_to_props_coerces_uuids_to_strings():
    insight = _insight()
    props = _insight_to_props(insight)
    assert props["id"] == str(insight.id)
    assert all(isinstance(s, str) for s in props["supporting_experience_ids"])
    assert props["theme"] == "mechanical_validation"
    assert props["kind"] == "principle"
    assert isinstance(props["synthesized_at"], str)


def test_props_round_trip_through_node_to_insight():
    insight = _insight()
    props = _insight_to_props(insight)
    # Neo4j returns the property map as the node dict.
    restored = _node_to_insight(props)
    assert restored.id == insight.id
    assert restored.theme == insight.theme
    assert restored.kind == insight.kind
    assert restored.narrative == insight.narrative
    assert restored.supporting_experience_ids == insight.supporting_experience_ids
    assert restored.confidence_tier == insight.confidence_tier


def test_node_to_insight_handles_uuid_objects_in_list():
    node = {
        "id": "00000000-0000-0000-0000-000000000001",
        "theme": "power_analysis",
        "kind": "observation",
        "narrative": "Power budget stays under target across runs.",
        "supporting_experience_ids": [
            UUID("11111111-1111-1111-1111-111111111111"),
        ],
        "confidence": 0.75,
        "confidence_tier": "llm_inferred",
        "synthesized_at": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    }
    insight = _node_to_insight(node)
    assert insight.theme == ConsolidationTheme.POWER_ANALYSIS
    assert insight.confidence_tier == ConfidenceTier.LLM_INFERRED
    assert len(insight.supporting_experience_ids) == 1


def test_node_to_insight_normalises_naive_timestamp():
    node = {
        "id": "00000000-0000-0000-0000-000000000001",
        "theme": "misc",
        "kind": "observation",
        "narrative": "Naive timestamps get UTC attached.",
        "supporting_experience_ids": [],
        "confidence": 0.9,
        "confidence_tier": "verbatim",
        "synthesized_at": "2026-05-26T12:00:00",  # no tz
    }
    insight = _node_to_insight(node)
    assert insight.synthesized_at.tzinfo is UTC


def test_node_to_insight_recovers_from_bad_timestamp():
    node = {
        "id": "00000000-0000-0000-0000-000000000001",
        "theme": "misc",
        "kind": "observation",
        "narrative": "Bad timestamp falls back to now().",
        "supporting_experience_ids": [],
        "confidence": 0.9,
        "confidence_tier": "verbatim",
        "synthesized_at": "not-a-timestamp",
    }
    insight = _node_to_insight(node)
    assert insight.synthesized_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_write_before_connect_raises():
    store = Neo4jInsightStore()
    with pytest.raises(Neo4jInsightStoreError, match="connect"):
        await store.write(_insight())


@pytest.mark.asyncio
async def test_get_before_connect_raises():
    store = Neo4jInsightStore()
    with pytest.raises(Neo4jInsightStoreError, match="connect"):
        await store.get(uuid4())


@pytest.mark.asyncio
async def test_list_before_connect_raises():
    store = Neo4jInsightStore()
    with pytest.raises(Neo4jInsightStoreError, match="connect"):
        await store.list()
