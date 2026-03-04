"""MET-101: Gateway Prometheus metrics registry and collector.

Defines metric descriptors as Pydantic models and provides a
``MetricsCollector`` that wraps OpenTelemetry meter instruments.  When the
OTel SDK is not installed the collector degrades to a silent no-op so the
rest of the platform can import it unconditionally.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Metric definition model
# ---------------------------------------------------------------------------

_METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class MetricDefinition(BaseModel):
    """Declarative description of a single Prometheus-style metric."""

    name: str
    type: str  # "counter", "histogram", "gauge"
    description: str
    labels: list[str]
    unit: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _METRIC_NAME_RE.match(v):
            raise ValueError(
                f"Metric name must be snake_case and start with a letter, got {v!r}"
            )
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        allowed = {"counter", "histogram", "gauge"}
        if v not in allowed:
            raise ValueError(f"Metric type must be one of {allowed}, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Registry of all MetaForge gateway metrics
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Registry of all MetaForge Prometheus metrics."""

    GATEWAY_REQUEST_TOTAL = MetricDefinition(
        name="metaforge_gateway_request_total",
        type="counter",
        description="Total HTTP requests to gateway",
        labels=["method", "endpoint", "status_code"],
    )

    GATEWAY_REQUEST_DURATION = MetricDefinition(
        name="metaforge_gateway_request_duration_seconds",
        type="histogram",
        description="HTTP request duration in seconds",
        labels=["method", "endpoint"],
        unit="s",
    )

    GATEWAY_WEBSOCKET_CONNECTIONS = MetricDefinition(
        name="metaforge_gateway_websocket_connections",
        type="gauge",
        description="Active WebSocket connections",
        labels=["state"],
    )

    GATEWAY_ACTIVE_SESSIONS = MetricDefinition(
        name="metaforge_gateway_active_sessions",
        type="gauge",
        description="Active user sessions",
        labels=["status"],
    )

    @classmethod
    def all_gateway_metrics(cls) -> list[MetricDefinition]:
        """Return every gateway metric definition."""
        return [
            cls.GATEWAY_REQUEST_TOTAL,
            cls.GATEWAY_REQUEST_DURATION,
            cls.GATEWAY_WEBSOCKET_CONNECTIONS,
            cls.GATEWAY_ACTIVE_SESSIONS,
        ]


# ---------------------------------------------------------------------------
# Metrics collector (wraps OTel meter or degrades to no-op)
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collects metrics using an OTel MeterProvider (or no-op if unavailable).

    Parameters
    ----------
    meter:
        An ``opentelemetry.metrics.Meter`` instance.  When *None* every
        recording method is a silent no-op, which lets callers use the
        collector unconditionally.
    """

    def __init__(self, meter: Any | None = None) -> None:
        self._meter = meter
        self._instruments: dict[str, Any] = {}

    # -- instrument creation ------------------------------------------------

    def create_instruments(self, definitions: list[MetricDefinition]) -> None:
        """Create OTel instruments from *definitions*.  No-op if no meter."""
        if self._meter is None:
            return

        for defn in definitions:
            if defn.type == "counter":
                self._instruments[defn.name] = self._meter.create_counter(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )
            elif defn.type == "histogram":
                self._instruments[defn.name] = self._meter.create_histogram(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )
            elif defn.type == "gauge":
                self._instruments[defn.name] = self._meter.create_up_down_counter(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )

    # -- convenience recorders ---------------------------------------------

    def record_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration: float,
    ) -> None:
        """Record an HTTP request (counter + histogram)."""
        counter = self._instruments.get(
            MetricsRegistry.GATEWAY_REQUEST_TOTAL.name
        )
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "method": method,
                    "endpoint": endpoint,
                    "status_code": str(status_code),
                },
            )

        histogram = self._instruments.get(
            MetricsRegistry.GATEWAY_REQUEST_DURATION.name
        )
        if histogram is not None:
            histogram.record(
                duration,
                attributes={"method": method, "endpoint": endpoint},
            )

    def set_websocket_connections(self, state: str, count: int) -> None:
        """Record the current number of WebSocket connections."""
        gauge = self._instruments.get(
            MetricsRegistry.GATEWAY_WEBSOCKET_CONNECTIONS.name
        )
        if gauge is not None:
            gauge.add(count, attributes={"state": state})

    def set_active_sessions(self, status: str, count: int) -> None:
        """Record the current number of active sessions."""
        gauge = self._instruments.get(
            MetricsRegistry.GATEWAY_ACTIVE_SESSIONS.name
        )
        if gauge is not None:
            gauge.add(count, attributes={"status": status})
