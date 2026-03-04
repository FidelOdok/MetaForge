"""W3C TraceContext propagation helpers for cross-boundary tracing (MET-109)."""

from __future__ import annotations

from typing import Any

try:
    from opentelemetry.propagate import extract, inject

    HAS_OTEL = True
except ImportError:
    inject = None  # type: ignore[assignment]
    extract = None  # type: ignore[assignment]
    HAS_OTEL = False


def inject_trace_context(carrier: dict[str, str]) -> dict[str, str]:
    """Inject current trace context into a carrier dict (HTTP headers, Kafka headers)."""
    if HAS_OTEL:
        inject(carrier)
    return carrier


def extract_trace_context(carrier: dict[str, str]) -> Any:
    """Extract trace context from a carrier dict, returns OTel context token."""
    if HAS_OTEL:
        return extract(carrier)
    return None


def produce_with_context(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Prepare Kafka message headers with trace context injected."""
    carrier: dict[str, str] = dict(headers) if headers else {}
    return inject_trace_context(carrier)
