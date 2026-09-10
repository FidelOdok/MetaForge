"""Tests for the structlog -> OTel logging bridge."""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import structlog

from observability.config import ObservabilityConfig
from observability.logging import (
    _ConsoleHandler,
    add_trace_context,
    configure_logging,
    ensure_console_handler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span_context(
    trace_id: int = 0x0AF7651916CD43DD8448EB211C80319C,
    span_id: int = 0x00F067AA0BA902B7,
    trace_flags: int = 1,
) -> MagicMock:
    """Create a mock SpanContext with the given IDs."""
    ctx = MagicMock()
    ctx.trace_id = trace_id
    ctx.span_id = span_id
    ctx.trace_flags = trace_flags
    ctx.is_valid = True
    return ctx


def _make_span(span_context: MagicMock | None = None) -> MagicMock:
    """Create a mock Span wrapping the given SpanContext."""
    span = MagicMock()
    span.get_span_context.return_value = span_context or _make_span_context()
    return span


# ---------------------------------------------------------------------------
# add_trace_context -- no OTel
# ---------------------------------------------------------------------------


class TestAddTraceContextNoOtel:
    """Processor behaviour when OTel is not installed."""

    def test_returns_event_dict_unchanged_without_otel(self) -> None:
        with patch("observability.logging._otel_available", return_value=False):
            event: dict[str, Any] = {"event": "hello", "key": "value"}
            result = add_trace_context(None, "info", event)
        assert result is event
        assert "trace_id" not in result
        assert "span_id" not in result

    def test_preserves_existing_fields(self) -> None:
        with patch("observability.logging._otel_available", return_value=False):
            event: dict[str, Any] = {"event": "test", "custom": 42}
            result = add_trace_context(None, "debug", event)
        assert result["custom"] == 42
        assert result["event"] == "test"


# ---------------------------------------------------------------------------
# add_trace_context -- no active span
# ---------------------------------------------------------------------------


class TestAddTraceContextNoSpan:
    """Processor behaviour when OTel is available but no span is active."""

    def test_no_active_span_returns_unchanged(self) -> None:
        mock_trace = MagicMock()
        # get_current_span returns a span with invalid context
        invalid_span = MagicMock()
        invalid_ctx = MagicMock()
        invalid_ctx.is_valid = False
        invalid_span.get_span_context.return_value = invalid_ctx
        mock_trace.get_current_span.return_value = invalid_span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            event: dict[str, Any] = {"event": "no-span"}
            result = add_trace_context(None, "info", event)

        assert "trace_id" not in result
        assert "span_id" not in result

    def test_none_span_returns_unchanged(self) -> None:
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = None

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            event: dict[str, Any] = {"event": "null-span"}
            result = add_trace_context(None, "info", event)

        assert "trace_id" not in result

    def test_none_span_context_returns_unchanged(self) -> None:
        mock_trace = MagicMock()
        span = MagicMock()
        span.get_span_context.return_value = None
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            event: dict[str, Any] = {"event": "null-ctx"}
            result = add_trace_context(None, "info", event)

        assert "trace_id" not in result


# ---------------------------------------------------------------------------
# add_trace_context -- with active span
# ---------------------------------------------------------------------------


class TestAddTraceContextWithSpan:
    """Processor injects trace context when a valid span is active."""

    def test_injects_trace_id_and_span_id(self) -> None:
        span = _make_span()
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            event: dict[str, Any] = {"event": "traced"}
            result = add_trace_context(None, "info", event)

        assert "trace_id" in result
        assert "span_id" in result
        assert isinstance(result["trace_id"], str)
        assert isinstance(result["span_id"], str)

    def test_trace_id_format_is_hex(self) -> None:
        sc = _make_span_context(trace_id=0xABCDEF, span_id=0x123456)
        span = _make_span(sc)
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            result = add_trace_context(None, "info", {"event": "hex"})

        assert result["trace_id"] == format(0xABCDEF, "032x")
        assert result["span_id"] == format(0x123456, "016x")

    def test_trace_flags_injected(self) -> None:
        sc = _make_span_context(trace_flags=1)
        span = _make_span(sc)
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            result = add_trace_context(None, "info", {"event": "flags"})

        assert result["trace_flags"] == 1

    def test_existing_fields_preserved(self) -> None:
        span = _make_span()
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            event: dict[str, Any] = {"event": "preserve-me", "custom_key": "keep"}
            result = add_trace_context(None, "info", event)

        assert result["custom_key"] == "keep"
        assert result["event"] == "preserve-me"
        # And trace context is also present
        assert "trace_id" in result

    def test_trace_id_is_zero_padded_32_chars(self) -> None:
        sc = _make_span_context(trace_id=1)
        span = _make_span(sc)
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            result = add_trace_context(None, "info", {"event": "pad"})

        assert len(result["trace_id"]) == 32

    def test_span_id_is_zero_padded_16_chars(self) -> None:
        sc = _make_span_context(span_id=1)
        span = _make_span(sc)
        mock_trace = MagicMock()
        mock_trace.get_current_span.return_value = span

        with (
            patch("observability.logging._otel_available", return_value=True),
            patch("observability.logging.otel_trace", mock_trace),
        ):
            result = add_trace_context(None, "info", {"event": "pad"})

        assert len(result["span_id"]) == 16


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    """Tests for the structlog configuration helper."""

    def test_configure_logging_does_not_crash(self) -> None:
        cfg = ObservabilityConfig()
        # Should complete without raising
        configure_logging(cfg)

    def test_configure_logging_with_otel_available(self) -> None:
        cfg = ObservabilityConfig(enable_logs=True)
        with patch("observability.logging._otel_available", return_value=True):
            configure_logging(cfg)
        # Verify structlog is configured (check that we can get a logger)
        log = structlog.get_logger()
        assert log is not None

    def test_configure_logging_with_logs_disabled(self) -> None:
        cfg = ObservabilityConfig(enable_logs=False)
        with patch("observability.logging._otel_available", return_value=True):
            configure_logging(cfg)
        # Should still work -- just no trace context processor
        log = structlog.get_logger()
        assert log is not None

    def test_json_output_format(self) -> None:
        cfg = ObservabilityConfig()
        configure_logging(cfg)

        # Capture output by creating a bound logger and rendering
        # The JSONRenderer should produce valid JSON
        renderer = structlog.processors.JSONRenderer()
        result = renderer(None, None, {"event": "test", "key": "value"})
        parsed = json.loads(result)
        assert parsed["event"] == "test"
        assert parsed["key"] == "value"

    def test_processor_chain_preserves_event(self) -> None:
        """The add_trace_context processor is additive -- never removes fields."""
        event: dict[str, Any] = {
            "event": "my-event",
            "user_id": "abc123",
            "request_id": "req-456",
        }
        with patch("observability.logging._otel_available", return_value=False):
            result = add_trace_context(None, "info", event)

        assert result["event"] == "my-event"
        assert result["user_id"] == "abc123"
        assert result["request_id"] == "req-456"


# ---------------------------------------------------------------------------
# ensure_console_handler (MET-646)
# ---------------------------------------------------------------------------


class TestEnsureConsoleHandler:
    """A console sink must always exist, independent of OTel state.

    Regression coverage for MET-646: on fidel-dev, application logs emitted
    after startup were invisible in ``docker logs`` because the only handler
    ever attached to the root logger was the OTel exporter -- and it silently
    swallows records when its collector is unreachable. These tests assert
    the fallback sink is always present and idempotent, not that OTel export
    itself works (that's covered by the bootstrap tests).
    """

    def setup_method(self) -> None:
        root = logging.getLogger()
        self._saved_handlers = list(root.handlers)
        self._saved_level = root.level
        for h in list(root.handlers):
            if isinstance(h, _ConsoleHandler):
                root.removeHandler(h)

    def teardown_method(self) -> None:
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, _ConsoleHandler):
                root.removeHandler(h)
        root.handlers[:] = self._saved_handlers
        root.setLevel(self._saved_level)

    def test_attaches_a_console_handler(self) -> None:
        root = logging.getLogger()
        assert not any(isinstance(h, _ConsoleHandler) for h in root.handlers)
        ensure_console_handler()
        assert any(isinstance(h, _ConsoleHandler) for h in root.handlers)

    def test_idempotent_does_not_stack_handlers(self) -> None:
        ensure_console_handler()
        ensure_console_handler()
        ensure_console_handler()
        root = logging.getLogger()
        assert sum(1 for h in root.handlers if isinstance(h, _ConsoleHandler)) == 1

    def test_lowers_root_level_to_info_if_stricter(self) -> None:
        root = logging.getLogger()
        root.setLevel(logging.ERROR)
        ensure_console_handler()
        assert root.level <= logging.INFO

    def test_does_not_raise_root_level_if_already_permissive(self) -> None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        ensure_console_handler()
        assert root.level == logging.DEBUG

    def test_configure_logging_always_wires_a_console_handler(self) -> None:
        """The real bug: OTel disabled must not mean zero output anywhere."""
        cfg = ObservabilityConfig(enable_logs=False)
        with patch("observability.logging._otel_available", return_value=False):
            configure_logging(cfg)
        root = logging.getLogger()
        assert any(isinstance(h, _ConsoleHandler) for h in root.handlers)
