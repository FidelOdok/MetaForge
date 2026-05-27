"""Unit tests for ``digital_twin.memory.consolidation.pgvector_insight_store``.

Live-DB integration is gated on a separate test (deferred behind an
env var). These tests cover the pure-Python helpers: row→insight
reconstruction and the JSON-helper for supporting IDs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.pgvector_insight_store import (
    PgVectorInsightStore,
    _experience_ids_json,
    _row_to_insight,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.models import ConfidenceTier


def test_row_to_insight_reconstructs_uuid_list():
    exp_id_a = UUID("11111111-1111-1111-1111-111111111111")
    exp_id_b = UUID("22222222-2222-2222-2222-222222222222")
    row = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "theme": "mechanical_validation",
        "kind": "principle",
        "narrative": "Stress tests pass under nominal load.",
        "supporting_experience_ids": [exp_id_a, exp_id_b],
        "confidence": 0.85,
        "confidence_tier": "llm_inferred",
        "synthesized_at": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    }
    insight = _row_to_insight(row)
    assert insight.theme == ConsolidationTheme.MECHANICAL_VALIDATION
    assert insight.kind == InsightKind.PRINCIPLE
    assert insight.supporting_experience_ids == [exp_id_a, exp_id_b]
    assert insight.confidence_tier == ConfidenceTier.LLM_INFERRED


def test_row_to_insight_handles_string_uuids():
    # Some asyncpg drivers materialise uuid[] columns as strings depending
    # on codec setup. Verify the coercion path.
    row = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "theme": "power_analysis",
        "kind": "observation",
        "narrative": "Power budget consistently under target.",
        "supporting_experience_ids": [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ],
        "confidence": 0.75,
        "confidence_tier": "llm_inferred",
        "synthesized_at": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    }
    insight = _row_to_insight(row)
    assert all(isinstance(uid, UUID) for uid in insight.supporting_experience_ids)


def test_row_to_insight_attaches_utc_when_timestamp_naive():
    row = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "theme": "misc",
        "kind": "observation",
        "narrative": "Naive timestamp gets normalised to UTC.",
        "supporting_experience_ids": [],
        "confidence": 0.72,
        "confidence_tier": "verbatim",
        "synthesized_at": datetime(2026, 5, 26, 12, 0, 0),  # tzinfo None
    }
    insight = _row_to_insight(row)
    assert insight.synthesized_at.tzinfo is UTC


def test_row_to_insight_empty_supporting_list():
    row = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "theme": "misc",
        "kind": "observation",
        "narrative": "Empty supporting list survives the round-trip.",
        "supporting_experience_ids": None,
        "confidence": 0.9,
        "confidence_tier": "verbatim",
        "synthesized_at": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    }
    insight = _row_to_insight(row)
    assert insight.supporting_experience_ids == []


def test_experience_ids_json_serializes_to_strings():
    insight = Insight(
        theme=ConsolidationTheme.POWER_ANALYSIS,
        narrative="x" * 40,
        confidence=0.8,
        supporting_experience_ids=[uuid4(), uuid4()],
    )
    serialised = _experience_ids_json(insight)
    assert isinstance(serialised, str)
    assert serialised.startswith("[") and serialised.endswith("]")
    assert serialised.count('"') == 4  # two UUIDs, two quotes each


def test_constructor_defaults_embedding_dim():
    store = PgVectorInsightStore(dsn="postgresql://example")
    assert store._embedding_dim == 384  # noqa: SLF001 — testing default
