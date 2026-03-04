"""Tests for observability.propagation (MET-109): W3C TraceContext propagation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from observability.propagation import (
    extract_trace_context,
    inject_trace_context,
    produce_with_context,
)

# ── inject_trace_context ───────────────────────────────────────────────


class TestInjectTraceContext:
    """inject_trace_context should be safe with or without OTel."""

    def test_returns_carrier_unchanged_without_otel(self) -> None:
        with patch("observability.propagation.HAS_OTEL", False):
            carrier: dict[str, str] = {"x-custom": "value"}
            result = inject_trace_context(carrier)
            assert result == {"x-custom": "value"}
            assert result is carrier  # same object

    def test_returns_empty_carrier_when_empty_and_no_otel(self) -> None:
        with patch("observability.propagation.HAS_OTEL", False):
            carrier: dict[str, str] = {}
            result = inject_trace_context(carrier)
            assert result == {}

    def test_calls_otel_inject_when_available(self) -> None:
        with (
            patch("observability.propagation.HAS_OTEL", True),
            patch("observability.propagation.inject") as mock_inject,
        ):
            carrier: dict[str, str] = {"x-custom": "value"}
            result = inject_trace_context(carrier)
            mock_inject.assert_called_once_with(carrier)
            assert result is carrier


# ── extract_trace_context ──────────────────────────────────────────────


class TestExtractTraceContext:
    """extract_trace_context should return None without OTel."""

    def test_returns_none_without_otel(self) -> None:
        with patch("observability.propagation.HAS_OTEL", False):
            carrier = {"traceparent": "00-abc-def-01"}
            result = extract_trace_context(carrier)
            assert result is None

    def test_calls_otel_extract_when_available(self) -> None:
        mock_context = MagicMock()
        with (
            patch("observability.propagation.HAS_OTEL", True),
            patch(
                "observability.propagation.extract", return_value=mock_context
            ) as mock_extract,
        ):
            carrier = {"traceparent": "00-abc-def-01"}
            result = extract_trace_context(carrier)
            mock_extract.assert_called_once_with(carrier)
            assert result is mock_context


# ── produce_with_context ───────────────────────────────────────────────


class TestProduceWithContext:
    """produce_with_context creates Kafka headers with trace context."""

    def test_returns_empty_dict_when_no_headers_no_otel(self) -> None:
        with patch("observability.propagation.HAS_OTEL", False):
            result = produce_with_context()
            assert result == {}

    def test_preserves_existing_headers(self) -> None:
        with patch("observability.propagation.HAS_OTEL", False):
            result = produce_with_context(headers={"x-app-id": "metaforge"})
            assert result == {"x-app-id": "metaforge"}

    def test_does_not_mutate_original_headers(self) -> None:
        with patch("observability.propagation.HAS_OTEL", False):
            original = {"x-app-id": "metaforge"}
            result = produce_with_context(headers=original)
            # result should be a new dict, not the original
            assert result is not original
            assert result == {"x-app-id": "metaforge"}

    def test_injects_otel_context_when_available(self) -> None:
        with (
            patch("observability.propagation.HAS_OTEL", True),
            patch("observability.propagation.inject") as mock_inject,
        ):
            result = produce_with_context(headers={"x-app-id": "metaforge"})
            mock_inject.assert_called_once()
            assert "x-app-id" in result
