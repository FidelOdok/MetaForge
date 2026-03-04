"""MetaForge Observability -- OTel bootstrap, config, and structlog bridge."""

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

__all__ = [
    "ObservabilityConfig",
    "ObservabilityState",
    "OtlpExporterConfig",
    "PrometheusConfig",
    "GrafanaConfig",
    "init_observability",
    "shutdown_observability",
    "add_trace_context",
    "configure_logging",
]
