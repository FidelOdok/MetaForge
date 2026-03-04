"""MetaForge observability package.

Provides custom span helpers, metrics registry, W3C TraceContext propagation,
and .forge/traces/ JSONL enrichment for cross-boundary distributed tracing.
"""

from observability.metrics import MetricDefinition, MetricsCollector, MetricsRegistry
from observability.propagation import (
    extract_trace_context,
    inject_trace_context,
    produce_with_context,
)
from observability.trace_enrichment import enrich_trace_entry, get_current_trace_context
from observability.tracing import (
    SPAN_CATALOG,
    NoOpSpan,
    NoOpTracer,
    get_tracer,
    traced,
)

__all__ = [
    # tracing (MET-106)
    "SPAN_CATALOG",
    "NoOpSpan",
    "NoOpTracer",
    "get_tracer",
    "traced",
    # metrics (MET-107)
    "MetricDefinition",
    "MetricsCollector",
    "MetricsRegistry",
    # propagation (MET-109)
    "extract_trace_context",
    "inject_trace_context",
    "produce_with_context",
    # trace enrichment (MET-110)
    "enrich_trace_entry",
    "get_current_trace_context",
]
