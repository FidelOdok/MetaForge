"""Unit tests for MET-101: Gateway Prometheus metrics.

Tests cover ``MetricDefinition`` validation, ``MetricsRegistry`` enumeration,
and ``MetricsCollector`` recording behaviour (with and without an OTel meter).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from observability.metrics import MetricDefinition, MetricsCollector, MetricsRegistry

# ===================================================================
# MetricDefinition
# ===================================================================


class TestMetricDefinition:
    """Tests for the ``MetricDefinition`` Pydantic model."""

    def test_create_counter(self) -> None:
        defn = MetricDefinition(
            name="my_counter",
            type="counter",
            description="A counter",
            labels=["label1"],
        )
        assert defn.name == "my_counter"
        assert defn.type == "counter"
        assert defn.labels == ["label1"]
        assert defn.unit == ""

    def test_create_histogram_with_unit(self) -> None:
        defn = MetricDefinition(
            name="my_histogram",
            type="histogram",
            description="A histogram",
            labels=["l1", "l2"],
            unit="s",
        )
        assert defn.unit == "s"

    def test_create_gauge(self) -> None:
        defn = MetricDefinition(
            name="my_gauge",
            type="gauge",
            description="A gauge",
            labels=[],
        )
        assert defn.type == "gauge"

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Metric type must be one of"):
            MetricDefinition(
                name="bad_type",
                type="summary",
                description="invalid",
                labels=[],
            )

    def test_invalid_name_uppercase_rejected(self) -> None:
        with pytest.raises(ValidationError, match="snake_case"):
            MetricDefinition(
                name="BadName",
                type="counter",
                description="invalid",
                labels=[],
            )

    def test_invalid_name_starts_with_digit_rejected(self) -> None:
        with pytest.raises(ValidationError, match="snake_case"):
            MetricDefinition(
                name="123_bad",
                type="counter",
                description="invalid",
                labels=[],
            )

    def test_name_with_underscores_accepted(self) -> None:
        defn = MetricDefinition(
            name="a_b_c_d",
            type="counter",
            description="ok",
            labels=[],
        )
        assert defn.name == "a_b_c_d"

    def test_empty_labels_accepted(self) -> None:
        defn = MetricDefinition(
            name="no_labels",
            type="counter",
            description="ok",
            labels=[],
        )
        assert defn.labels == []


# ===================================================================
# MetricsRegistry
# ===================================================================


class TestMetricsRegistry:
    """Tests for the ``MetricsRegistry`` class-level definitions."""

    def test_all_gateway_metrics_returns_four(self) -> None:
        metrics = MetricsRegistry.all_gateway_metrics()
        assert len(metrics) == 4

    def test_all_metrics_are_definitions(self) -> None:
        for m in MetricsRegistry.all_gateway_metrics():
            assert isinstance(m, MetricDefinition)

    def test_all_names_have_metaforge_prefix(self) -> None:
        for m in MetricsRegistry.all_gateway_metrics():
            assert m.name.startswith("metaforge_"), f"{m.name} missing prefix"

    def test_all_names_are_snake_case(self) -> None:
        import re

        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for m in MetricsRegistry.all_gateway_metrics():
            assert pattern.match(m.name), f"{m.name} is not snake_case"

    def test_request_total_is_counter(self) -> None:
        assert MetricsRegistry.GATEWAY_REQUEST_TOTAL.type == "counter"

    def test_request_duration_is_histogram(self) -> None:
        assert MetricsRegistry.GATEWAY_REQUEST_DURATION.type == "histogram"

    def test_websocket_connections_is_gauge(self) -> None:
        assert MetricsRegistry.GATEWAY_WEBSOCKET_CONNECTIONS.type == "gauge"

    def test_active_sessions_is_gauge(self) -> None:
        assert MetricsRegistry.GATEWAY_ACTIVE_SESSIONS.type == "gauge"

    def test_request_total_labels(self) -> None:
        assert MetricsRegistry.GATEWAY_REQUEST_TOTAL.labels == [
            "method",
            "endpoint",
            "status_code",
        ]

    def test_request_duration_unit(self) -> None:
        assert MetricsRegistry.GATEWAY_REQUEST_DURATION.unit == "s"


# ===================================================================
# MetricsCollector — no-op mode (no meter)
# ===================================================================


class TestMetricsCollectorNoOp:
    """When no meter is provided every method must silently no-op."""

    def test_init_without_meter(self) -> None:
        collector = MetricsCollector()
        assert collector._meter is None

    def test_create_instruments_noop(self) -> None:
        collector = MetricsCollector()
        collector.create_instruments(MetricsRegistry.all_gateway_metrics())
        assert collector._instruments == {}

    def test_record_request_noop(self) -> None:
        collector = MetricsCollector()
        # Should not raise
        collector.record_request("GET", "/health", 200, 0.05)

    def test_set_websocket_connections_noop(self) -> None:
        collector = MetricsCollector()
        collector.set_websocket_connections("open", 5)

    def test_set_active_sessions_noop(self) -> None:
        collector = MetricsCollector()
        collector.set_active_sessions("active", 3)


# ===================================================================
# MetricsCollector — with mocked meter
# ===================================================================


class TestMetricsCollectorWithMeter:
    """Verify that instruments are created and invoked correctly."""

    @pytest.fixture()
    def mock_meter(self) -> MagicMock:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        return meter

    @pytest.fixture()
    def collector(self, mock_meter: MagicMock) -> MetricsCollector:
        c = MetricsCollector(meter=mock_meter)
        c.create_instruments(MetricsRegistry.all_gateway_metrics())
        return c

    def test_create_instruments_populates_dict(self, collector: MetricsCollector) -> None:
        assert len(collector._instruments) == 4

    def test_counter_created(self, mock_meter: MagicMock, collector: MetricsCollector) -> None:
        mock_meter.create_counter.assert_called_once()

    def test_histogram_created(self, mock_meter: MagicMock, collector: MetricsCollector) -> None:
        mock_meter.create_histogram.assert_called_once()

    def test_gauges_created_as_up_down_counters(
        self, mock_meter: MagicMock, collector: MetricsCollector
    ) -> None:
        # Two gauge metrics -> two up_down_counter calls
        assert mock_meter.create_up_down_counter.call_count == 2

    def test_record_request_calls_counter_and_histogram(self, collector: MetricsCollector) -> None:
        collector.record_request("POST", "/v1/chat", 201, 0.123)
        counter = collector._instruments[MetricsRegistry.GATEWAY_REQUEST_TOTAL.name]
        counter.add.assert_called_once_with(
            1,
            attributes={
                "method": "POST",
                "endpoint": "/v1/chat",
                "status_code": "201",
            },
        )
        histogram = collector._instruments[MetricsRegistry.GATEWAY_REQUEST_DURATION.name]
        histogram.record.assert_called_once_with(
            0.123,
            attributes={"method": "POST", "endpoint": "/v1/chat"},
        )

    def test_set_websocket_connections_calls_gauge(self, collector: MetricsCollector) -> None:
        collector.set_websocket_connections("open", 7)
        gauge = collector._instruments[MetricsRegistry.GATEWAY_WEBSOCKET_CONNECTIONS.name]
        gauge.add.assert_called_once_with(7, attributes={"state": "open"})

    def test_set_active_sessions_calls_gauge(self, collector: MetricsCollector) -> None:
        collector.set_active_sessions("authenticated", 42)
        gauge = collector._instruments[MetricsRegistry.GATEWAY_ACTIVE_SESSIONS.name]
        gauge.add.assert_called_once_with(42, attributes={"status": "authenticated"})
