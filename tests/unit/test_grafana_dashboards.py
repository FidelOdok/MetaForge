"""Tests for Grafana dashboard JSON files.

Validates that the System Overview dashboard (and any future dashboards)
are well-formed, contain the expected panels, and follow Grafana
dashboard conventions. Covers MET-105.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DASHBOARD_DIR = PROJECT_ROOT / "observability" / "dashboards"
SYSTEM_OVERVIEW = DASHBOARD_DIR / "system-overview.json"

# Valid Grafana panel types we use in the overview dashboard
VALID_PANEL_TYPES = {"stat", "timeseries", "gauge", "alertlist", "graph", "table", "heatmap"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dashboard_data() -> dict:
    """Load and parse the system-overview dashboard JSON."""
    assert SYSTEM_OVERVIEW.exists(), "system-overview.json must exist"
    text = SYSTEM_OVERVIEW.read_text(encoding="utf-8")
    return json.loads(text)


@pytest.fixture
def panels(dashboard_data: dict) -> list[dict]:
    """Extract the panels list from the dashboard."""
    return dashboard_data.get("panels", [])


# ===========================================================================
# File validity
# ===========================================================================

class TestDashboardFileValidity:
    """Basic file and JSON validity checks."""

    def test_system_overview_file_exists(self) -> None:
        assert SYSTEM_OVERVIEW.exists(), "system-overview.json must exist"

    def test_system_overview_is_valid_json(self) -> None:
        text = SYSTEM_OVERVIEW.read_text(encoding="utf-8")
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            pytest.fail(f"system-overview.json is not valid JSON: {exc}")

    def test_dashboard_is_a_dict(self, dashboard_data: dict) -> None:
        assert isinstance(dashboard_data, dict), "Dashboard root must be a JSON object"


# ===========================================================================
# Dashboard metadata
# ===========================================================================

class TestDashboardMetadata:
    """Verify dashboard-level settings."""

    def test_dashboard_has_correct_title(self, dashboard_data: dict) -> None:
        assert dashboard_data.get("title") == "MetaForge System Overview", (
            "Dashboard title must be 'MetaForge System Overview'"
        )

    def test_dashboard_has_correct_uid(self, dashboard_data: dict) -> None:
        assert dashboard_data.get("uid") == "metaforge-system-overview", (
            "Dashboard UID must be 'metaforge-system-overview'"
        )

    def test_dashboard_has_tags(self, dashboard_data: dict) -> None:
        tags = dashboard_data.get("tags", [])
        assert "metaforge" in tags, "Dashboard must have 'metaforge' tag"
        assert "overview" in tags, "Dashboard must have 'overview' tag"

    def test_dashboard_has_auto_refresh(self, dashboard_data: dict) -> None:
        refresh = dashboard_data.get("refresh")
        assert refresh is not None and refresh != "", (
            "Dashboard must have an auto-refresh interval"
        )

    def test_dashboard_refresh_is_30s(self, dashboard_data: dict) -> None:
        assert dashboard_data.get("refresh") == "30s", (
            "Dashboard auto-refresh must be 30s"
        )

    def test_dashboard_has_time_range(self, dashboard_data: dict) -> None:
        time_config = dashboard_data.get("time", {})
        assert "from" in time_config, "Dashboard must have a time 'from' setting"
        assert "to" in time_config, "Dashboard must have a time 'to' setting"

    def test_dashboard_default_time_range_is_1h(self, dashboard_data: dict) -> None:
        time_config = dashboard_data.get("time", {})
        assert time_config.get("from") == "now-1h", (
            "Dashboard default time range 'from' must be 'now-1h'"
        )
        assert time_config.get("to") == "now", (
            "Dashboard default time range 'to' must be 'now'"
        )


# ===========================================================================
# Panels
# ===========================================================================

class TestDashboardPanels:
    """Validate panel structure and content."""

    def test_dashboard_has_at_least_6_panels(self, panels: list[dict]) -> None:
        assert len(panels) >= 6, (
            f"Dashboard must have at least 6 panels, found {len(panels)}"
        )

    def test_dashboard_has_8_panels(self, panels: list[dict]) -> None:
        assert len(panels) == 8, (
            f"Dashboard should have exactly 8 panels, found {len(panels)}"
        )

    def test_each_panel_has_valid_type(self, panels: list[dict]) -> None:
        for panel in panels:
            panel_type = panel.get("type")
            assert panel_type in VALID_PANEL_TYPES, (
                f"Panel '{panel.get('title', 'unknown')}' has invalid type: {panel_type}"
            )

    def test_each_panel_has_title(self, panels: list[dict]) -> None:
        for panel in panels:
            title = panel.get("title", "")
            assert title, f"Panel id={panel.get('id')} must have a non-empty title"

    def test_each_panel_has_grid_position(self, panels: list[dict]) -> None:
        for panel in panels:
            grid_pos = panel.get("gridPos")
            assert grid_pos is not None, (
                f"Panel '{panel.get('title')}' must have gridPos"
            )
            for key in ("h", "w", "x", "y"):
                assert key in grid_pos, (
                    f"Panel '{panel.get('title')}' gridPos must have '{key}'"
                )

    def test_panels_with_targets_have_promql(self, panels: list[dict]) -> None:
        """All panels with targets must have a non-empty PromQL expression."""
        for panel in panels:
            targets = panel.get("targets", [])
            if targets:
                for target in targets:
                    expr = target.get("expr", "")
                    assert expr, (
                        f"Panel '{panel.get('title')}' target must have a PromQL expr"
                    )

    def test_panel_types_include_stat(self, panels: list[dict]) -> None:
        types = {p.get("type") for p in panels}
        assert "stat" in types, "Dashboard must have at least one stat panel"

    def test_panel_types_include_timeseries(self, panels: list[dict]) -> None:
        types = {p.get("type") for p in panels}
        assert "timeseries" in types, "Dashboard must have at least one timeseries panel"

    def test_panel_types_include_gauge(self, panels: list[dict]) -> None:
        types = {p.get("type") for p in panels}
        assert "gauge" in types, "Dashboard must have at least one gauge panel"

    def test_panel_types_include_alertlist(self, panels: list[dict]) -> None:
        types = {p.get("type") for p in panels}
        assert "alertlist" in types, "Dashboard must have at least one alertlist panel"

    def test_gateway_status_panel_exists(self, panels: list[dict]) -> None:
        titles = [p.get("title", "") for p in panels]
        assert any("Gateway Status" in t for t in titles), (
            "Dashboard must have a 'Gateway Status' panel"
        )

    def test_request_rate_panel_exists(self, panels: list[dict]) -> None:
        titles = [p.get("title", "") for p in panels]
        assert any("Request Rate" in t for t in titles), (
            "Dashboard must have a 'Request Rate' panel"
        )

    def test_error_rate_panel_exists(self, panels: list[dict]) -> None:
        titles = [p.get("title", "") for p in panels]
        assert any("Error Rate" in t for t in titles), (
            "Dashboard must have an 'Error Rate' panel"
        )

    def test_panels_use_4_column_grid(self, panels: list[dict]) -> None:
        """Panels should use a 4-column grid (widths of 6 in a 24-unit grid)."""
        for panel in panels:
            grid_pos = panel.get("gridPos", {})
            w = grid_pos.get("w", 0)
            assert w == 6, (
                f"Panel '{panel.get('title')}' width should be 6 (4-column layout), got {w}"
            )

    def test_panel_ids_are_unique(self, panels: list[dict]) -> None:
        ids = [p.get("id") for p in panels]
        assert len(ids) == len(set(ids)), (
            f"Panel IDs must be unique, found duplicates in {ids}"
        )
