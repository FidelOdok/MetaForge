"""Tests for alerting rules, notification routing, and Grafana dashboards.

Covers:
- MET-115: Prometheus alerting rules (observability/alerting/rules.yaml)
- MET-116: Alertmanager notification routing (observability/alerting/routes.yaml)
- MET-117: Agent Performance + Data Stores dashboards
- MET-118: SLO Overview dashboard
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent.parent
_ALERTING_DIR = _ROOT / "observability" / "alerting"
_DASHBOARDS_DIR = _ROOT / "observability" / "dashboards"

_RULES_PATH = _ALERTING_DIR / "rules.yaml"
_ROUTES_PATH = _ALERTING_DIR / "routes.yaml"
_AGENT_PERF_PATH = _DASHBOARDS_DIR / "agent-performance.json"
_DATA_STORES_PATH = _DASHBOARDS_DIR / "data-stores.json"
_SLO_OVERVIEW_PATH = _DASHBOARDS_DIR / "slo-overview.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    """Load and return parsed YAML."""
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _load_json(path: Path) -> dict:
    """Load and return parsed JSON."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _all_alert_rules(data: dict) -> list[dict]:
    """Flatten all alert rules from all groups."""
    rules: list[dict] = []
    for group in data.get("groups", []):
        rules.extend(group.get("rules", []))
    return rules


# ===================================================================
# MET-115: Prometheus Alerting Rules
# ===================================================================


class TestAlertingRules:
    """Tests for observability/alerting/rules.yaml."""

    def test_rules_yaml_is_valid(self) -> None:
        """rules.yaml must be parseable by yaml.safe_load."""
        data = _load_yaml(_RULES_PATH)
        assert isinstance(data, dict)

    def test_rules_has_groups(self) -> None:
        """Top-level 'groups' key must exist."""
        data = _load_yaml(_RULES_PATH)
        assert "groups" in data
        assert len(data["groups"]) >= 2

    def test_critical_group_exists(self) -> None:
        data = _load_yaml(_RULES_PATH)
        group_names = [g["name"] for g in data["groups"]]
        assert "metaforge_critical" in group_names

    def test_warning_group_exists(self) -> None:
        data = _load_yaml(_RULES_PATH)
        group_names = [g["name"] for g in data["groups"]]
        assert "metaforge_warning" in group_names

    def test_total_alert_rules_count(self) -> None:
        """There must be exactly 13 alert rules in total (8 original + 5 anomaly)."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        assert len(rules) == 13

    def test_all_rules_have_required_fields(self) -> None:
        """Every alert rule must have alert, expr, for, labels.severity, annotations.summary."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        for rule in rules:
            assert "alert" in rule, f"Missing 'alert' in rule: {rule}"
            assert "expr" in rule, f"Missing 'expr' in {rule['alert']}"
            assert "for" in rule, f"Missing 'for' in {rule['alert']}"
            assert "labels" in rule, f"Missing 'labels' in {rule['alert']}"
            assert "severity" in rule["labels"], f"Missing 'severity' label in {rule['alert']}"
            assert "annotations" in rule, f"Missing 'annotations' in {rule['alert']}"
            assert "summary" in rule["annotations"], (
                f"Missing 'summary' annotation in {rule['alert']}"
            )

    def test_five_critical_rules(self) -> None:
        """There must be exactly 5 critical rules (3 original + 2 anomaly)."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        critical = [r for r in rules if r["labels"]["severity"] == "critical"]
        assert len(critical) == 5

    def test_eight_warning_rules(self) -> None:
        """There must be exactly 8 warning rules (5 original + 3 anomaly)."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        warnings = [r for r in rules if r["labels"]["severity"] == "warning"]
        assert len(warnings) == 8

    def test_critical_rule_names(self) -> None:
        """Verify the names of all critical alert rules."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        critical_names = sorted(r["alert"] for r in rules if r["labels"]["severity"] == "critical")
        assert critical_names == sorted(
            [
                "DeviceTelemetryStopped",
                "FleetAnomalyPattern",
                "GatewayDown",
                "KafkaConsumerStopped",
                "Neo4jUnreachable",
            ]
        )

    def test_warning_rule_names(self) -> None:
        """Verify the names of all warning alert rules."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        warning_names = sorted(r["alert"] for r in rules if r["labels"]["severity"] == "warning")
        assert warning_names == sorted(
            [
                "DeviceOffline",
                "ErrorBudgetBurnRate",
                "HighAgentFailureRate",
                "KafkaConsumerLagHigh",
                "LLMCostSpike",
                "OscillationDetected",
                "SensorOutOfRange",
                "TSDBIngestionFailing",
            ]
        )

    def test_gateway_down_has_runbook_url(self) -> None:
        """GatewayDown must include a runbook_url annotation."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        gw_down = [r for r in rules if r["alert"] == "GatewayDown"][0]
        assert "runbook_url" in gw_down["annotations"]
        assert gw_down["annotations"]["runbook_url"].startswith("https://")

    def test_all_rules_have_description_annotation(self) -> None:
        """Every alert rule should have a description annotation."""
        data = _load_yaml(_RULES_PATH)
        rules = _all_alert_rules(data)
        for rule in rules:
            assert "description" in rule["annotations"], (
                f"Missing 'description' annotation in {rule['alert']}"
            )


# ===================================================================
# MET-116: Alertmanager Notification Routing
# ===================================================================


class TestAlertmanagerRoutes:
    """Tests for observability/alerting/routes.yaml."""

    def test_routes_yaml_is_valid(self) -> None:
        """routes.yaml must be parseable by yaml.safe_load."""
        data = _load_yaml(_ROUTES_PATH)
        assert isinstance(data, dict)

    def test_top_level_route_exists(self) -> None:
        data = _load_yaml(_ROUTES_PATH)
        assert "route" in data

    def test_receivers_defined(self) -> None:
        """All 5 receivers must be defined."""
        data = _load_yaml(_ROUTES_PATH)
        assert "receivers" in data
        receiver_names = [r["name"] for r in data["receivers"]]
        expected = [
            "default-slack",
            "pagerduty-critical",
            "slack-critical",
            "slack-warning",
            "slack-info",
        ]
        for name in expected:
            assert name in receiver_names, f"Missing receiver: {name}"

    def test_critical_routes_to_pagerduty(self) -> None:
        """Critical alerts should route to pagerduty-critical."""
        data = _load_yaml(_ROUTES_PATH)
        routes = data["route"]["routes"]
        critical_routes = [r for r in routes if r.get("match", {}).get("severity") == "critical"]
        pagerduty_route = [r for r in critical_routes if r["receiver"] == "pagerduty-critical"]
        assert len(pagerduty_route) == 1

    def test_critical_routes_to_slack(self) -> None:
        """Critical alerts should also route to slack-critical."""
        data = _load_yaml(_ROUTES_PATH)
        routes = data["route"]["routes"]
        critical_routes = [r for r in routes if r.get("match", {}).get("severity") == "critical"]
        slack_route = [r for r in critical_routes if r["receiver"] == "slack-critical"]
        assert len(slack_route) == 1

    def test_warning_routes_to_slack(self) -> None:
        """Warning alerts should route to slack-warning."""
        data = _load_yaml(_ROUTES_PATH)
        routes = data["route"]["routes"]
        warning_routes = [r for r in routes if r.get("match", {}).get("severity") == "warning"]
        assert any(r["receiver"] == "slack-warning" for r in warning_routes)

    def test_info_routes_to_slack(self) -> None:
        """Info alerts should route to slack-info."""
        data = _load_yaml(_ROUTES_PATH)
        routes = data["route"]["routes"]
        info_routes = [r for r in routes if r.get("match", {}).get("severity") == "info"]
        assert any(r["receiver"] == "slack-info" for r in info_routes)

    def test_inhibit_rules_exist(self) -> None:
        """Inhibit rules must be defined."""
        data = _load_yaml(_ROUTES_PATH)
        assert "inhibit_rules" in data
        assert len(data["inhibit_rules"]) >= 1

    def test_inhibit_critical_suppresses_warning(self) -> None:
        """Critical alerts should suppress warning alerts for same alertname."""
        data = _load_yaml(_ROUTES_PATH)
        inhibit = data["inhibit_rules"][0]
        assert inhibit["source_match"]["severity"] == "critical"
        assert inhibit["target_match"]["severity"] == "warning"
        assert "alertname" in inhibit["equal"]

    def test_pagerduty_continue_flag(self) -> None:
        """The first critical route (pagerduty) must have continue: true."""
        data = _load_yaml(_ROUTES_PATH)
        routes = data["route"]["routes"]
        pagerduty_route = [
            r
            for r in routes
            if r.get("match", {}).get("severity") == "critical"
            and r["receiver"] == "pagerduty-critical"
        ]
        assert len(pagerduty_route) == 1
        assert pagerduty_route[0].get("continue") is True

    def test_default_receiver(self) -> None:
        """The top-level route must have a default receiver."""
        data = _load_yaml(_ROUTES_PATH)
        assert data["route"]["receiver"] == "default-slack"

    def test_group_by_includes_alertname(self) -> None:
        """The top-level route must group by alertname."""
        data = _load_yaml(_ROUTES_PATH)
        assert "alertname" in data["route"]["group_by"]


# ===================================================================
# MET-117: Agent Performance Dashboard
# ===================================================================


class TestAgentPerformanceDashboard:
    """Tests for observability/dashboards/agent-performance.json."""

    def test_json_is_valid(self) -> None:
        """Dashboard JSON must be parseable."""
        data = _load_json(_AGENT_PERF_PATH)
        assert isinstance(data, dict)

    def test_has_correct_title(self) -> None:
        data = _load_json(_AGENT_PERF_PATH)
        assert data["title"] == "MetaForge Agent Performance"

    def test_has_seven_panels(self) -> None:
        data = _load_json(_AGENT_PERF_PATH)
        assert len(data["panels"]) == 7

    def test_has_agent_code_template_variable(self) -> None:
        """Dashboard must have an agent_code template variable."""
        data = _load_json(_AGENT_PERF_PATH)
        assert "templating" in data
        var_names = [v["name"] for v in data["templating"]["list"]]
        assert "agent_code" in var_names

    def test_panels_have_datasource(self) -> None:
        """All panels must reference the Prometheus datasource."""
        data = _load_json(_AGENT_PERF_PATH)
        for panel in data["panels"]:
            assert "datasource" in panel, f"Panel '{panel['title']}' missing datasource"
            ds = panel["datasource"]
            assert ds["uid"] == "${DS_PROMETHEUS}", (
                f"Panel '{panel['title']}' has wrong datasource uid"
            )

    def test_panel_titles(self) -> None:
        """Verify all 7 panel titles are present."""
        data = _load_json(_AGENT_PERF_PATH)
        titles = [p["title"] for p in data["panels"]]
        expected = [
            "Agent Success Rate",
            "Execution Duration Heatmap",
            "LLM Token Usage",
            "LLM Cost Over Time",
            "Skill Execution Breakdown",
            "MCP Tool Call Latency",
            "Agent Trace Explorer",
        ]
        for title in expected:
            assert title in titles, f"Missing panel: {title}"

    def test_has_uid(self) -> None:
        data = _load_json(_AGENT_PERF_PATH)
        assert "uid" in data
        assert data["uid"] == "metaforge-agent-performance"


# ===================================================================
# MET-117: Data Stores Dashboard
# ===================================================================


class TestDataStoresDashboard:
    """Tests for observability/dashboards/data-stores.json."""

    def test_json_is_valid(self) -> None:
        """Dashboard JSON must be parseable."""
        data = _load_json(_DATA_STORES_PATH)
        assert isinstance(data, dict)

    def test_has_correct_title(self) -> None:
        data = _load_json(_DATA_STORES_PATH)
        assert data["title"] == "MetaForge Data Stores"

    def test_has_seven_panels(self) -> None:
        data = _load_json(_DATA_STORES_PATH)
        assert len(data["panels"]) == 7

    def test_panels_have_datasource(self) -> None:
        """All panels must reference the Prometheus datasource."""
        data = _load_json(_DATA_STORES_PATH)
        for panel in data["panels"]:
            assert "datasource" in panel, f"Panel '{panel['title']}' missing datasource"
            ds = panel["datasource"]
            assert ds["uid"] == "${DS_PROMETHEUS}", (
                f"Panel '{panel['title']}' has wrong datasource uid"
            )

    def test_panel_titles(self) -> None:
        """Verify all 7 panel titles are present."""
        data = _load_json(_DATA_STORES_PATH)
        titles = [p["title"] for p in data["panels"]]
        expected = [
            "Neo4j Query Latency",
            "Neo4j Operations",
            "pgvector Search Latency",
            "Kafka Consumer Lag",
            "Kafka Throughput",
            "Dead Letter Queue",
            "MinIO Operations",
        ]
        for title in expected:
            assert title in titles, f"Missing panel: {title}"

    def test_has_templating_variable(self) -> None:
        """Data stores dashboard should have a consumer_group variable."""
        data = _load_json(_DATA_STORES_PATH)
        assert "templating" in data
        var_names = [v["name"] for v in data["templating"]["list"]]
        assert "consumer_group" in var_names

    def test_has_uid(self) -> None:
        data = _load_json(_DATA_STORES_PATH)
        assert "uid" in data
        assert data["uid"] == "metaforge-data-stores"


# ===================================================================
# MET-118: SLO Overview Dashboard
# ===================================================================


class TestSLOOverviewDashboard:
    """Tests for observability/dashboards/slo-overview.json."""

    def test_json_is_valid(self) -> None:
        """Dashboard JSON must be parseable."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        assert isinstance(data, dict)

    def test_has_correct_title(self) -> None:
        data = _load_json(_SLO_OVERVIEW_PATH)
        assert data["title"] == "MetaForge SLO Overview"

    def test_has_six_panels(self) -> None:
        data = _load_json(_SLO_OVERVIEW_PATH)
        assert len(data["panels"]) == 6

    def test_panels_have_datasource(self) -> None:
        """All panels must reference the Prometheus datasource."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        for panel in data["panels"]:
            assert "datasource" in panel, f"Panel '{panel['title']}' missing datasource"
            ds = panel["datasource"]
            assert ds["uid"] == "${DS_PROMETHEUS}", (
                f"Panel '{panel['title']}' has wrong datasource uid"
            )

    def test_panel_titles(self) -> None:
        """Verify all 6 panel titles are present."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        titles = [p["title"] for p in data["panels"]]
        expected = [
            "Gateway Availability (30d)",
            "Gateway Latency p99",
            "Agent Success Rate (30d)",
            "Error Budget Remaining",
            "Error Budget Burn Rate",
            "SLO Compliance History",
        ]
        for title in expected:
            assert title in titles, f"Missing panel: {title}"

    def test_has_uid(self) -> None:
        data = _load_json(_SLO_OVERVIEW_PATH)
        assert "uid" in data
        assert data["uid"] == "metaforge-slo-overview"

    def test_availability_stat_panel_type(self) -> None:
        """Gateway Availability should be a stat panel."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        panel = [p for p in data["panels"] if "Availability" in p["title"]][0]
        assert panel["type"] == "stat"

    def test_latency_stat_panel_type(self) -> None:
        """Gateway Latency p99 should be a stat panel."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        panel = [p for p in data["panels"] if "Latency" in p["title"]][0]
        assert panel["type"] == "stat"

    def test_error_budget_gauge_panel_type(self) -> None:
        """Error Budget Remaining should be a gauge panel."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        panel = [p for p in data["panels"] if "Error Budget Remaining" in p["title"]][0]
        assert panel["type"] == "gauge"

    def test_burn_rate_timeseries_panel_type(self) -> None:
        """Error Budget Burn Rate should be a timeseries panel."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        panel = [p for p in data["panels"] if "Burn Rate" in p["title"]][0]
        assert panel["type"] == "timeseries"

    def test_compliance_table_panel_type(self) -> None:
        """SLO Compliance History should be a table panel."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        panel = [p for p in data["panels"] if "Compliance" in p["title"]][0]
        assert panel["type"] == "table"

    def test_30d_time_range(self) -> None:
        """SLO Overview dashboard should default to a 30-day time range."""
        data = _load_json(_SLO_OVERVIEW_PATH)
        assert data["time"]["from"] == "now-30d"
