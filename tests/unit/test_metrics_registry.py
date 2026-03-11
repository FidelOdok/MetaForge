"""Tests for observability.metrics (MET-107/MET-108): metrics registry and collector."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from observability.metrics import MetricDefinition, MetricsCollector, MetricsRegistry

# ── MetricDefinition validation ────────────────────────────────────────


class TestMetricDefinition:
    """Each MetricDefinition must be a valid Pydantic model."""

    def test_valid_counter_definition(self) -> None:
        defn = MetricDefinition(
            name="metaforge_test_total",
            type="counter",
            description="Test counter",
            labels=["label_a"],
        )
        assert defn.name == "metaforge_test_total"

    def test_valid_histogram_definition(self) -> None:
        defn = MetricDefinition(
            name="metaforge_test_duration_seconds",
            type="histogram",
            description="Test histogram",
            labels=["label_a"],
            unit="s",
            buckets=[0.1, 0.5, 1.0],
        )
        assert defn.buckets == [0.1, 0.5, 1.0]

    def test_valid_gauge_definition(self) -> None:
        defn = MetricDefinition(
            name="metaforge_test_gauge",
            type="gauge",
            description="Test gauge",
            labels=["label_a"],
        )
        assert defn.type == "gauge"


# ── MetricsRegistry grouped access ────────────────────────────────────


class TestMetricsRegistryGroupedAccess:
    """Grouped access methods return the correct count of metrics."""

    def test_all_metrics_returns_full_list(self) -> None:
        all_metrics = MetricsRegistry.all_metrics()
        # 4 gateway + 5 agent + 2 skill + 5 kafka + 7 datastore + 5 telemetry + 4 constraint
        assert len(all_metrics) == 32

    def test_gateway_metrics_returns_4(self) -> None:
        assert len(MetricsRegistry.gateway_metrics()) == 4

    def test_agent_metrics_returns_5(self) -> None:
        assert len(MetricsRegistry.agent_metrics()) == 5

    def test_skill_metrics_returns_2(self) -> None:
        assert len(MetricsRegistry.skill_metrics()) == 2

    def test_kafka_metrics_returns_5(self) -> None:
        assert len(MetricsRegistry.kafka_metrics()) == 5

    def test_all_metrics_equals_sum_of_groups(self) -> None:
        total = (
            len(MetricsRegistry.gateway_metrics())
            + len(MetricsRegistry.agent_metrics())
            + len(MetricsRegistry.skill_metrics())
            + len(MetricsRegistry.kafka_metrics())
            + len(MetricsRegistry.datastore_metrics())
            + len(MetricsRegistry.telemetry_metrics())
            + len(MetricsRegistry.constraint_metrics())
        )
        assert len(MetricsRegistry.all_metrics()) == total


# ── Naming convention validation ───────────────────────────────────────


_METRIC_NAME_RE = re.compile(r"^metaforge_[a-z][a-z0-9_]*$")


class TestMetricNamingConventions:
    """All metric names must follow metaforge_ prefix + snake_case."""

    def test_all_metric_names_have_metaforge_prefix(self) -> None:
        for metric in MetricsRegistry.all_metrics():
            assert metric.name.startswith("metaforge_"), (
                f"Metric {metric.name} must start with 'metaforge_'"
            )

    def test_all_metric_names_are_snake_case(self) -> None:
        for metric in MetricsRegistry.all_metrics():
            assert _METRIC_NAME_RE.match(metric.name), (
                f"Metric {metric.name} must be snake_case with 'metaforge_' prefix"
            )

    def test_no_duplicate_metric_names(self) -> None:
        names = [m.name for m in MetricsRegistry.all_metrics()]
        assert len(names) == len(set(names)), "Duplicate metric names detected"

    def test_all_types_are_valid(self) -> None:
        valid_types = {"counter", "histogram", "gauge"}
        for metric in MetricsRegistry.all_metrics():
            assert metric.type in valid_types, (
                f"Metric {metric.name} has invalid type '{metric.type}'"
            )

    def test_all_descriptions_non_empty(self) -> None:
        for metric in MetricsRegistry.all_metrics():
            assert metric.description, f"Metric {metric.name} must have a non-empty description"

    def test_all_labels_are_strings(self) -> None:
        for metric in MetricsRegistry.all_metrics():
            for label in metric.labels:
                assert isinstance(label, str), f"Label in {metric.name} must be a string"


# ── MetricsCollector with no meter (no-op) ─────────────────────────────


class TestMetricsCollectorNoOp:
    """When no meter is given all recording methods must be silent no-ops."""

    def test_create_instruments_with_no_meter(self) -> None:
        collector = MetricsCollector()
        collector.create_instruments(MetricsRegistry.all_metrics())
        assert collector._instruments == {}

    def test_record_request_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_request("GET", "/health", 200, 0.01)  # no raise

    def test_set_websocket_connections_noop(self) -> None:
        collector = MetricsCollector()
        collector.set_websocket_connections("active", 5)

    def test_set_active_sessions_noop(self) -> None:
        collector = MetricsCollector()
        collector.set_active_sessions("running", 3)

    def test_record_agent_execution_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_agent_execution("MECH", "success", 1.5)

    def test_record_llm_tokens_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_llm_tokens("MECH", "openai", "gpt-4", "prompt", 1000)

    def test_record_llm_cost_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_llm_cost("MECH", "openai", "gpt-4", 0.03)

    def test_record_skill_execution_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_skill_execution("validate_stress", "mechanical", "success", 2.0)

    def test_set_consumer_lag_noop(self) -> None:
        collector = MetricsCollector()
        collector.set_consumer_lag("group-1", "events", "0", 42)

    def test_record_message_produced_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_message_produced("events")

    def test_record_message_consumed_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_message_consumed("events", "group-1")

    def test_record_dead_letter_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_dead_letter("events", "group-1")


# ── MetricsCollector with mocked meter ─────────────────────────────────


class TestMetricsCollectorWithMeter:
    """When a mock meter is given instruments should be created and called."""

    @pytest.fixture()
    def mock_meter(self) -> MagicMock:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        return meter

    def test_create_instruments_creates_counters(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments([MetricsRegistry.AGENT_EXECUTION_TOTAL])
        mock_meter.create_counter.assert_called_once()

    def test_create_instruments_creates_histograms(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments([MetricsRegistry.AGENT_EXECUTION_DURATION])
        mock_meter.create_histogram.assert_called_once()

    def test_create_instruments_creates_gauges(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments([MetricsRegistry.KAFKA_CONSUMER_LAG])
        mock_meter.create_up_down_counter.assert_called_once()

    def test_record_agent_execution_calls_instruments(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.agent_metrics())
        collector.record_agent_execution("MECH", "success", 1.5)
        # Both counter and histogram should be called
        counter = collector._instruments[MetricsRegistry.AGENT_EXECUTION_TOTAL.name]
        counter.add.assert_called_once()
        hist = collector._instruments[MetricsRegistry.AGENT_EXECUTION_DURATION.name]
        hist.record.assert_called_once()

    def test_record_skill_execution_calls_instruments(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.skill_metrics())
        collector.record_skill_execution("validate_stress", "mechanical", "success", 2.0)
        counter = collector._instruments[MetricsRegistry.SKILL_EXECUTION_TOTAL.name]
        counter.add.assert_called_once()

    def test_record_llm_tokens_calls_counter(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.agent_metrics())
        collector.record_llm_tokens("MECH", "openai", "gpt-4", "prompt", 500)
        counter = collector._instruments[MetricsRegistry.AGENT_LLM_TOKENS_TOTAL.name]
        counter.add.assert_called_once_with(
            500,
            attributes={
                "agent_code": "MECH",
                "llm_provider": "openai",
                "llm_model": "gpt-4",
                "token_type": "prompt",
            },
        )

    def test_set_consumer_lag_calls_gauge(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.kafka_metrics())
        collector.set_consumer_lag("group-1", "events", "0", 42)
        gauge = collector._instruments[MetricsRegistry.KAFKA_CONSUMER_LAG.name]
        gauge.add.assert_called_once_with(
            42,
            attributes={"consumer_group": "group-1", "topic": "events", "partition": "0"},
        )

    def test_record_message_produced_calls_counter(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.kafka_metrics())
        collector.record_message_produced("events")
        counter = collector._instruments[MetricsRegistry.KAFKA_MESSAGES_PRODUCED.name]
        counter.add.assert_called_once_with(1, attributes={"topic": "events"})

    def test_record_message_consumed_calls_counter(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.kafka_metrics())
        collector.record_message_consumed("events", "group-1")
        counter = collector._instruments[MetricsRegistry.KAFKA_MESSAGES_CONSUMED.name]
        counter.add.assert_called_once_with(
            1, attributes={"topic": "events", "consumer_group": "group-1"}
        )

    def test_record_dead_letter_calls_counter(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.kafka_metrics())
        collector.record_dead_letter("events", "group-1")
        counter = collector._instruments[MetricsRegistry.KAFKA_DEAD_LETTERS.name]
        counter.add.assert_called_once_with(
            1, attributes={"topic": "events", "consumer_group": "group-1"}
        )
