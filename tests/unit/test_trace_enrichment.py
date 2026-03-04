"""Tests for observability.trace_enrichment (MET-110): JSONL trace enrichment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from observability.trace_enrichment import enrich_trace_entry, get_current_trace_context


def _make_mock_otel_trace(trace_id: int, span_id: int) -> MagicMock:
    """Create a mock otel_trace module with a fake current span."""
    mock_span = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.trace_id = trace_id
    mock_ctx.span_id = span_id
    mock_span.get_span_context.return_value = mock_ctx

    mock_trace_module = MagicMock()
    mock_trace_module.get_current_span.return_value = mock_span
    return mock_trace_module


# ── get_current_trace_context ──────────────────────────────────────────


class TestGetCurrentTraceContext:
    """get_current_trace_context returns trace IDs or None values."""

    def test_returns_all_none_without_otel(self) -> None:
        with patch("observability.trace_enrichment.HAS_OTEL", False):
            ctx = get_current_trace_context()
            assert ctx == {
                "trace_id": None,
                "span_id": None,
                "parent_span_id": None,
            }

    def test_returns_dict_with_three_keys(self) -> None:
        with patch("observability.trace_enrichment.HAS_OTEL", False):
            ctx = get_current_trace_context()
            assert set(ctx.keys()) == {"trace_id", "span_id", "parent_span_id"}

    def test_returns_none_when_no_active_span(self) -> None:
        mock_trace = _make_mock_otel_trace(trace_id=0, span_id=0)

        with (
            patch("observability.trace_enrichment.HAS_OTEL", True),
            patch("observability.trace_enrichment.otel_trace", mock_trace),
        ):
            ctx = get_current_trace_context()
            assert ctx["trace_id"] is None
            assert ctx["span_id"] is None

    def test_returns_hex_trace_id_when_span_active(self) -> None:
        mock_trace = _make_mock_otel_trace(
            trace_id=0x1234567890ABCDEF1234567890ABCDEF,
            span_id=0xFEDCBA0987654321,
        )

        with (
            patch("observability.trace_enrichment.HAS_OTEL", True),
            patch("observability.trace_enrichment.otel_trace", mock_trace),
        ):
            ctx = get_current_trace_context()
            assert ctx["trace_id"] == "1234567890abcdef1234567890abcdef"
            assert ctx["span_id"] == "fedcba0987654321"
            assert ctx["parent_span_id"] is None

    def test_trace_id_is_32_hex_chars(self) -> None:
        mock_trace = _make_mock_otel_trace(
            trace_id=0x00000000000000000000000000000001,
            span_id=0x0000000000000001,
        )

        with (
            patch("observability.trace_enrichment.HAS_OTEL", True),
            patch("observability.trace_enrichment.otel_trace", mock_trace),
        ):
            ctx = get_current_trace_context()
            assert len(ctx["trace_id"]) == 32  # type: ignore[arg-type]
            assert len(ctx["span_id"]) == 16  # type: ignore[arg-type]


# ── enrich_trace_entry ─────────────────────────────────────────────────


class TestEnrichTraceEntry:
    """enrich_trace_entry must add trace context fields to JSONL entries."""

    def test_adds_three_trace_fields(self) -> None:
        with patch("observability.trace_enrichment.HAS_OTEL", False):
            entry = {"event": "skill.execute", "timestamp": "2024-01-01T00:00:00Z"}
            enriched = enrich_trace_entry(entry)
            assert "trace_id" in enriched
            assert "span_id" in enriched
            assert "parent_span_id" in enriched

    def test_preserves_existing_fields(self) -> None:
        with patch("observability.trace_enrichment.HAS_OTEL", False):
            entry = {
                "event": "agent.execute",
                "agent_code": "MECH",
                "duration_ms": 1500,
            }
            enriched = enrich_trace_entry(entry)
            assert enriched["event"] == "agent.execute"
            assert enriched["agent_code"] == "MECH"
            assert enriched["duration_ms"] == 1500

    def test_does_not_mutate_original_entry(self) -> None:
        with patch("observability.trace_enrichment.HAS_OTEL", False):
            entry = {"event": "test"}
            enriched = enrich_trace_entry(entry)
            assert "trace_id" not in entry  # original unchanged
            assert "trace_id" in enriched

    def test_all_none_when_no_otel(self) -> None:
        with patch("observability.trace_enrichment.HAS_OTEL", False):
            entry = {"event": "test"}
            enriched = enrich_trace_entry(entry)
            assert enriched["trace_id"] is None
            assert enriched["span_id"] is None
            assert enriched["parent_span_id"] is None

    def test_populates_trace_id_with_mocked_otel(self) -> None:
        mock_trace = _make_mock_otel_trace(
            trace_id=0xAAAABBBBCCCCDDDD1111222233334444,
            span_id=0xEEEEFFFF00001111,
        )

        with (
            patch("observability.trace_enrichment.HAS_OTEL", True),
            patch("observability.trace_enrichment.otel_trace", mock_trace),
        ):
            entry = {"event": "skill.execute", "skill_name": "validate_stress"}
            enriched = enrich_trace_entry(entry)
            assert enriched["trace_id"] == "aaaabbbbccccdddd1111222233334444"
            assert enriched["span_id"] == "eeeeffff00001111"
            assert enriched["skill_name"] == "validate_stress"
