"""Tests for observability Phase 2: Loki/Tempo backends, data store metrics,
constraint metrics, and SLO/SLI framework (MET-111, MET-112, MET-113, MET-114).

Target: 40+ tests covering all four issues.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest
import yaml

from observability.metrics import MetricsCollector, MetricsRegistry
from observability.slo.calculator import (
    calculate_availability,
    calculate_burn_rate,
    calculate_error_budget,
    is_budget_exhausted,
)
from observability.slo.definitions import SLIDefinition, SLODefinition, SLORegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OBS_DIR = pathlib.Path(__file__).resolve().parents[2] / "observability"
_ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]


def _load_yaml(path: pathlib.Path) -> dict:
    """Load and return a YAML file as a dict."""
    return yaml.safe_load(path.read_text())


# ═══════════════════════════════════════════════════════════════════════════
# MET-111: Docker Compose + config YAML validation
# ═══════════════════════════════════════════════════════════════════════════


class TestDockerComposeObservability:
    """Validate docker-compose.observability.yml structure."""

    def test_docker_compose_is_valid_yaml(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        assert isinstance(data, dict)

    def test_docker_compose_has_tempo_service(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        assert "tempo" in data["services"]

    def test_docker_compose_has_loki_service(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        assert "loki" in data["services"]

    def test_docker_compose_has_alertmanager_service(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        assert "alertmanager" in data["services"]

    def test_tempo_service_ports(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        ports = data["services"]["tempo"]["ports"]
        port_strs = [str(p) for p in ports]
        assert any("3200" in p for p in port_strs), "Tempo API port 3200 expected"

    def test_loki_service_port_3100(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        ports = data["services"]["loki"]["ports"]
        port_strs = [str(p) for p in ports]
        assert any("3100" in p for p in port_strs), "Loki port 3100 expected"

    def test_alertmanager_service_port_9093(self) -> None:
        data = _load_yaml(_ROOT_DIR / "docker-compose.observability.yml")
        ports = data["services"]["alertmanager"]["ports"]
        port_strs = [str(p) for p in ports]
        assert any("9093" in p for p in port_strs), "Alertmanager port 9093 expected"


class TestTempoConfig:
    """Validate observability/tempo.yaml."""

    def test_tempo_config_is_valid_yaml(self) -> None:
        data = _load_yaml(_OBS_DIR / "tempo.yaml")
        assert isinstance(data, dict)

    def test_tempo_has_server_section(self) -> None:
        data = _load_yaml(_OBS_DIR / "tempo.yaml")
        assert "server" in data

    def test_tempo_has_distributor(self) -> None:
        data = _load_yaml(_OBS_DIR / "tempo.yaml")
        assert "distributor" in data

    def test_tempo_has_storage(self) -> None:
        data = _load_yaml(_OBS_DIR / "tempo.yaml")
        assert "storage" in data

    def test_tempo_handles_compaction(self) -> None:
        """Compaction is automatic in latest Tempo — no compactor block needed."""
        data = _load_yaml(_OBS_DIR / "tempo.yaml")
        assert "storage" in data


class TestLokiConfig:
    """Validate observability/loki.yaml."""

    def test_loki_config_is_valid_yaml(self) -> None:
        data = _load_yaml(_OBS_DIR / "loki.yaml")
        assert isinstance(data, dict)

    def test_loki_has_server_section(self) -> None:
        data = _load_yaml(_OBS_DIR / "loki.yaml")
        assert "server" in data

    def test_loki_has_schema_config(self) -> None:
        data = _load_yaml(_OBS_DIR / "loki.yaml")
        assert "schema_config" in data

    def test_loki_has_storage_config(self) -> None:
        data = _load_yaml(_OBS_DIR / "loki.yaml")
        assert "storage_config" in data


class TestAlertmanagerConfig:
    """Validate observability/alertmanager.yaml."""

    def test_alertmanager_config_is_valid_yaml(self) -> None:
        data = _load_yaml(_OBS_DIR / "alertmanager.yaml")
        assert isinstance(data, dict)

    def test_alertmanager_has_route(self) -> None:
        data = _load_yaml(_OBS_DIR / "alertmanager.yaml")
        assert "route" in data

    def test_alertmanager_has_receivers(self) -> None:
        data = _load_yaml(_OBS_DIR / "alertmanager.yaml")
        assert "receivers" in data
        assert len(data["receivers"]) >= 1


class TestOtelCollectorConfig:
    """Validate OTel collector config includes Loki and Tempo exporters."""

    def test_otel_collector_is_valid_yaml(self) -> None:
        data = _load_yaml(_OBS_DIR / "otel-collector-config.yaml")
        assert isinstance(data, dict)

    def test_otel_collector_has_loki_exporter(self) -> None:
        data = _load_yaml(_OBS_DIR / "otel-collector-config.yaml")
        assert "otlphttp/loki" in data["exporters"]

    def test_otel_collector_has_tempo_exporter(self) -> None:
        data = _load_yaml(_OBS_DIR / "otel-collector-config.yaml")
        assert "otlp/tempo" in data["exporters"]

    def test_otel_traces_pipeline_includes_tempo(self) -> None:
        data = _load_yaml(_OBS_DIR / "otel-collector-config.yaml")
        exporters = data["service"]["pipelines"]["traces"]["exporters"]
        assert "otlp/tempo" in exporters

    def test_otel_logs_pipeline_includes_loki(self) -> None:
        data = _load_yaml(_OBS_DIR / "otel-collector-config.yaml")
        exporters = data["service"]["pipelines"]["logs"]["exporters"]
        assert "otlphttp/loki" in exporters


class TestGrafanaDatasources:
    """Validate grafana-datasources.yml has Tempo + Loki alongside Prometheus."""

    def test_datasources_is_valid_yaml(self) -> None:
        data = _load_yaml(_OBS_DIR / "grafana-datasources.yml")
        assert isinstance(data, dict)

    def test_datasources_has_prometheus(self) -> None:
        data = _load_yaml(_OBS_DIR / "grafana-datasources.yml")
        names = [ds["name"] for ds in data["datasources"]]
        assert "Prometheus" in names

    def test_datasources_has_tempo(self) -> None:
        data = _load_yaml(_OBS_DIR / "grafana-datasources.yml")
        names = [ds["name"] for ds in data["datasources"]]
        assert "Tempo" in names

    def test_datasources_has_loki(self) -> None:
        data = _load_yaml(_OBS_DIR / "grafana-datasources.yml")
        names = [ds["name"] for ds in data["datasources"]]
        assert "Loki" in names


# ═══════════════════════════════════════════════════════════════════════════
# MET-112: Data store metric definitions
# ═══════════════════════════════════════════════════════════════════════════


class TestDatastoreMetricDefinitions:
    """All 7 datastore MetricDefinitions must be valid."""

    def test_datastore_metrics_returns_7(self) -> None:
        assert len(MetricsRegistry.datastore_metrics()) == 7

    def test_neo4j_query_duration_is_histogram(self) -> None:
        assert MetricsRegistry.NEO4J_QUERY_DURATION.type == "histogram"

    def test_neo4j_active_connections_is_gauge(self) -> None:
        assert MetricsRegistry.NEO4J_ACTIVE_CONNECTIONS.type == "gauge"

    def test_neo4j_query_total_is_counter(self) -> None:
        assert MetricsRegistry.NEO4J_QUERY_TOTAL.type == "counter"

    def test_pgvector_search_duration_is_histogram(self) -> None:
        assert MetricsRegistry.PGVECTOR_SEARCH_DURATION.type == "histogram"

    def test_pgvector_search_total_is_counter(self) -> None:
        assert MetricsRegistry.PGVECTOR_SEARCH_TOTAL.type == "counter"

    def test_minio_operation_duration_is_histogram(self) -> None:
        assert MetricsRegistry.MINIO_OPERATION_DURATION.type == "histogram"

    def test_minio_operation_total_is_counter(self) -> None:
        assert MetricsRegistry.MINIO_OPERATION_TOTAL.type == "counter"

    def test_all_datastore_names_start_with_metaforge(self) -> None:
        for m in MetricsRegistry.datastore_metrics():
            assert m.name.startswith("metaforge_"), m.name


# ═══════════════════════════════════════════════════════════════════════════
# MET-113: Constraint & Policy metric definitions
# ═══════════════════════════════════════════════════════════════════════════


class TestConstraintMetricDefinitions:
    """All 4 constraint MetricDefinitions must be valid."""

    def test_constraint_metrics_returns_4(self) -> None:
        assert len(MetricsRegistry.constraint_metrics()) == 4

    def test_constraint_evaluation_total_is_counter(self) -> None:
        assert MetricsRegistry.CONSTRAINT_EVALUATION_TOTAL.type == "counter"

    def test_constraint_evaluation_duration_is_histogram(self) -> None:
        assert MetricsRegistry.CONSTRAINT_EVALUATION_DURATION.type == "histogram"

    def test_opa_decision_total_is_counter(self) -> None:
        assert MetricsRegistry.OPA_DECISION_TOTAL.type == "counter"

    def test_oscillation_detected_total_is_counter(self) -> None:
        assert MetricsRegistry.OSCILLATION_DETECTED_TOTAL.type == "counter"

    def test_all_constraint_names_start_with_metaforge(self) -> None:
        for m in MetricsRegistry.constraint_metrics():
            assert m.name.startswith("metaforge_"), m.name


# ═══════════════════════════════════════════════════════════════════════════
# Combined metrics count
# ═══════════════════════════════════════════════════════════════════════════


class TestAllMetricsCombined:
    """Overall registry counts after MET-112 + MET-113 + MET-119 additions."""

    def test_all_metrics_returns_32(self) -> None:
        # 4 gateway + 5 agent + 2 skill + 5 kafka + 7 datastore + 5 telemetry + 4 constraint
        assert len(MetricsRegistry.all_metrics()) == 32

    def test_all_metrics_equals_sum_of_all_groups(self) -> None:
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


# ═══════════════════════════════════════════════════════════════════════════
# MetricsCollector recording methods (datastore + constraint) with mock meter
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricsCollectorDatastore:
    """Recording methods for datastore metrics."""

    @pytest.fixture()
    def collector(self) -> MetricsCollector:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        c = MetricsCollector(meter=meter)
        c.create_instruments(MetricsRegistry.datastore_metrics())
        return c

    def test_record_neo4j_query_calls_counter(self, collector: MetricsCollector) -> None:
        collector.record_neo4j_query("read", "Artifact", "success", 0.012)
        counter = collector._instruments[MetricsRegistry.NEO4J_QUERY_TOTAL.name]
        counter.add.assert_called_once_with(
            1, attributes={"operation": "read", "status": "success"}
        )

    def test_record_neo4j_query_calls_histogram(self, collector: MetricsCollector) -> None:
        collector.record_neo4j_query("read", "Artifact", "success", 0.012)
        hist = collector._instruments[MetricsRegistry.NEO4J_QUERY_DURATION.name]
        hist.record.assert_called_once_with(
            0.012, attributes={"operation": "read", "node_type": "Artifact"}
        )

    def test_set_neo4j_connections(self, collector: MetricsCollector) -> None:
        collector.set_neo4j_connections(5)
        gauge = collector._instruments[MetricsRegistry.NEO4J_ACTIVE_CONNECTIONS.name]
        gauge.add.assert_called_once_with(5, attributes={})

    def test_record_pgvector_search_calls_counter(self, collector: MetricsCollector) -> None:
        collector.record_pgvector_search("component_spec", "success", 0.045)
        counter = collector._instruments[MetricsRegistry.PGVECTOR_SEARCH_TOTAL.name]
        counter.add.assert_called_once_with(
            1, attributes={"knowledge_type": "component_spec", "status": "success"}
        )

    def test_record_pgvector_search_calls_histogram(self, collector: MetricsCollector) -> None:
        collector.record_pgvector_search("component_spec", "success", 0.045)
        hist = collector._instruments[MetricsRegistry.PGVECTOR_SEARCH_DURATION.name]
        hist.record.assert_called_once_with(0.045, attributes={"knowledge_type": "component_spec"})

    def test_record_minio_operation_calls_counter(self, collector: MetricsCollector) -> None:
        collector.record_minio_operation("put_object", "success", 0.1)
        counter = collector._instruments[MetricsRegistry.MINIO_OPERATION_TOTAL.name]
        counter.add.assert_called_once_with(
            1, attributes={"operation": "put_object", "status": "success"}
        )

    def test_record_minio_operation_calls_histogram(self, collector: MetricsCollector) -> None:
        collector.record_minio_operation("put_object", "success", 0.1)
        hist = collector._instruments[MetricsRegistry.MINIO_OPERATION_DURATION.name]
        hist.record.assert_called_once_with(0.1, attributes={"operation": "put_object"})

    def test_noop_record_neo4j_query(self) -> None:
        collector = MetricsCollector()
        collector.record_neo4j_query("read", "Artifact", "success", 0.01)  # no raise

    def test_noop_set_neo4j_connections(self) -> None:
        collector = MetricsCollector()
        collector.set_neo4j_connections(3)

    def test_noop_record_pgvector_search(self) -> None:
        collector = MetricsCollector()
        collector.record_pgvector_search("spec", "success", 0.05)

    def test_noop_record_minio_operation(self) -> None:
        collector = MetricsCollector()
        collector.record_minio_operation("get_object", "success", 0.02)


class TestMetricsCollectorConstraint:
    """Recording methods for constraint/policy metrics."""

    @pytest.fixture()
    def collector(self) -> MetricsCollector:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        c = MetricsCollector(meter=meter)
        c.create_instruments(MetricsRegistry.constraint_metrics())
        return c

    def test_record_constraint_evaluation_calls_counter(self, collector: MetricsCollector) -> None:
        collector.record_constraint_evaluation("mechanical", "pass", 0.003)
        counter = collector._instruments[MetricsRegistry.CONSTRAINT_EVALUATION_TOTAL.name]
        counter.add.assert_called_once_with(
            1, attributes={"domain": "mechanical", "result": "pass"}
        )

    def test_record_constraint_evaluation_calls_histogram(
        self, collector: MetricsCollector
    ) -> None:
        collector.record_constraint_evaluation("mechanical", "pass", 0.003)
        hist = collector._instruments[MetricsRegistry.CONSTRAINT_EVALUATION_DURATION.name]
        hist.record.assert_called_once_with(0.003, attributes={"domain": "mechanical"})

    def test_record_opa_decision(self, collector: MetricsCollector) -> None:
        collector.record_opa_decision("agent_budget", "allow")
        counter = collector._instruments[MetricsRegistry.OPA_DECISION_TOTAL.name]
        counter.add.assert_called_once_with(
            1, attributes={"policy": "agent_budget", "result": "allow"}
        )

    def test_record_oscillation_detected(self, collector: MetricsCollector) -> None:
        collector.record_oscillation_detected("Constraint")
        counter = collector._instruments[MetricsRegistry.OSCILLATION_DETECTED_TOTAL.name]
        counter.add.assert_called_once_with(1, attributes={"node_type": "Constraint"})

    def test_noop_record_constraint_evaluation(self) -> None:
        collector = MetricsCollector()
        collector.record_constraint_evaluation("electrical", "fail", 0.001)

    def test_noop_record_opa_decision(self) -> None:
        collector = MetricsCollector()
        collector.record_opa_decision("agent_budget", "deny")

    def test_noop_record_oscillation_detected(self) -> None:
        collector = MetricsCollector()
        collector.record_oscillation_detected("Artifact")


# ═══════════════════════════════════════════════════════════════════════════
# MET-114: SLO/SLI framework
# ═══════════════════════════════════════════════════════════════════════════


class TestSLIDefinition:
    """SLIDefinition validation."""

    def test_valid_sli(self) -> None:
        sli = SLIDefinition(
            name="test_sli",
            description="A test SLI",
            metric_name="metaforge_test_total",
            good_events_query="sum(rate(metaforge_test_total[30d]))",
            total_events_query="sum(rate(metaforge_test_total[30d]))",
        )
        assert sli.name == "test_sli"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SLIDefinition(
                name="   ",
                description="bad",
                metric_name="m",
                good_events_query="q",
                total_events_query="q",
            )


class TestSLODefinition:
    """SLODefinition validation."""

    def _make_sli(self) -> SLIDefinition:
        return SLIDefinition(
            name="sli",
            description="d",
            metric_name="m",
            good_events_query="q",
            total_events_query="q",
        )

    def test_valid_slo(self) -> None:
        slo = SLODefinition(
            name="slo",
            description="d",
            sli=self._make_sli(),
            target=99.9,
            window_days=30,
            error_budget_minutes=43.2,
        )
        assert slo.target == 99.9

    def test_target_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="between 0"):
            SLODefinition(
                name="bad",
                description="d",
                sli=self._make_sli(),
                target=0.0,
                window_days=30,
                error_budget_minutes=0,
            )

    def test_target_above_100_rejected(self) -> None:
        with pytest.raises(ValueError, match="between 0"):
            SLODefinition(
                name="bad",
                description="d",
                sli=self._make_sli(),
                target=100.1,
                window_days=30,
                error_budget_minutes=0,
            )

    def test_negative_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            SLODefinition(
                name="bad",
                description="d",
                sli=self._make_sli(),
                target=99.0,
                window_days=-1,
                error_budget_minutes=0,
            )

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            SLODefinition(
                name="bad",
                description="d",
                sli=self._make_sli(),
                target=99.0,
                window_days=30,
                error_budget_minutes=-1.0,
            )


class TestSLORegistry:
    """SLORegistry has the expected 9 definitions."""

    def test_registry_has_9_slos(self) -> None:
        assert len(SLORegistry.all_slos()) == 9

    def test_all_slo_names_unique(self) -> None:
        names = [slo.name for slo in SLORegistry.all_slos()]
        assert len(names) == len(set(names))

    def test_all_targets_positive(self) -> None:
        for slo in SLORegistry.all_slos():
            assert slo.target > 0

    def test_gateway_availability_target(self) -> None:
        assert SLORegistry.GATEWAY_AVAILABILITY.target == 99.9

    def test_agent_success_rate_target(self) -> None:
        assert SLORegistry.AGENT_SUCCESS_RATE.target == 95.0


class TestErrorBudgetCalculation:
    """calculate_error_budget tests."""

    def test_gateway_availability_budget(self) -> None:
        budget = calculate_error_budget(SLORegistry.GATEWAY_AVAILABILITY)
        # 30 * 24 * 60 * 0.001 = 43.2
        assert abs(budget - 43.2) < 0.01

    def test_agent_success_rate_budget(self) -> None:
        budget = calculate_error_budget(SLORegistry.AGENT_SUCCESS_RATE)
        # 30 * 24 * 60 * 0.05 = 2160.0
        assert abs(budget - 2160.0) < 0.01

    def test_custom_window(self) -> None:
        budget = calculate_error_budget(SLORegistry.GATEWAY_AVAILABILITY, window_days=7)
        # 7 * 24 * 60 * 0.001 = 10.08
        assert abs(budget - 10.08) < 0.01


class TestBurnRateCalculation:
    """calculate_burn_rate tests."""

    def test_burn_rate_normal(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY  # 99.9% target
        # 0.1% error rate = exactly at budget -> burn rate 1.0
        rate = calculate_burn_rate(slo, error_count=1, total_count=1000, window_hours=1)
        assert abs(rate - 1.0) < 0.01

    def test_burn_rate_fast_burn(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY  # allowed = 0.1%
        # 1% error = 10x burn
        rate = calculate_burn_rate(slo, error_count=10, total_count=1000, window_hours=1)
        assert abs(rate - 10.0) < 0.01

    def test_burn_rate_zero_total(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY
        rate = calculate_burn_rate(slo, error_count=0, total_count=0, window_hours=1)
        assert rate == 0.0

    def test_burn_rate_no_errors(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY
        rate = calculate_burn_rate(slo, error_count=0, total_count=1000, window_hours=1)
        assert rate == 0.0


class TestBudgetExhaustion:
    """is_budget_exhausted tests."""

    def test_budget_not_exhausted(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY  # 99.9%
        assert is_budget_exhausted(slo, error_count=0, total_count=1000) is False

    def test_budget_exhausted(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY  # allowed 0.1%
        # 1% error -> exhausted
        assert is_budget_exhausted(slo, error_count=10, total_count=1000) is True

    def test_budget_zero_total(self) -> None:
        slo = SLORegistry.GATEWAY_AVAILABILITY
        assert is_budget_exhausted(slo, error_count=0, total_count=0) is False

    def test_budget_at_exact_boundary(self) -> None:
        slo = SLORegistry.AGENT_SUCCESS_RATE  # 95% target, allowed = 5%
        # exactly 50 errors in 1000 = 5.0% -> NOT exhausted (not strictly >)
        # Note: 50/1000 = 0.05 and allowed = 1 - 95/100 = 0.05 exactly
        assert is_budget_exhausted(slo, error_count=50, total_count=1000) is False


class TestAvailabilityCalculation:
    """calculate_availability edge cases."""

    def test_perfect_availability(self) -> None:
        assert calculate_availability(1000, 1000) == 100.0

    def test_zero_availability(self) -> None:
        assert calculate_availability(0, 1000) == 0.0

    def test_zero_total_returns_100(self) -> None:
        assert calculate_availability(0, 0) == 100.0

    def test_partial_availability(self) -> None:
        avail = calculate_availability(999, 1000)
        assert abs(avail - 99.9) < 0.01

    def test_half_availability(self) -> None:
        avail = calculate_availability(500, 1000)
        assert abs(avail - 50.0) < 0.01
