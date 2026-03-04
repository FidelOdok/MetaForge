"""Enrich .forge/traces/ JSONL entries with W3C trace context (MET-110)."""

from __future__ import annotations

from typing import Any

try:
    from opentelemetry import trace as otel_trace

    HAS_OTEL = True
except ImportError:
    otel_trace = None  # type: ignore[assignment]
    HAS_OTEL = False


def get_current_trace_context() -> dict[str, str | None]:
    """Get current trace_id, span_id, parent_span_id from OTel context."""
    if not HAS_OTEL:
        return {"trace_id": None, "span_id": None, "parent_span_id": None}

    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
            "parent_span_id": None,  # parent available from span tree, not individual span
        }
    return {"trace_id": None, "span_id": None, "parent_span_id": None}


def enrich_trace_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Add trace context fields to a .forge/traces/ JSONL entry."""
    ctx = get_current_trace_context()
    enriched = dict(entry)
    enriched["trace_id"] = ctx["trace_id"]
    enriched["span_id"] = ctx["span_id"]
    enriched["parent_span_id"] = ctx["parent_span_id"]
    return enriched
