"""OTel SDK bootstrap -- initialise tracer, meter, and logger providers.

All OpenTelemetry imports are guarded by try/except so that the module
works even when OTel packages are not installed.  In that case a no-op
``ObservabilityState`` is returned and the application continues without
telemetry instrumentation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from observability.config import ObservabilityConfig

logger = logging.getLogger(__name__)

# ---- optional OTel imports ------------------------------------------------

_HAS_OTEL_TRACE = False
_HAS_OTEL_METRICS = False
_HAS_OTEL_SDK_TRACE = False
_HAS_OTEL_SDK_METRICS = False
_HAS_OTEL_EXPORTER = False

try:
    from opentelemetry import trace as otel_trace  # noqa: F401

    _HAS_OTEL_TRACE = True
except ImportError:
    otel_trace = None  # type: ignore[assignment]

try:
    from opentelemetry import metrics as otel_metrics  # noqa: F401

    _HAS_OTEL_METRICS = True
except ImportError:
    otel_metrics = None  # type: ignore[assignment]

try:
    from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: F401

    _HAS_OTEL_SDK_TRACE = True
except ImportError:
    TracerProvider = None  # type: ignore[assignment,misc]
    BatchSpanProcessor = None  # type: ignore[assignment,misc]

try:
    from opentelemetry.sdk.metrics import MeterProvider  # noqa: F401
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader  # noqa: F401

    _HAS_OTEL_SDK_METRICS = True
except ImportError:
    MeterProvider = None  # type: ignore[assignment,misc]
    PeriodicExportingMetricReader = None  # type: ignore[assignment,misc]

try:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (  # noqa: F401
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: F401
        OTLPSpanExporter,
    )

    _HAS_OTEL_EXPORTER = True
except ImportError:
    OTLPSpanExporter = None  # type: ignore[assignment,misc]
    OTLPMetricExporter = None  # type: ignore[assignment,misc]

_HAS_OTEL_LOG_EXPORTER = False
try:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (  # noqa: F401
        OTLPLogExporter,
    )
    from opentelemetry.sdk._logs import LoggerProvider  # noqa: F401
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor  # noqa: F401

    _HAS_OTEL_LOG_EXPORTER = True
except ImportError:
    OTLPLogExporter = None  # type: ignore[assignment,misc]
    LoggerProvider = None  # type: ignore[assignment,misc]
    BatchLogRecordProcessor = None  # type: ignore[assignment,misc]

try:
    from opentelemetry.sdk.resources import Resource  # noqa: F401

    _HAS_OTEL_RESOURCE = True
except ImportError:
    Resource = None  # type: ignore[assignment,misc]
    _HAS_OTEL_RESOURCE = False


def _otel_fully_available() -> bool:
    """Return True when all required OTel packages are importable."""
    return all(
        [
            _HAS_OTEL_TRACE,
            _HAS_OTEL_METRICS,
            _HAS_OTEL_SDK_TRACE,
            _HAS_OTEL_SDK_METRICS,
            _HAS_OTEL_EXPORTER,
            _HAS_OTEL_RESOURCE,
        ]
    )


# ---- state container ------------------------------------------------------


@dataclass
class ObservabilityState:
    """Holds references to OTel providers so they can be shut down later.

    When OTel is unavailable every field stays ``None`` and
    ``is_active`` returns ``False``.
    """

    tracer_provider: Any = field(default=None)
    meter_provider: Any = field(default=None)
    logger_provider: Any = field(default=None)
    is_active: bool = field(default=False)


# ---- public API ------------------------------------------------------------


def init_observability(config: ObservabilityConfig) -> ObservabilityState:
    """Initialise OpenTelemetry providers based on *config*.

    Returns an ``ObservabilityState`` that callers should keep alive for the
    duration of the process and pass to ``shutdown_observability`` on exit.

    If OTel packages are not installed **or** ``config.enabled`` is ``False``
    a no-op state is returned.
    """

    if not config.enabled:
        logger.info("Observability disabled by configuration")
        return ObservabilityState()

    if not _otel_fully_available():
        logger.warning("OpenTelemetry packages not installed -- running without telemetry")
        return ObservabilityState()

    # Build a Resource describing this service
    resource = Resource.create(
        {
            "service.name": config.service_name,
            "deployment.environment": config.environment,
        }
    )

    state = ObservabilityState(is_active=True)

    # -- traces --------------------------------------------------------------
    if config.enable_traces:
        span_exporter = OTLPSpanExporter(
            endpoint=config.otlp.endpoint,
            insecure=config.otlp.insecure,
            timeout=config.otlp.timeout_ms,
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        otel_trace.set_tracer_provider(tracer_provider)
        state.tracer_provider = tracer_provider

    # -- metrics -------------------------------------------------------------
    if config.enable_metrics:
        metric_exporter = OTLPMetricExporter(
            endpoint=config.otlp.endpoint,
            insecure=config.otlp.insecure,
            timeout=config.otlp.timeout_ms,
        )
        reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        otel_metrics.set_meter_provider(meter_provider)
        state.meter_provider = meter_provider

    # -- logs ----------------------------------------------------------------
    if config.enable_logs and _HAS_OTEL_LOG_EXPORTER:
        log_exporter = OTLPLogExporter(
            endpoint=config.otlp.endpoint,
            insecure=config.otlp.insecure,
            timeout=config.otlp.timeout_ms,
        )
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(log_exporter)
        )
        # Bridge Python stdlib logging → OTel log exporter
        from opentelemetry.sdk._logs import LoggingHandler

        otel_handler = LoggingHandler(
            level=logging.DEBUG,
            logger_provider=logger_provider,
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(otel_handler)
        # Ensure root logger level allows INFO+ through to OTel handler
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        state.logger_provider = logger_provider

    logger.info(
        "Observability initialised (traces=%s, metrics=%s, logs=%s)",
        config.enable_traces,
        config.enable_metrics,
        config.enable_logs and _HAS_OTEL_LOG_EXPORTER,
    )
    return state


def shutdown_observability(state: ObservabilityState) -> None:
    """Flush pending telemetry data and shut down providers."""
    if not state.is_active:
        return

    if state.tracer_provider is not None:
        try:
            state.tracer_provider.shutdown()
        except Exception:
            logger.exception("Error shutting down tracer provider")

    if state.meter_provider is not None:
        try:
            state.meter_provider.shutdown()
        except Exception:
            logger.exception("Error shutting down meter provider")

    if state.logger_provider is not None:
        try:
            state.logger_provider.shutdown()
        except Exception:
            logger.exception("Error shutting down logger provider")

    state.is_active = False
    logger.info("Observability shut down")
