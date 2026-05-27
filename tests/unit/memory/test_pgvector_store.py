"""Unit tests for ``digital_twin.memory.pgvector_store`` helpers + shape.

The wire-format helpers (vector literal, delete-count parser,
row→experience) are tested directly because they're pure functions.
Live-database integration is gated on ``EXPERIENCE_PGVECTOR_TEST_DSN``
in ``tests/integration/test_pgvector_experience_store.py``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID

from digital_twin.memory.models import ConfidenceTier
from digital_twin.memory.pgvector_store import (
    PgVectorExperienceStore,
    _parse_delete_count,
    _row_to_experience,
    _vector_literal,
    schema_statements,
)


def test_vector_literal_round_trips_floats():
    literal = _vector_literal([0.5, -1.0, 0.0])
    assert literal.startswith("[")
    assert literal.endswith("]")
    parts = literal.strip("[]").split(",")
    assert [float(p) for p in parts] == [0.5, -1.0, 0.0]


def test_vector_literal_coerces_nan_and_inf_to_zero():
    literal = _vector_literal([math.nan, math.inf, -math.inf, 1.0])
    parts = [float(p) for p in literal.strip("[]").split(",")]
    assert parts == [0.0, 0.0, 0.0, 1.0]


def test_vector_literal_empty_list():
    assert _vector_literal([]) == "[]"


def test_parse_delete_count_handles_typical_status():
    assert _parse_delete_count("DELETE 0") == 0
    assert _parse_delete_count("DELETE 5") == 5


def test_parse_delete_count_returns_zero_for_unexpected_format():
    assert _parse_delete_count("") == 0
    assert _parse_delete_count("UNKNOWN") == 0
    assert _parse_delete_count("DELETE not-a-number") == 0


def test_row_to_experience_reconstructs_fields():
    row = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "run_id": "r",
        "step_id": "s",
        "agent_code": "mech",
        "task_type": "validate",
        "success": True,
        "duration_seconds": 1.5,
        "result_summary": "ok",
        "error": None,
        "project_id": UUID("22222222-2222-2222-2222-222222222222"),
        "timestamp": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        "importance": 0.7,
        "confidence": "verbatim",
        "embedding": "[0.5,0.5,0.5]",
        "metadata": '{"event_id": "evt-1"}',
    }
    exp = _row_to_experience(row)
    assert exp.agent_code == "mech"
    assert exp.success is True
    assert exp.embedding == [0.5, 0.5, 0.5]
    assert exp.confidence == ConfidenceTier.VERBATIM
    assert exp.metadata == {"event_id": "evt-1"}
    assert exp.project_id == UUID("22222222-2222-2222-2222-222222222222")


def test_row_to_experience_handles_dict_metadata():
    row = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "run_id": "r",
        "step_id": "s",
        "agent_code": "mech",
        "task_type": "",
        "success": False,
        "duration_seconds": None,
        "result_summary": "",
        "error": "boom",
        "project_id": None,
        "timestamp": datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        "importance": 0.9,
        "confidence": "verbatim",
        "embedding": None,
        "metadata": {"already": "dict"},
    }
    exp = _row_to_experience(row)
    assert exp.embedding == []
    assert exp.metadata == {"already": "dict"}
    assert exp.error == "boom"


def test_constructor_defaults_embedding_dim():
    store = PgVectorExperienceStore(dsn="postgresql://example")
    assert store._embedding_dim == 384  # noqa: SLF001 — testing the default


# ---------------------------------------------------------------------------
# Schema DDL (MET-457) — verifiable without a live database
# ---------------------------------------------------------------------------


def test_schema_statements_create_table_and_extension():
    stmts = schema_statements(384)
    joined = "\n".join(stmts)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in joined
    assert "CREATE TABLE IF NOT EXISTS agent_experiences" in joined


def test_schema_statements_pins_embedding_dimension():
    assert "embedding vector(1536)" in "\n".join(schema_statements(1536))
    assert "embedding vector(384)" in "\n".join(schema_statements(384))


def test_schema_statements_metadata_is_jsonb():
    assert "metadata JSONB" in "\n".join(schema_statements(384))


def test_schema_statements_includes_ivfflat_cosine_index():
    joined = "\n".join(schema_statements(384))
    assert "USING ivfflat (embedding vector_cosine_ops)" in joined
    assert "WITH (lists = 100)" in joined


def test_schema_statements_honors_custom_ivfflat_lists():
    assert "WITH (lists = 250)" in "\n".join(schema_statements(384, ivfflat_lists=250))


def test_schema_statements_index_targets_searchable_columns():
    joined = "\n".join(schema_statements(384))
    assert "idx_agent_experiences_run" in joined
    assert "idx_agent_experiences_project" in joined
    assert "idx_agent_experiences_embedding" in joined
