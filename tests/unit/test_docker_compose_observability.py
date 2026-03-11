"""Tests for Docker Compose observability stack configuration files.

Validates that all YAML/JSON configuration files for the observability
stack (OTel Collector, Prometheus, Grafana) are well-formed and correctly
structured. Covers MET-104.
"""

from __future__ import annotations

import re
from pathlib import Path

# Project root is four levels up from this test file:
# tests/unit/test_docker_compose_observability.py -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers: lightweight YAML parsing (no PyYAML dependency required)
# ---------------------------------------------------------------------------


def _load_yaml_text(path: Path) -> str:
    """Read a YAML file and return its raw text."""
    assert path.exists(), f"File not found: {path}"
    return path.read_text(encoding="utf-8")


def _yaml_has_key(text: str, key: str) -> bool:
    """Check if a top-level or nested key exists in YAML text."""
    pattern = rf"^\s*{re.escape(key)}\s*:"
    return bool(re.search(pattern, text, re.MULTILINE))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

COMPOSE_FILE = PROJECT_ROOT / "docker-compose.observability.yml"
OTEL_CONFIG = PROJECT_ROOT / "observability" / "otel-collector-config.yaml"
PROMETHEUS_CONFIG = PROJECT_ROOT / "observability" / "prometheus.yml"
GRAFANA_DS_CONFIG = PROJECT_ROOT / "observability" / "grafana-datasources.yml"
GRAFANA_DASH_CONFIG = PROJECT_ROOT / "observability" / "grafana-dashboards.yml"
DASHBOARD_DIR = PROJECT_ROOT / "observability" / "dashboards"


# ===========================================================================
# docker-compose.observability.yml
# ===========================================================================


class TestDockerComposeFile:
    """Validate docker-compose.observability.yml structure."""

    def test_compose_file_exists(self) -> None:
        assert COMPOSE_FILE.exists(), "docker-compose.observability.yml must exist at project root"

    def test_compose_is_valid_yaml(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        # Must contain the version key and services key
        assert _yaml_has_key(text, "version"), "Compose file must declare a version"
        assert _yaml_has_key(text, "services"), "Compose file must declare services"

    def test_compose_defines_otel_collector(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert _yaml_has_key(text, "otel-collector"), "Compose must define otel-collector service"

    def test_compose_defines_prometheus(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert _yaml_has_key(text, "prometheus"), "Compose must define prometheus service"

    def test_compose_defines_grafana(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert _yaml_has_key(text, "grafana"), "Compose must define grafana service"

    def test_compose_defines_volumes(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert _yaml_has_key(text, "volumes"), "Compose must declare volumes"

    def test_compose_defines_networks(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert _yaml_has_key(text, "networks"), "Compose must declare networks"

    def test_compose_port_mappings_no_conflicts(self) -> None:
        """Verify that all host ports in the compose file are unique."""
        text = _load_yaml_text(COMPOSE_FILE)
        # Match port mappings like "4317:4317" or "3001:3000"
        port_pattern = re.compile(r'"(\d+):\d+"')
        host_ports = port_pattern.findall(text)
        assert len(host_ports) == len(set(host_ports)), (
            f"Duplicate host ports detected: {host_ports}"
        )

    def test_compose_otel_collector_image(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert "otel/opentelemetry-collector-contrib" in text, (
            "otel-collector must use the contrib image"
        )

    def test_compose_prometheus_image(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert "prom/prometheus" in text, "prometheus must use prom/prometheus image"

    def test_compose_grafana_image(self) -> None:
        text = _load_yaml_text(COMPOSE_FILE)
        assert "grafana/grafana" in text, "grafana must use grafana/grafana image"


# ===========================================================================
# OTel Collector configuration
# ===========================================================================


class TestOtelCollectorConfig:
    """Validate observability/otel-collector-config.yaml."""

    def test_otel_config_exists(self) -> None:
        assert OTEL_CONFIG.exists(), "otel-collector-config.yaml must exist"

    def test_otel_has_receivers(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "receivers"), "OTel config must have receivers section"

    def test_otel_has_processors(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "processors"), "OTel config must have processors section"

    def test_otel_has_exporters(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "exporters"), "OTel config must have exporters section"

    def test_otel_has_service(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "service"), "OTel config must have service section"

    def test_otel_has_otlp_receiver(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "otlp"), "OTel config must have otlp receiver"

    def test_otel_has_prometheus_exporter(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "prometheus"), "OTel config must have prometheus exporter"

    def test_otel_has_pipelines(self) -> None:
        text = _load_yaml_text(OTEL_CONFIG)
        assert _yaml_has_key(text, "pipelines"), "OTel config must have pipelines in service"


# ===========================================================================
# Prometheus configuration
# ===========================================================================


class TestPrometheusConfig:
    """Validate observability/prometheus.yml."""

    def test_prometheus_config_exists(self) -> None:
        assert PROMETHEUS_CONFIG.exists(), "prometheus.yml must exist"

    def test_prometheus_has_scrape_configs(self) -> None:
        text = _load_yaml_text(PROMETHEUS_CONFIG)
        assert _yaml_has_key(text, "scrape_configs"), "prometheus.yml must have scrape_configs"

    def test_prometheus_has_global(self) -> None:
        text = _load_yaml_text(PROMETHEUS_CONFIG)
        assert _yaml_has_key(text, "global"), "prometheus.yml must have global section"

    def test_prometheus_scrapes_otel_collector(self) -> None:
        text = _load_yaml_text(PROMETHEUS_CONFIG)
        assert "otel-collector" in text, "prometheus.yml must scrape otel-collector"

    def test_prometheus_scrapes_gateway_via_otel_collector(self) -> None:
        """Gateway metrics flow via OTLP → OTel Collector → Prometheus exporter on :8889."""
        text = _load_yaml_text(PROMETHEUS_CONFIG)
        assert "otel-collector:8889" in text, (
            "prometheus.yml must scrape otel-collector:8889 (gateway metrics arrive via OTLP)"
        )


# ===========================================================================
# Grafana provisioning configs
# ===========================================================================


class TestGrafanaDatasourcesConfig:
    """Validate observability/grafana-datasources.yml."""

    def test_grafana_datasources_exists(self) -> None:
        assert GRAFANA_DS_CONFIG.exists(), "grafana-datasources.yml must exist"

    def test_grafana_datasources_has_api_version(self) -> None:
        text = _load_yaml_text(GRAFANA_DS_CONFIG)
        assert _yaml_has_key(text, "apiVersion"), "grafana-datasources.yml must have apiVersion"

    def test_grafana_datasources_points_to_prometheus(self) -> None:
        text = _load_yaml_text(GRAFANA_DS_CONFIG)
        assert "prometheus" in text.lower(), "grafana-datasources.yml must reference prometheus"

    def test_grafana_datasources_has_prometheus_url(self) -> None:
        text = _load_yaml_text(GRAFANA_DS_CONFIG)
        assert "http://prometheus:9090" in text, (
            "grafana-datasources.yml must point to prometheus:9090"
        )


class TestGrafanaDashboardsConfig:
    """Validate observability/grafana-dashboards.yml."""

    def test_grafana_dashboards_config_exists(self) -> None:
        assert GRAFANA_DASH_CONFIG.exists(), "grafana-dashboards.yml must exist"

    def test_grafana_dashboards_has_providers(self) -> None:
        text = _load_yaml_text(GRAFANA_DASH_CONFIG)
        assert _yaml_has_key(text, "providers"), "grafana-dashboards.yml must have providers"

    def test_grafana_dashboards_has_metaforge_folder(self) -> None:
        text = _load_yaml_text(GRAFANA_DASH_CONFIG)
        assert "MetaForge" in text, "grafana-dashboards.yml must reference MetaForge folder"


# ===========================================================================
# Volume mount file existence
# ===========================================================================


class TestVolumeMountReferences:
    """Verify that files referenced in volume mounts actually exist."""

    def test_otel_config_referenced_in_compose_exists(self) -> None:
        assert OTEL_CONFIG.exists(), (
            "otel-collector-config.yaml referenced in compose volume mount must exist"
        )

    def test_prometheus_config_referenced_in_compose_exists(self) -> None:
        assert PROMETHEUS_CONFIG.exists(), (
            "prometheus.yml referenced in compose volume mount must exist"
        )

    def test_grafana_datasources_referenced_in_compose_exists(self) -> None:
        assert GRAFANA_DS_CONFIG.exists(), (
            "grafana-datasources.yml referenced in compose volume mount must exist"
        )

    def test_grafana_dashboards_config_referenced_in_compose_exists(self) -> None:
        assert GRAFANA_DASH_CONFIG.exists(), (
            "grafana-dashboards.yml referenced in compose volume mount must exist"
        )

    def test_dashboards_directory_exists(self) -> None:
        assert DASHBOARD_DIR.exists() and DASHBOARD_DIR.is_dir(), (
            "observability/dashboards/ directory must exist"
        )

    def test_dashboards_directory_has_json_files(self) -> None:
        json_files = list(DASHBOARD_DIR.glob("*.json"))
        assert len(json_files) >= 1, (
            "observability/dashboards/ must contain at least one JSON dashboard file"
        )
