"""MetaForge Observability.

OTel bootstrap, config, structlog bridge, metrics, tracing, and middleware.
"""

from observability.bootstrap import (
    ObservabilityState,
    init_observability,
    shutdown_observability,
)
from observability.config import (
    GrafanaConfig,
    ObservabilityConfig,
    OtlpExporterConfig,
    PrometheusConfig,
)
from observability.logging import add_trace_context, configure_logging
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
    # config (MET-99)
    "ObservabilityConfig",
    "ObservabilityState",
    "OtlpExporterConfig",
    "PrometheusConfig",
    "GrafanaConfig",
    "init_observability",
    "shutdown_observability",
    # logging (MET-100)
    "add_trace_context",
    "configure_logging",
    # metrics (MET-101, MET-107, MET-108)
    "MetricDefinition",
    "MetricsCollector",
    "MetricsRegistry",
    # tracing (MET-106)
    "SPAN_CATALOG",
    "NoOpSpan",
    "NoOpTracer",
    "get_tracer",
    "traced",
    # propagation (MET-109)
    "extract_trace_context",
    "inject_trace_context",
    "produce_with_context",
    # trace enrichment (MET-110)
    "enrich_trace_entry",
    "get_current_trace_context",
]
