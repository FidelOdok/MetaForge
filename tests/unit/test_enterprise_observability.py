"""Tests for enterprise observability features (MET-123 through MET-126).

Covers:
- Per-tenant metric isolation (MET-123)
- Simulation performance tracking (MET-124)
- LLM cost attribution per project/team (MET-125)
- Enterprise audit log export (MET-126)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from observability.audit.integrity import AuditIntegrity
from observability.audit.logger import AuditLogger
from observability.audit.models import (
    AuditEvent,
    AuditEventType,
    ExportConfig,
    ExportDestination,
)
from observability.cost_attribution import CostAttributionTracker, CostRecord
from observability.simulation_metrics import SimulationCollector, SimulationMetrics
from observability.tenant_isolation import (
    TenantContext,
    TenantMetricInjector,
    TenantRBAC,
)


class TestTenantContext:
    """TenantContext Pydantic model validation."""

    def test_valid_tenant_context(self) -> None:
        ctx = TenantContext(tenant_id="acme", tenant_name="Acme Corp", plan="pro")
        assert ctx.tenant_id == "acme"
        assert ctx.tenant_name == "Acme Corp"
        assert ctx.plan == "pro"

    def test_all_valid_plans(self) -> None:
        for plan in ("free", "pro", "enterprise"):
            ctx = TenantContext(tenant_id="t1", tenant_name="T", plan=plan)
            assert ctx.plan == plan

    def test_invalid_plan(self) -> None:
        with pytest.raises(ValidationError):
            TenantContext(tenant_id="t1", tenant_name="T", plan="platinum")

    def test_empty_tenant_id(self) -> None:
        with pytest.raises(ValidationError):
            TenantContext(tenant_id="", tenant_name="T", plan="free")

    def test_whitespace_tenant_id(self) -> None:
        with pytest.raises(ValidationError):
            TenantContext(tenant_id="   ", tenant_name="T", plan="free")

    def test_empty_tenant_name(self) -> None:
        with pytest.raises(ValidationError):
            TenantContext(tenant_id="t1", tenant_name="", plan="free")


class TestTenantMetricInjector:
    """TenantMetricInjector label injection and filter generation."""

    def test_inject_tenant_labels_adds_tenant_id(self) -> None:
        ctx = TenantContext(tenant_id="acme", tenant_name="Acme", plan="pro")
        attrs = {"method": "GET", "endpoint": "/api/v1/twin"}
        result = TenantMetricInjector.inject_tenant_labels(attrs, ctx)
        assert result["tenant_id"] == "acme"
        assert result["method"] == "GET"
        assert result["endpoint"] == "/api/v1/twin"

    def test_inject_does_not_mutate_original(self) -> None:
        ctx = TenantContext(tenant_id="t1", tenant_name="T", plan="free")
        attrs = {"key": "val"}
        result = TenantMetricInjector.inject_tenant_labels(attrs, ctx)
        assert "tenant_id" not in attrs
        assert "tenant_id" in result

    def test_inject_empty_attributes(self) -> None:
        ctx = TenantContext(tenant_id="t1", tenant_name="T", plan="free")
        result = TenantMetricInjector.inject_tenant_labels({}, ctx)
        assert result == {"tenant_id": "t1"}

    def test_get_tenant_dashboard_filter(self) -> None:
        f = TenantMetricInjector.get_tenant_dashboard_filter("acme-corp")
        assert f == 'tenant_id="acme-corp"'

    def test_get_tenant_alert_matcher(self) -> None:
        m = TenantMetricInjector.get_tenant_alert_matcher("acme-corp")
        assert m == {"tenant_id": "acme-corp"}


class TestTenantRBAC:
    """TenantRBAC permission checks."""

    def test_can_view_own_metrics(self) -> None:
        assert TenantRBAC.can_view_metrics("acme", "acme") is True

    def test_cannot_view_other_tenant_metrics(self) -> None:
        assert TenantRBAC.can_view_metrics("acme", "globex") is False

    def test_admin_can_view_any_tenant(self) -> None:
        assert TenantRBAC.can_view_metrics("__admin__", "acme") is True
        assert TenantRBAC.can_view_metrics("__admin__", "globex") is True

    def test_get_visible_tenants_regular_user(self) -> None:
        visible = TenantRBAC.get_visible_tenants("acme", is_admin=False)
        assert visible == ["acme"]

    def test_get_visible_tenants_admin(self) -> None:
        all_ids = ["acme", "globex", "initech"]
        visible = TenantRBAC.get_visible_tenants(
            "__admin__", is_admin=True, all_tenant_ids=all_ids
        )
        assert set(visible) == {"acme", "globex", "initech"}

    def test_get_visible_tenants_admin_no_list(self) -> None:
        visible = TenantRBAC.get_visible_tenants("__admin__", is_admin=True)
        assert visible == []

    def test_regular_user_ignores_all_tenant_ids(self) -> None:
        visible = TenantRBAC.get_visible_tenants(
            "acme", is_admin=False, all_tenant_ids=["acme", "globex"]
        )
        assert visible == ["acme"]


# ---------------------------------------------------------------------------
# MET-123: Tenant variables dashboard JSON
# ---------------------------------------------------------------------------


class TestTenantVariablesDashboard:
    """Validate the tenant-variables.json dashboard file."""

    @pytest.fixture()
    def dashboard(self) -> dict:
        path = (
            Path(__file__).resolve().parents[2]
            / "observability"
            / "dashboards"
            / "tenant-variables.json"
        )
        return json.loads(path.read_text())

    def test_dashboard_is_valid_json(self, dashboard: dict) -> None:
        assert isinstance(dashboard, dict)

    def test_has_uid(self, dashboard: dict) -> None:
        assert dashboard["uid"] == "metaforge-tenant-variables"

    def test_has_tenant_id_template_variable(self, dashboard: dict) -> None:
        variables = dashboard["templating"]["list"]
        assert len(variables) >= 1
        tenant_var = variables[0]
        assert tenant_var["name"] == "tenant_id"
        assert tenant_var["type"] == "query"

    def test_has_panels(self, dashboard: dict) -> None:
        assert len(dashboard["panels"]) >= 1


# ---------------------------------------------------------------------------
# MET-124: Simulation metrics
# ---------------------------------------------------------------------------


class TestSimulationMetrics:
    """SimulationMetrics definitions."""

    def test_all_metrics_returns_five(self) -> None:
        metrics = SimulationMetrics.all_metrics()
        assert len(metrics) == 5

    def test_duration_is_histogram(self) -> None:
        assert SimulationMetrics.SIMULATION_DURATION.type == "histogram"

    def test_total_is_counter(self) -> None:
        assert SimulationMetrics.SIMULATION_TOTAL.type == "counter"

    def test_cpu_is_counter(self) -> None:
        assert SimulationMetrics.SIMULATION_RESOURCE_CPU.type == "counter"

    def test_memory_is_gauge(self) -> None:
        assert SimulationMetrics.SIMULATION_RESOURCE_MEMORY.type == "gauge"

    def test_accuracy_is_gauge(self) -> None:
        assert SimulationMetrics.SIMULATION_ACCURACY.type == "gauge"

    def test_all_names_start_with_metaforge(self) -> None:
        for m in SimulationMetrics.all_metrics():
            assert m.name.startswith("metaforge_simulation_")

    def test_labels_are_lists(self) -> None:
        for m in SimulationMetrics.all_metrics():
            assert isinstance(m.labels, list)


class TestSimulationCollector:
    """SimulationCollector with a mock meter."""

    @pytest.fixture()
    def mock_meter(self) -> MagicMock:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        return meter

    def test_create_instruments_with_meter(self, mock_meter: MagicMock) -> None:
        collector = SimulationCollector(meter=mock_meter)
        collector.create_instruments()
        assert mock_meter.create_counter.call_count >= 1
        assert mock_meter.create_histogram.call_count >= 1

    def test_record_simulation(self, mock_meter: MagicMock) -> None:
        collector = SimulationCollector(meter=mock_meter)
        collector.create_instruments()
        collector.record_simulation("fea", "calculix", "success", 12.5)
        # Histogram and counter should be called
        hist = mock_meter.create_histogram.return_value
        hist.record.assert_called()
        ctr = mock_meter.create_counter.return_value
        ctr.add.assert_called()

    def test_record_resource_usage(self, mock_meter: MagicMock) -> None:
        collector = SimulationCollector(meter=mock_meter)
        collector.create_instruments()
        collector.record_resource_usage("fea", 120.0, 1024 * 1024)
        # CPU counter + memory gauge
        ctr = mock_meter.create_counter.return_value
        ctr.add.assert_called()

    def test_record_accuracy(self, mock_meter: MagicMock) -> None:
        collector = SimulationCollector(meter=mock_meter)
        collector.create_instruments()
        collector.record_accuracy("fea", "v1.0", 0.95)
        gauge = mock_meter.create_up_down_counter.return_value
        gauge.add.assert_called()

    def test_noop_without_meter(self) -> None:
        collector = SimulationCollector(meter=None)
        collector.create_instruments()
        # Should not raise
        collector.record_simulation("fea", "calculix", "success", 10.0)
        collector.record_resource_usage("fea", 60.0, 512)
        collector.record_accuracy("fea", "v1.0", 0.9)

    def test_create_instruments_noop_without_meter(self) -> None:
        collector = SimulationCollector(meter=None)
        collector.create_instruments()
        assert len(collector._instruments) == 0


# ---------------------------------------------------------------------------
# MET-125: Cost attribution
# ---------------------------------------------------------------------------


class TestCostRecord:
    """CostRecord Pydantic model validation."""

    def test_valid_cost_record(self) -> None:
        r = CostRecord(
            agent_code="mech-agent",
            provider="openai",
            model="gpt-4",
            cost_usd=0.05,
            project_id="proj-1",
            team_id="team-a",
            timestamp=datetime(2024, 1, 15, 12, 0, 0),
        )
        assert r.cost_usd == 0.05

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CostRecord(
                agent_code="mech",
                provider="openai",
                model="gpt-4",
                cost_usd=-1.0,
                project_id="p1",
                team_id="t1",
                timestamp=datetime.now(),
            )

    def test_empty_agent_code_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CostRecord(
                agent_code="",
                provider="openai",
                model="gpt-4",
                cost_usd=0.01,
                project_id="p1",
                team_id="t1",
                timestamp=datetime.now(),
            )

    def test_zero_cost_accepted(self) -> None:
        r = CostRecord(
            agent_code="a",
            provider="p",
            model="m",
            cost_usd=0.0,
            project_id="p1",
            team_id="t1",
            timestamp=datetime.now(),
        )
        assert r.cost_usd == 0.0


class TestCostAttributionTracker:
    """CostAttributionTracker CRUD and aggregation."""

    @pytest.fixture()
    def tracker(self) -> CostAttributionTracker:
        return CostAttributionTracker()

    def _make_record(
        self,
        project: str = "proj-1",
        team: str = "team-a",
        cost: float = 1.0,
        ts: datetime | None = None,
    ) -> CostRecord:
        return CostRecord(
            agent_code="mech",
            provider="openai",
            model="gpt-4",
            cost_usd=cost,
            project_id=project,
            team_id=team,
            timestamp=ts or datetime(2024, 3, 15, 12, 0, 0),
        )

    def test_record_and_query_by_project(
        self, tracker: CostAttributionTracker
    ) -> None:
        tracker.record_cost(self._make_record(project="p1"))
        tracker.record_cost(self._make_record(project="p2"))
        results = tracker.get_costs_by_project("p1")
        assert len(results) == 1
        assert results[0].project_id == "p1"

    def test_record_and_query_by_team(
        self, tracker: CostAttributionTracker
    ) -> None:
        tracker.record_cost(self._make_record(team="t1"))
        tracker.record_cost(self._make_record(team="t2"))
        results = tracker.get_costs_by_team("t1")
        assert len(results) == 1
        assert results[0].team_id == "t1"

    def test_query_by_project_with_time_range(
        self, tracker: CostAttributionTracker
    ) -> None:
        early = datetime(2024, 1, 1)
        mid = datetime(2024, 6, 1)
        late = datetime(2024, 12, 1)
        tracker.record_cost(self._make_record(project="p1", ts=early))
        tracker.record_cost(self._make_record(project="p1", ts=mid))
        tracker.record_cost(self._make_record(project="p1", ts=late))
        results = tracker.get_costs_by_project(
            "p1",
            start=datetime(2024, 3, 1),
            end=datetime(2024, 9, 1),
        )
        assert len(results) == 1
        assert results[0].timestamp == mid

    def test_query_by_team_with_time_range(
        self, tracker: CostAttributionTracker
    ) -> None:
        jan = datetime(2024, 1, 15)
        jul = datetime(2024, 7, 15)
        tracker.record_cost(self._make_record(team="t1", ts=jan))
        tracker.record_cost(self._make_record(team="t1", ts=jul))
        results = tracker.get_costs_by_team(
            "t1", start=datetime(2024, 6, 1)
        )
        assert len(results) == 1

    def test_daily_totals(self, tracker: CostAttributionTracker) -> None:
        day1 = datetime(2024, 3, 1, 10, 0)
        day2 = datetime(2024, 3, 2, 14, 0)
        tracker.record_cost(self._make_record(cost=1.0, ts=day1))
        tracker.record_cost(self._make_record(cost=2.0, ts=day1))
        tracker.record_cost(self._make_record(cost=3.0, ts=day2))
        totals = tracker.get_daily_totals()
        assert totals["2024-03-01"] == pytest.approx(3.0)
        assert totals["2024-03-02"] == pytest.approx(3.0)

    def test_weekly_totals(self, tracker: CostAttributionTracker) -> None:
        # 2024-01-01 is Monday, ISO week 1
        mon = datetime(2024, 1, 1, 12, 0)
        tracker.record_cost(self._make_record(cost=5.0, ts=mon))
        totals = tracker.get_weekly_totals()
        assert "2024-W01" in totals
        assert totals["2024-W01"] == pytest.approx(5.0)

    def test_monthly_totals(self, tracker: CostAttributionTracker) -> None:
        jan = datetime(2024, 1, 15)
        feb = datetime(2024, 2, 15)
        tracker.record_cost(self._make_record(cost=10.0, ts=jan))
        tracker.record_cost(self._make_record(cost=20.0, ts=feb))
        totals = tracker.get_monthly_totals()
        assert totals["2024-01"] == pytest.approx(10.0)
        assert totals["2024-02"] == pytest.approx(20.0)

    def test_monthly_totals_with_time_range(
        self, tracker: CostAttributionTracker
    ) -> None:
        jan = datetime(2024, 1, 15)
        feb = datetime(2024, 2, 15)
        mar = datetime(2024, 3, 15)
        tracker.record_cost(self._make_record(cost=10.0, ts=jan))
        tracker.record_cost(self._make_record(cost=20.0, ts=feb))
        tracker.record_cost(self._make_record(cost=30.0, ts=mar))
        totals = tracker.get_monthly_totals(
            start=datetime(2024, 2, 1), end=datetime(2024, 2, 28)
        )
        assert "2024-01" not in totals
        assert "2024-03" not in totals
        assert totals["2024-02"] == pytest.approx(20.0)

    def test_budget_threshold_not_exceeded(
        self, tracker: CostAttributionTracker
    ) -> None:
        tracker.record_cost(self._make_record(project="p1", cost=50.0))
        exceeded, total = tracker.check_budget_threshold("p1", 100.0)
        assert exceeded is False
        assert total == pytest.approx(50.0)

    def test_budget_threshold_exceeded(
        self, tracker: CostAttributionTracker
    ) -> None:
        tracker.record_cost(self._make_record(project="p1", cost=100.0))
        tracker.record_cost(self._make_record(project="p1", cost=50.0))
        exceeded, total = tracker.check_budget_threshold("p1", 100.0)
        assert exceeded is True
        assert total == pytest.approx(150.0)

    def test_budget_threshold_exactly_at_limit(
        self, tracker: CostAttributionTracker
    ) -> None:
        tracker.record_cost(self._make_record(project="p1", cost=100.0))
        exceeded, total = tracker.check_budget_threshold("p1", 100.0)
        assert exceeded is True
        assert total == pytest.approx(100.0)

    def test_budget_threshold_empty_project(
        self, tracker: CostAttributionTracker
    ) -> None:
        exceeded, total = tracker.check_budget_threshold("nonexistent", 100.0)
        assert exceeded is False
        assert total == pytest.approx(0.0)

    def test_clear(self, tracker: CostAttributionTracker) -> None:
        tracker.record_cost(self._make_record())
        assert len(tracker.get_costs_by_project("proj-1")) == 1
        tracker.clear()
        assert len(tracker.get_costs_by_project("proj-1")) == 0


# ---------------------------------------------------------------------------
# MET-125: Cost attribution dashboard JSON
# ---------------------------------------------------------------------------


class TestCostAttributionDashboard:
    """Validate the cost-attribution.json dashboard file."""

    @pytest.fixture()
    def dashboard(self) -> dict:
        path = (
            Path(__file__).resolve().parents[2]
            / "observability"
            / "dashboards"
            / "cost-attribution.json"
        )
        return json.loads(path.read_text())

    def test_dashboard_is_valid_json(self, dashboard: dict) -> None:
        assert isinstance(dashboard, dict)

    def test_has_uid(self, dashboard: dict) -> None:
        assert dashboard["uid"] == "metaforge-cost-attribution"

    def test_has_five_panels(self, dashboard: dict) -> None:
        assert len(dashboard["panels"]) == 5

    def test_panel_types(self, dashboard: dict) -> None:
        types = [p["type"] for p in dashboard["panels"]]
        assert "stat" in types
        assert "piechart" in types
        assert "barchart" in types
        assert "timeseries" in types
        assert "gauge" in types

    def test_panel_titles(self, dashboard: dict) -> None:
        titles = [p["title"] for p in dashboard["panels"]]
        assert "Total LLM Cost" in titles
        assert "Cost by Project" in titles
        assert "Cost by Team" in titles
        assert "Daily Cost Trend" in titles
        assert "Budget Utilization" in titles

    def test_has_template_variables(self, dashboard: dict) -> None:
        variables = dashboard["templating"]["list"]
        names = [v["name"] for v in variables]
        assert "project_id" in names
        assert "team_id" in names


# ---------------------------------------------------------------------------
# MET-126: Audit log models
# ---------------------------------------------------------------------------


class TestAuditEventModel:
    """AuditEvent Pydantic model validation."""

    def test_valid_audit_event(self) -> None:
        ev = AuditEvent(
            event_type=AuditEventType.graph_mutation,
            actor="user@example.com",
            action="create",
            resource_type="artifact",
            resource_id="art-123",
        )
        assert ev.event_type == AuditEventType.graph_mutation
        assert ev.actor == "user@example.com"

    def test_event_id_auto_generated(self) -> None:
        ev = AuditEvent(
            event_type=AuditEventType.authentication,
            actor="user",
            action="login",
            resource_type="session",
            resource_id="s-1",
        )
        assert ev.event_id is not None

    def test_timestamp_auto_generated(self) -> None:
        ev = AuditEvent(
            event_type=AuditEventType.authentication,
            actor="user",
            action="login",
            resource_type="session",
            resource_id="s-1",
        )
        assert isinstance(ev.timestamp, datetime)

    def test_empty_actor_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditEvent(
                event_type=AuditEventType.authentication,
                actor="",
                action="login",
                resource_type="session",
                resource_id="s-1",
            )

    def test_empty_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AuditEvent(
                event_type=AuditEventType.authentication,
                actor="user",
                action="",
                resource_type="session",
                resource_id="s-1",
            )

    def test_all_event_types(self) -> None:
        for evt in AuditEventType:
            ev = AuditEvent(
                event_type=evt,
                actor="user",
                action="test",
                resource_type="res",
                resource_id="r-1",
            )
            assert ev.event_type == evt

    def test_details_default_empty(self) -> None:
        ev = AuditEvent(
            event_type=AuditEventType.authorization,
            actor="user",
            action="check",
            resource_type="res",
            resource_id="r-1",
        )
        assert ev.details == {}

    def test_trace_id_optional(self) -> None:
        ev = AuditEvent(
            event_type=AuditEventType.session_lifecycle,
            actor="user",
            action="start",
            resource_type="session",
            resource_id="s-1",
            trace_id="abc-123",
        )
        assert ev.trace_id == "abc-123"


class TestExportConfig:
    """ExportConfig Pydantic model validation."""

    def test_valid_config(self) -> None:
        cfg = ExportConfig(
            destination=ExportDestination.s3,
            format="jsonl",
            batch_size=50,
            flush_interval_seconds=30,
        )
        assert cfg.destination == ExportDestination.s3

    def test_all_destinations(self) -> None:
        for dest in ExportDestination:
            cfg = ExportConfig(destination=dest)
            assert cfg.destination == dest

    def test_invalid_batch_size(self) -> None:
        with pytest.raises(ValidationError):
            ExportConfig(destination=ExportDestination.s3, batch_size=0)

    def test_invalid_flush_interval(self) -> None:
        with pytest.raises(ValidationError):
            ExportConfig(
                destination=ExportDestination.s3, flush_interval_seconds=0
            )


# ---------------------------------------------------------------------------
# MET-126: Audit logger
# ---------------------------------------------------------------------------


class TestAuditLogger:
    """AuditLogger event recording, flush, and query."""

    @pytest.fixture()
    def audit_logger(self) -> AuditLogger:
        config = ExportConfig(
            destination=ExportDestination.local_file,
            batch_size=100,
            flush_interval_seconds=60,
        )
        return AuditLogger(config=config)

    def _make_event(
        self,
        actor: str = "user@test.com",
        action: str = "create",
        event_type: AuditEventType = AuditEventType.graph_mutation,
        ts: datetime | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_type=event_type,
            actor=actor,
            action=action,
            resource_type="artifact",
            resource_id="art-1",
            timestamp=ts or datetime(2024, 6, 1, 12, 0, 0),
        )

    def test_log_event_stores_in_buffer(self, audit_logger: AuditLogger) -> None:
        ev = self._make_event()
        audit_logger.log_event(ev)
        events = audit_logger.get_events()
        assert len(events) == 1
        assert events[0].actor == "user@test.com"

    def test_flush_moves_to_flushed(self, audit_logger: AuditLogger) -> None:
        ev = self._make_event()
        audit_logger.log_event(ev)
        flushed = audit_logger.flush()
        assert len(flushed) == 1
        # Buffer should be empty now
        assert len(audit_logger._buffer) == 0
        # Still accessible via get_events (from _flushed)
        assert len(audit_logger.get_events()) == 1

    def test_auto_flush_on_batch_size(self) -> None:
        config = ExportConfig(
            destination=ExportDestination.local_file, batch_size=2
        )
        al = AuditLogger(config=config)
        al.log_event(self._make_event())
        assert len(al._buffer) == 1
        al.log_event(self._make_event())
        # Should have auto-flushed
        assert len(al._buffer) == 0
        assert len(al._flushed) == 2

    def test_query_by_event_type(self, audit_logger: AuditLogger) -> None:
        audit_logger.log_event(
            self._make_event(event_type=AuditEventType.graph_mutation)
        )
        audit_logger.log_event(
            self._make_event(event_type=AuditEventType.authentication)
        )
        results = audit_logger.get_events(
            event_type=AuditEventType.authentication
        )
        assert len(results) == 1
        assert results[0].event_type == AuditEventType.authentication

    def test_query_by_actor(self, audit_logger: AuditLogger) -> None:
        audit_logger.log_event(self._make_event(actor="alice"))
        audit_logger.log_event(self._make_event(actor="bob"))
        results = audit_logger.get_events(actor="alice")
        assert len(results) == 1
        assert results[0].actor == "alice"

    def test_query_by_time_range(self, audit_logger: AuditLogger) -> None:
        early = datetime(2024, 1, 1)
        late = datetime(2024, 12, 1)
        audit_logger.log_event(self._make_event(ts=early))
        audit_logger.log_event(self._make_event(ts=late))
        results = audit_logger.get_events(
            start=datetime(2024, 6, 1), end=datetime(2024, 12, 31)
        )
        assert len(results) == 1
        assert results[0].timestamp == late

    def test_log_policy_decision(self, audit_logger: AuditLogger) -> None:
        audit_logger.log_policy_decision(
            actor="opa-engine",
            policy="twin_write_policy",
            result="allow",
            details={"input": "test"},
        )
        events = audit_logger.get_events()
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.policy_decision
        assert events[0].details["result"] == "allow"

    def test_log_graph_mutation(self, audit_logger: AuditLogger) -> None:
        audit_logger.log_graph_mutation(
            actor="mech-agent",
            action="update",
            resource_type="artifact",
            resource_id="art-42",
            details={"field": "stress_result"},
        )
        events = audit_logger.get_events()
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.graph_mutation
        assert events[0].resource_id == "art-42"

    def test_log_approval_action(self, audit_logger: AuditLogger) -> None:
        audit_logger.log_approval_action(
            actor="reviewer@test.com",
            action="review",
            change_id="cr-99",
            decision="approved",
        )
        events = audit_logger.get_events()
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.approval_action
        assert events[0].details["decision"] == "approved"

    def test_export_jsonl_format(self, audit_logger: AuditLogger) -> None:
        ev1 = self._make_event(actor="alice")
        ev2 = self._make_event(actor="bob")
        jsonl = AuditLogger.export_jsonl([ev1, ev2])
        lines = jsonl.strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "event_id" in obj
            assert "actor" in obj
            assert "timestamp" in obj

    def test_export_jsonl_empty(self) -> None:
        jsonl = AuditLogger.export_jsonl([])
        assert jsonl == ""

    def test_export_jsonl_single_event(self) -> None:
        ev = AuditEvent(
            event_type=AuditEventType.authentication,
            actor="user",
            action="login",
            resource_type="session",
            resource_id="s-1",
        )
        jsonl = AuditLogger.export_jsonl([ev])
        obj = json.loads(jsonl)
        assert obj["actor"] == "user"
        assert obj["action"] == "login"


# ---------------------------------------------------------------------------
# MET-126: Audit integrity
# ---------------------------------------------------------------------------


class TestAuditIntegrity:
    """AuditIntegrity hash computation and chain verification."""

    def _make_event(self, action: str = "create") -> AuditEvent:
        return AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.graph_mutation,
            actor="user@test.com",
            action=action,
            resource_type="artifact",
            resource_id="art-1",
            timestamp=datetime(2024, 6, 1, 12, 0, 0),
        )

    def test_compute_hash_returns_hex_string(self) -> None:
        ev = self._make_event()
        h = AuditIntegrity.compute_hash(ev)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

    def test_same_event_same_hash(self) -> None:
        ev = self._make_event()
        h1 = AuditIntegrity.compute_hash(ev)
        h2 = AuditIntegrity.compute_hash(ev)
        assert h1 == h2

    def test_different_events_different_hashes(self) -> None:
        ev1 = self._make_event(action="create")
        ev2 = self._make_event(action="delete")
        h1 = AuditIntegrity.compute_hash(ev1)
        h2 = AuditIntegrity.compute_hash(ev2)
        assert h1 != h2

    def test_previous_hash_changes_result(self) -> None:
        ev = self._make_event()
        h1 = AuditIntegrity.compute_hash(ev, previous_hash="")
        h2 = AuditIntegrity.compute_hash(ev, previous_hash="abc")
        assert h1 != h2

    def test_build_hash_chain(self) -> None:
        events = [self._make_event(action=f"act-{i}") for i in range(5)]
        chain = AuditIntegrity.build_hash_chain(events)
        assert len(chain) == 5
        # Each hash should be unique
        assert len(set(chain)) == 5

    def test_build_hash_chain_empty(self) -> None:
        chain = AuditIntegrity.build_hash_chain([])
        assert chain == []

    def test_verify_chain_valid(self) -> None:
        events = [self._make_event(action=f"act-{i}") for i in range(5)]
        chain = AuditIntegrity.build_hash_chain(events)
        assert AuditIntegrity.verify_chain(events, chain) is True

    def test_verify_chain_tampered_event(self) -> None:
        events = [self._make_event(action=f"act-{i}") for i in range(5)]
        chain = AuditIntegrity.build_hash_chain(events)
        # Tamper with event at index 2
        events[2] = self._make_event(action="tampered")
        assert AuditIntegrity.verify_chain(events, chain) is False

    def test_verify_chain_tampered_hash(self) -> None:
        events = [self._make_event(action=f"act-{i}") for i in range(3)]
        chain = AuditIntegrity.build_hash_chain(events)
        chain[1] = "0" * 64
        assert AuditIntegrity.verify_chain(events, chain) is False

    def test_verify_chain_length_mismatch(self) -> None:
        events = [self._make_event()]
        chain = AuditIntegrity.build_hash_chain(events)
        chain.append("extra")
        assert AuditIntegrity.verify_chain(events, chain) is False

    def test_verify_empty_chain(self) -> None:
        assert AuditIntegrity.verify_chain([], []) is True

    def test_chain_is_ordered(self) -> None:
        """The hash at index N depends on the hash at index N-1."""
        events = [self._make_event(action=f"act-{i}") for i in range(3)]
        chain = AuditIntegrity.build_hash_chain(events)
        # Verify second hash uses first hash as previous
        expected_second = AuditIntegrity.compute_hash(
            events[1], previous_hash=chain[0]
        )
        assert chain[1] == expected_second
