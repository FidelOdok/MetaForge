"""Observability configuration models using Pydantic v2.

Defines configuration for OTLP exporters, Prometheus metrics,
Grafana dashboards, and the top-level ObservabilityConfig that
controls the entire observability stack.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OtlpExporterConfig(BaseModel):
    """Configuration for the OpenTelemetry OTLP exporter."""

    endpoint: str = "http://localhost:4317"
    insecure: bool = True
    timeout_ms: int = Field(default=5000, ge=0)


class PrometheusConfig(BaseModel):
    """Configuration for Prometheus metrics exposition."""

    port: int = Field(default=9464, ge=1, le=65535)
    scrape_interval: str = "15s"


class GrafanaConfig(BaseModel):
    """Configuration for Grafana dashboard access."""

    url: str = "http://localhost:3000"


class ObservabilityConfig(BaseModel):
    """Top-level observability configuration.

    Controls all three pillars of observability: traces, metrics, and logs.
    When ``enabled`` is False the entire observability stack is disabled
    regardless of individual pillar flags.
    """

    enabled: bool = True
    service_name: str = "metaforge"
    environment: str = "development"
    otlp: OtlpExporterConfig = Field(default_factory=OtlpExporterConfig)
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)
    grafana: GrafanaConfig = Field(default_factory=GrafanaConfig)
    trace_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    log_level: str = "INFO"
    enable_traces: bool = True
    enable_metrics: bool = True
    enable_logs: bool = True
