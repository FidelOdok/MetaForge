"""Tests for Phase 3 observability features (MET-119 through MET-122).

Covers:
- MET-119: MQTT / telemetry pipeline metrics
- MET-120: Fleet health Grafana dashboard
- MET-121: Anomaly detection alerting rules
- MET-122: Incident runbooks for critical alerts
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest
import yaml

from observability.metrics import MetricDefinition, MetricsCollector, MetricsRegistry

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DASHBOARD_DIR = _REPO_ROOT / "observability" / "dashboards"
_ALERTING_DIR = _REPO_ROOT / "observability" / "alerting"
_RUNBOOK_DIR = _REPO_ROOT / "docs" / "runbooks"


# ═══════════════════════════════════════════════════════════════════════════
# MET-119 — Telemetry MetricDefinitions
# ═══════════════════════════════════════════════════════════════════════════


class TestTelemetryMetricDefinitions:
    """Each of the 5 telemetry MetricDefinitions must be valid."""

    @pytest.mark.parametrize(
        "metric_attr",
        [
            "MQTT_MESSAGES_RECEIVED_TOTAL",
            "TELEMETRY_ROUTER_DURATION",
            "TELEMETRY_INGESTION_TOTAL",
            "TELEMETRY_INGESTION_ERRORS_TOTAL",
            "TELEMETRY_LAG_SECONDS",
        ],
    )
    def test_telemetry_metric_is_valid_definition(self, metric_attr: str) -> None:
        defn = getattr(MetricsRegistry, metric_attr)
        assert isinstance(defn, MetricDefinition)

    def test_mqtt_messages_received_total(self) -> None:
        m = MetricsRegistry.MQTT_MESSAGES_RECEIVED_TOTAL
        assert m.name == "metaforge_mqtt_messages_received_total"
        assert m.type == "counter"
        assert set(m.labels) == {"device_id", "topic"}

    def test_telemetry_router_duration(self) -> None:
        m = MetricsRegistry.TELEMETRY_ROUTER_DURATION
        assert m.name == "metaforge_telemetry_router_duration_seconds"
        assert m.type == "histogram"
        assert "device_type" in m.labels
        assert m.unit == "s"

    def test_telemetry_ingestion_total(self) -> None:
        m = MetricsRegistry.TELEMETRY_INGESTION_TOTAL
        assert m.name == "metaforge_telemetry_ingestion_total"
        assert m.type == "counter"
        assert "status" in m.labels

    def test_telemetry_ingestion_errors_total(self) -> None:
        m = MetricsRegistry.TELEMETRY_INGESTION_ERRORS_TOTAL
        assert m.name == "metaforge_telemetry_ingestion_errors_total"
        assert m.type == "counter"
        assert "error_type" in m.labels

    def test_telemetry_lag_seconds(self) -> None:
        m = MetricsRegistry.TELEMETRY_LAG_SECONDS
        assert m.name == "metaforge_telemetry_lag_seconds"
        assert m.type == "gauge"
        assert "device_id" in m.labels
        assert m.unit == "s"


class TestTelemetryMetricsGroupAccess:
    """Grouped access methods for telemetry metrics."""

    def test_telemetry_metrics_returns_5(self) -> None:
        assert len(MetricsRegistry.telemetry_metrics()) == 5

    def test_all_metrics_returns_32(self) -> None:
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

    def test_telemetry_metrics_all_have_metaforge_prefix(self) -> None:
        for m in MetricsRegistry.telemetry_metrics():
            assert m.name.startswith("metaforge_")

    def test_telemetry_metrics_no_duplicates(self) -> None:
        names = [m.name for m in MetricsRegistry.telemetry_metrics()]
        assert len(names) == len(set(names))


# ═══════════════════════════════════════════════════════════════════════════
# MET-119 — MetricsCollector telemetry recording methods (no-op)
# ═══════════════════════════════════════════════════════════════════════════


class TestTelemetryCollectorNoOp:
    """Recording methods must be silent no-ops when no meter is given."""

    def test_record_mqtt_message_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_mqtt_message("dev-001", "sensors/temp")

    def test_record_telemetry_routing_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_telemetry_routing("temperature_sensor", 0.05)

    def test_record_telemetry_ingestion_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_telemetry_ingestion("success")

    def test_record_telemetry_error_noop(self) -> None:
        collector = MetricsCollector()
        collector.record_telemetry_error("malformed")

    def test_set_telemetry_lag_noop(self) -> None:
        collector = MetricsCollector()
        collector.set_telemetry_lag("dev-001", 3.2)


# ═══════════════════════════════════════════════════════════════════════════
# MET-119 — MetricsCollector telemetry recording methods (mocked meter)
# ═══════════════════════════════════════════════════════════════════════════


class TestTelemetryCollectorWithMeter:
    """When a mock meter is given, instruments should be created and called."""

    @pytest.fixture()
    def mock_meter(self) -> MagicMock:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        return meter

    def test_record_mqtt_message_calls_counter(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.telemetry_metrics())
        collector.record_mqtt_message("dev-001", "sensors/temp")
        counter = collector._instruments[
            MetricsRegistry.MQTT_MESSAGES_RECEIVED_TOTAL.name
        ]
        counter.add.assert_called_once_with(
            1, attributes={"device_id": "dev-001", "topic": "sensors/temp"}
        )

    def test_record_telemetry_routing_calls_histogram(
        self, mock_meter: MagicMock
    ) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.telemetry_metrics())
        collector.record_telemetry_routing("temperature_sensor", 0.042)
        hist = collector._instruments[
            MetricsRegistry.TELEMETRY_ROUTER_DURATION.name
        ]
        hist.record.assert_called_once_with(
            0.042, attributes={"device_type": "temperature_sensor"}
        )

    def test_record_telemetry_ingestion_calls_counter(
        self, mock_meter: MagicMock
    ) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.telemetry_metrics())
        collector.record_telemetry_ingestion("success")
        counter = collector._instruments[
            MetricsRegistry.TELEMETRY_INGESTION_TOTAL.name
        ]
        counter.add.assert_called_once_with(1, attributes={"status": "success"})

    def test_record_telemetry_error_calls_counter(
        self, mock_meter: MagicMock
    ) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.telemetry_metrics())
        collector.record_telemetry_error("write_failure")
        counter = collector._instruments[
            MetricsRegistry.TELEMETRY_INGESTION_ERRORS_TOTAL.name
        ]
        counter.add.assert_called_once_with(
            1, attributes={"error_type": "write_failure"}
        )

    def test_set_telemetry_lag_calls_gauge(self, mock_meter: MagicMock) -> None:
        collector = MetricsCollector(meter=mock_meter)
        collector.create_instruments(MetricsRegistry.telemetry_metrics())
        collector.set_telemetry_lag("dev-001", 3.2)
        gauge = collector._instruments[
            MetricsRegistry.TELEMETRY_LAG_SECONDS.name
        ]
        gauge.add.assert_called_once_with(
            3.2, attributes={"device_id": "dev-001"}
        )


# ═══════════════════════════════════════════════════════════════════════════
# MET-120 — Fleet health Grafana dashboard
# ═══════════════════════════════════════════════════════════════════════════


class TestFleetHealthDashboard:
    """Validate fleet-health.json structure and content."""

    @pytest.fixture()
    def dashboard(self) -> dict:
        path = _DASHBOARD_DIR / "fleet-health.json"
        return json.loads(path.read_text())

    def test_dashboard_file_exists(self) -> None:
        assert (_DASHBOARD_DIR / "fleet-health.json").is_file()

    def test_dashboard_is_valid_json(self) -> None:
        path = _DASHBOARD_DIR / "fleet-health.json"
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_dashboard_has_uid(self, dashboard: dict) -> None:
        assert dashboard["uid"] == "metaforge-fleet-health"

    def test_dashboard_has_title(self, dashboard: dict) -> None:
        assert "Fleet Health" in dashboard["title"]

    def test_dashboard_has_6_panels(self, dashboard: dict) -> None:
        assert len(dashboard["panels"]) == 6

    def test_panel_ids_are_unique(self, dashboard: dict) -> None:
        ids = [p["id"] for p in dashboard["panels"]]
        assert len(ids) == len(set(ids))

    def test_panel_1_is_device_count_stat(self, dashboard: dict) -> None:
        panel = dashboard["panels"][0]
        assert panel["type"] == "stat"
        assert "Device Count" in panel["title"]

    def test_panel_2_is_ingestion_rate_timeseries(self, dashboard: dict) -> None:
        panel = dashboard["panels"][1]
        assert panel["type"] == "timeseries"
        assert "Ingestion Rate" in panel["title"]

    def test_panel_3_is_anomaly_stat(self, dashboard: dict) -> None:
        panel = dashboard["panels"][2]
        assert panel["type"] == "stat"
        assert "Anomaly" in panel["title"]

    def test_panel_4_is_device_drilldown_table(self, dashboard: dict) -> None:
        panel = dashboard["panels"][3]
        assert panel["type"] == "table"
        assert "Drill-down" in panel["title"]

    def test_panel_5_is_mqtt_connection_timeseries(self, dashboard: dict) -> None:
        panel = dashboard["panels"][4]
        assert panel["type"] == "timeseries"
        assert "MQTT" in panel["title"]

    def test_panel_6_is_tsdb_write_histogram(self, dashboard: dict) -> None:
        panel = dashboard["panels"][5]
        assert panel["type"] == "histogram"
        assert "TSDB" in panel["title"] or "Write Latency" in panel["title"]

    def test_dashboard_has_device_group_template_variable(
        self, dashboard: dict
    ) -> None:
        variables = dashboard.get("templating", {}).get("list", [])
        names = [v["name"] for v in variables]
        assert "device_group" in names

    def test_all_panels_use_prometheus_datasource(self, dashboard: dict) -> None:
        for panel in dashboard["panels"]:
            ds = panel.get("datasource", {})
            if ds:  # alertlist panel may not have datasource
                assert ds.get("type") == "prometheus"


# ═══════════════════════════════════════════════════════════════════════════
# MET-121 — Anomaly detection alerting rules
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyAlertingRules:
    """Validate the metaforge_anomaly alert group in rules.yaml."""

    @pytest.fixture()
    def rules_data(self) -> dict:
        path = _ALERTING_DIR / "rules.yaml"
        return yaml.safe_load(path.read_text())

    @pytest.fixture()
    def anomaly_group(self, rules_data: dict) -> dict:
        for group in rules_data["groups"]:
            if group["name"] == "metaforge_anomaly":
                return group
        pytest.fail("metaforge_anomaly group not found in rules.yaml")

    def test_rules_yaml_is_valid(self) -> None:
        path = _ALERTING_DIR / "rules.yaml"
        data = yaml.safe_load(path.read_text())
        assert "groups" in data

    def test_anomaly_group_exists(self, rules_data: dict) -> None:
        group_names = [g["name"] for g in rules_data["groups"]]
        assert "metaforge_anomaly" in group_names

    def test_anomaly_group_has_5_rules(self, anomaly_group: dict) -> None:
        assert len(anomaly_group["rules"]) == 5

    def _get_rule(self, anomaly_group: dict, alert_name: str) -> dict:
        for rule in anomaly_group["rules"]:
            if rule["alert"] == alert_name:
                return rule
        pytest.fail(f"Rule {alert_name} not found in metaforge_anomaly group")

    def test_device_telemetry_stopped_rule(self, anomaly_group: dict) -> None:
        rule = self._get_rule(anomaly_group, "DeviceTelemetryStopped")
        assert rule["labels"]["severity"] == "critical"
        assert rule["for"] == "5m"
        assert "absent_over_time" in rule["expr"]

    def test_sensor_out_of_range_rule(self, anomaly_group: dict) -> None:
        rule = self._get_rule(anomaly_group, "SensorOutOfRange")
        assert rule["labels"]["severity"] == "warning"
        assert rule["for"] == "1m"

    def test_device_offline_rule(self, anomaly_group: dict) -> None:
        rule = self._get_rule(anomaly_group, "DeviceOffline")
        assert rule["labels"]["severity"] == "warning"
        assert rule["for"] == "2m"

    def test_fleet_anomaly_pattern_rule(self, anomaly_group: dict) -> None:
        rule = self._get_rule(anomaly_group, "FleetAnomalyPattern")
        assert rule["labels"]["severity"] == "critical"
        assert rule["for"] == "5m"
        assert "0.10" in str(rule["expr"]) or "0.1" in str(rule["expr"])

    def test_tsdb_ingestion_failing_rule(self, anomaly_group: dict) -> None:
        rule = self._get_rule(anomaly_group, "TSDBIngestionFailing")
        assert rule["labels"]["severity"] == "warning"
        assert rule["for"] == "5m"
        assert "0.01" in str(rule["expr"])

    def test_all_rules_have_annotations(self, anomaly_group: dict) -> None:
        for rule in anomaly_group["rules"]:
            assert "annotations" in rule
            assert "summary" in rule["annotations"]
            assert "description" in rule["annotations"]

    def test_existing_groups_still_present(self, rules_data: dict) -> None:
        group_names = [g["name"] for g in rules_data["groups"]]
        assert "metaforge_critical" in group_names
        assert "metaforge_warning" in group_names


# ═══════════════════════════════════════════════════════════════════════════
# MET-122 — Incident runbooks
# ═══════════════════════════════════════════════════════════════════════════

_RUNBOOK_FILES = [
    "gateway-down.md",
    "kafka-consumer-stopped.md",
    "neo4j-unreachable.md",
    "device-telemetry-stopped.md",
    "fleet-anomaly-pattern.md",
]

_REQUIRED_SECTIONS = [
    "Alert Description",
    "Severity",
    "Dashboard Links",
    "Diagnosis Checklist",
    "Resolution Procedures",
    "Escalation Path",
]


class TestIncidentRunbooks:
    """Validate that all runbooks exist and contain required sections."""

    @pytest.mark.parametrize("filename", _RUNBOOK_FILES)
    def test_runbook_file_exists(self, filename: str) -> None:
        path = _RUNBOOK_DIR / filename
        assert path.is_file(), f"Runbook {filename} does not exist"

    @pytest.mark.parametrize("filename", _RUNBOOK_FILES)
    def test_runbook_is_not_empty(self, filename: str) -> None:
        content = (_RUNBOOK_DIR / filename).read_text()
        assert len(content) > 200, f"Runbook {filename} is too short"

    @pytest.mark.parametrize("filename", _RUNBOOK_FILES)
    @pytest.mark.parametrize("section", _REQUIRED_SECTIONS)
    def test_runbook_has_required_section(
        self, filename: str, section: str
    ) -> None:
        content = (_RUNBOOK_DIR / filename).read_text()
        assert section in content, (
            f"Runbook {filename} is missing required section: {section}"
        )

    def test_gateway_down_runbook_references_alert(self) -> None:
        content = (_RUNBOOK_DIR / "gateway-down.md").read_text()
        assert "GatewayDown" in content

    def test_kafka_consumer_stopped_runbook_references_alert(self) -> None:
        content = (_RUNBOOK_DIR / "kafka-consumer-stopped.md").read_text()
        assert "KafkaConsumerStopped" in content

    def test_neo4j_unreachable_runbook_references_alert(self) -> None:
        content = (_RUNBOOK_DIR / "neo4j-unreachable.md").read_text()
        assert "Neo4jUnreachable" in content

    def test_device_telemetry_stopped_runbook_references_alert(self) -> None:
        content = (_RUNBOOK_DIR / "device-telemetry-stopped.md").read_text()
        assert "DeviceTelemetryStopped" in content

    def test_fleet_anomaly_pattern_runbook_references_alert(self) -> None:
        content = (_RUNBOOK_DIR / "fleet-anomaly-pattern.md").read_text()
        assert "FleetAnomalyPattern" in content
