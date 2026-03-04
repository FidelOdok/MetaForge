"""Tests for observability config, bootstrap, and ObservabilityState."""

from __future__ import annotations

from dataclasses import fields as dc_fields
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from observability.bootstrap import (
    ObservabilityState,
    init_observability,
    shutdown_observability,
)
from observability.config import (
    GrafanaConfig,
    ObservabilityConfig,
    OtlpExporterConfig,
    PrometheusConfig,
)

# ---------------------------------------------------------------------------
# ObservabilityConfig defaults
# ---------------------------------------------------------------------------


class TestObservabilityConfigDefaults:
    """Verify default values for all configuration models."""

    def test_default_config_creation(self) -> None:
        cfg = ObservabilityConfig()
        assert cfg.enabled is True
        assert cfg.service_name == "metaforge"
        assert cfg.environment == "development"
        assert cfg.trace_sample_rate == 1.0
        assert cfg.log_level == "INFO"
        assert cfg.enable_traces is True
        assert cfg.enable_metrics is True
        assert cfg.enable_logs is True

    def test_otlp_defaults(self) -> None:
        cfg = OtlpExporterConfig()
        assert cfg.endpoint == "http://localhost:4317"
        assert cfg.insecure is True
        assert cfg.timeout_ms == 5000

    def test_prometheus_defaults(self) -> None:
        cfg = PrometheusConfig()
        assert cfg.port == 9464
        assert cfg.scrape_interval == "15s"

    def test_grafana_defaults(self) -> None:
        cfg = GrafanaConfig()
        assert cfg.url == "http://localhost:3000"

    def test_nested_configs_created_by_default(self) -> None:
        cfg = ObservabilityConfig()
        assert isinstance(cfg.otlp, OtlpExporterConfig)
        assert isinstance(cfg.prometheus, PrometheusConfig)
        assert isinstance(cfg.grafana, GrafanaConfig)


# ---------------------------------------------------------------------------
# Custom / override values
# ---------------------------------------------------------------------------


class TestObservabilityConfigCustom:
    """Verify that custom overrides are applied correctly."""

    def test_custom_service_name_and_environment(self) -> None:
        cfg = ObservabilityConfig(
            service_name="my-service",
            environment="production",
        )
        assert cfg.service_name == "my-service"
        assert cfg.environment == "production"

    def test_custom_otlp_endpoint(self) -> None:
        cfg = ObservabilityConfig(
            otlp=OtlpExporterConfig(
                endpoint="https://otel.example.com:4317",
                insecure=False,
                timeout_ms=10000,
            ),
        )
        assert cfg.otlp.endpoint == "https://otel.example.com:4317"
        assert cfg.otlp.insecure is False
        assert cfg.otlp.timeout_ms == 10000

    def test_custom_prometheus_port(self) -> None:
        cfg = ObservabilityConfig(
            prometheus=PrometheusConfig(port=9090, scrape_interval="30s"),
        )
        assert cfg.prometheus.port == 9090
        assert cfg.prometheus.scrape_interval == "30s"

    def test_custom_grafana_url(self) -> None:
        cfg = ObservabilityConfig(
            grafana=GrafanaConfig(url="https://grafana.internal"),
        )
        assert cfg.grafana.url == "https://grafana.internal"

    def test_disabled_config(self) -> None:
        cfg = ObservabilityConfig(enabled=False)
        assert cfg.enabled is False

    def test_selective_pillars(self) -> None:
        cfg = ObservabilityConfig(
            enable_traces=False,
            enable_metrics=True,
            enable_logs=False,
        )
        assert cfg.enable_traces is False
        assert cfg.enable_metrics is True
        assert cfg.enable_logs is False

    def test_trace_sample_rate_custom(self) -> None:
        cfg = ObservabilityConfig(trace_sample_rate=0.5)
        assert cfg.trace_sample_rate == 0.5


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestObservabilityConfigSerialization:
    """Verify round-trip serialization with model_dump."""

    def test_model_dump_returns_dict(self) -> None:
        cfg = ObservabilityConfig()
        data = cfg.model_dump()
        assert isinstance(data, dict)
        assert "enabled" in data
        assert "otlp" in data
        assert "prometheus" in data
        assert "grafana" in data

    def test_model_dump_nested_configs(self) -> None:
        cfg = ObservabilityConfig()
        data = cfg.model_dump()
        assert data["otlp"]["endpoint"] == "http://localhost:4317"
        assert data["prometheus"]["port"] == 9464
        assert data["grafana"]["url"] == "http://localhost:3000"

    def test_round_trip_from_dict(self) -> None:
        original = ObservabilityConfig(
            service_name="round-trip",
            environment="staging",
            trace_sample_rate=0.25,
        )
        data = original.model_dump()
        restored = ObservabilityConfig(**data)
        assert restored == original


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestObservabilityConfigValidation:
    """Ensure invalid values are rejected by Pydantic."""

    def test_invalid_trace_sample_rate_too_high(self) -> None:
        with pytest.raises(ValidationError):
            ObservabilityConfig(trace_sample_rate=1.5)

    def test_invalid_trace_sample_rate_negative(self) -> None:
        with pytest.raises(ValidationError):
            ObservabilityConfig(trace_sample_rate=-0.1)

    def test_invalid_timeout_negative(self) -> None:
        with pytest.raises(ValidationError):
            OtlpExporterConfig(timeout_ms=-1)

    def test_invalid_prometheus_port_zero(self) -> None:
        with pytest.raises(ValidationError):
            PrometheusConfig(port=0)

    def test_invalid_prometheus_port_too_high(self) -> None:
        with pytest.raises(ValidationError):
            PrometheusConfig(port=70000)


# ---------------------------------------------------------------------------
# ObservabilityState
# ---------------------------------------------------------------------------


class TestObservabilityState:
    """Tests for the ObservabilityState dataclass."""

    def test_default_state_is_inactive(self) -> None:
        state = ObservabilityState()
        assert state.is_active is False
        assert state.tracer_provider is None
        assert state.meter_provider is None

    def test_active_state(self) -> None:
        state = ObservabilityState(
            tracer_provider="mock_tp",
            meter_provider="mock_mp",
            is_active=True,
        )
        assert state.is_active is True
        assert state.tracer_provider == "mock_tp"
        assert state.meter_provider == "mock_mp"

    def test_state_has_expected_fields(self) -> None:
        names = {f.name for f in dc_fields(ObservabilityState)}
        assert names == {"tracer_provider", "meter_provider", "is_active"}


# ---------------------------------------------------------------------------
# init_observability -- no OTel installed (real environment)
# ---------------------------------------------------------------------------


class TestInitObservabilityNoOtel:
    """init_observability should return a no-op state when OTel is absent."""

    def test_returns_inactive_state_when_otel_unavailable(self) -> None:
        with patch(
            "observability.bootstrap._otel_fully_available", return_value=False
        ):
            state = init_observability(ObservabilityConfig())
        assert state.is_active is False
        assert state.tracer_provider is None
        assert state.meter_provider is None

    def test_returns_inactive_state_when_disabled(self) -> None:
        cfg = ObservabilityConfig(enabled=False)
        state = init_observability(cfg)
        assert state.is_active is False


# ---------------------------------------------------------------------------
# init_observability -- mocked OTel packages
# ---------------------------------------------------------------------------


class TestInitObservabilityMocked:
    """init_observability with OTel mocked as available."""

    def _mock_otel_init(self, config: ObservabilityConfig) -> ObservabilityState:
        """Run init_observability with all OTel symbols mocked."""
        mock_resource = MagicMock()
        mock_resource_cls = MagicMock(return_value=mock_resource)

        mock_tracer_provider = MagicMock()
        mock_meter_provider = MagicMock()
        mock_span_exporter = MagicMock()
        mock_metric_exporter = MagicMock()
        mock_span_processor = MagicMock()
        mock_reader = MagicMock()

        patches = {
            "observability.bootstrap._otel_fully_available": MagicMock(
                return_value=True
            ),
            "observability.bootstrap.Resource": mock_resource_cls,
            "observability.bootstrap.TracerProvider": MagicMock(
                return_value=mock_tracer_provider
            ),
            "observability.bootstrap.MeterProvider": MagicMock(
                return_value=mock_meter_provider
            ),
            "observability.bootstrap.OTLPSpanExporter": MagicMock(
                return_value=mock_span_exporter
            ),
            "observability.bootstrap.OTLPMetricExporter": MagicMock(
                return_value=mock_metric_exporter
            ),
            "observability.bootstrap.BatchSpanProcessor": MagicMock(
                return_value=mock_span_processor
            ),
            "observability.bootstrap.PeriodicExportingMetricReader": MagicMock(
                return_value=mock_reader
            ),
            "observability.bootstrap.otel_trace": MagicMock(),
            "observability.bootstrap.otel_metrics": MagicMock(),
        }

        with patch.dict("observability.bootstrap.__dict__", {}):
            # Apply patches one by one so each target is resolved independently
            ctx_managers = [patch(k, v) for k, v in patches.items()]
            for cm in ctx_managers:
                cm.start()
            try:
                state = init_observability(config)
            finally:
                for cm in ctx_managers:
                    cm.stop()

        return state

    def test_init_with_mocked_otel_returns_active_state(self) -> None:
        state = self._mock_otel_init(ObservabilityConfig())
        assert state.is_active is True

    def test_init_with_traces_disabled(self) -> None:
        state = self._mock_otel_init(
            ObservabilityConfig(enable_traces=False, enable_metrics=True)
        )
        assert state.is_active is True
        assert state.tracer_provider is None

    def test_init_with_metrics_disabled(self) -> None:
        state = self._mock_otel_init(
            ObservabilityConfig(enable_traces=True, enable_metrics=False)
        )
        assert state.is_active is True
        assert state.meter_provider is None


# ---------------------------------------------------------------------------
# shutdown_observability
# ---------------------------------------------------------------------------


class TestShutdownObservability:
    """Tests for clean shutdown behaviour."""

    def test_shutdown_noop_state(self) -> None:
        state = ObservabilityState()
        # Should not raise
        shutdown_observability(state)
        assert state.is_active is False

    def test_shutdown_calls_provider_shutdown(self) -> None:
        tp = MagicMock()
        mp = MagicMock()
        state = ObservabilityState(tracer_provider=tp, meter_provider=mp, is_active=True)
        shutdown_observability(state)
        tp.shutdown.assert_called_once()
        mp.shutdown.assert_called_once()
        assert state.is_active is False

    def test_shutdown_handles_provider_errors(self) -> None:
        tp = MagicMock()
        tp.shutdown.side_effect = RuntimeError("tracer boom")
        mp = MagicMock()
        mp.shutdown.side_effect = RuntimeError("meter boom")
        state = ObservabilityState(tracer_provider=tp, meter_provider=mp, is_active=True)
        # Should not raise despite errors
        shutdown_observability(state)
        assert state.is_active is False
