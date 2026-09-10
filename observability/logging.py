"""structlog -> OTel logging bridge.

Provides a structlog processor that injects OpenTelemetry trace context
(``trace_id``, ``span_id``, ``trace_flags``) into every log event so that
logs can be correlated with distributed traces.

When OTel is not installed the processor is a no-op -- it passes the
event dict through unchanged without crashing.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from observability.config import ObservabilityConfig

# ---- optional OTel imports ------------------------------------------------

_HAS_OTEL_TRACE = False
_HAS_OTEL_CONTEXT = False

try:
    from opentelemetry import trace as otel_trace

    _HAS_OTEL_TRACE = True
except ImportError:
    otel_trace = None  # type: ignore[assignment]

try:
    from opentelemetry import context as otel_context  # noqa: F401

    _HAS_OTEL_CONTEXT = True
except ImportError:
    otel_context = None  # type: ignore[assignment]


def _otel_available() -> bool:
    return _HAS_OTEL_TRACE and _HAS_OTEL_CONTEXT


# ---- structlog processor --------------------------------------------------


def add_trace_context(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor that injects OTel trace context.

    If there is an active span the following keys are added:

    * ``trace_id`` -- hex-encoded 128-bit trace identifier
    * ``span_id``  -- hex-encoded 64-bit span identifier
    * ``trace_flags`` -- integer trace flags (usually ``1`` when sampled)

    If OTel is not installed or there is no active span the event dict is
    returned unchanged.
    """

    if not _otel_available():
        return event_dict

    span = otel_trace.get_current_span()
    if span is None:
        return event_dict

    span_context = span.get_span_context()
    if span_context is None or not span_context.is_valid:
        return event_dict

    event_dict["trace_id"] = format(span_context.trace_id, "032x")
    event_dict["span_id"] = format(span_context.span_id, "016x")
    event_dict["trace_flags"] = span_context.trace_flags

    return event_dict


# ---- configure structlog ---------------------------------------------------


class _ConsoleHandler(logging.StreamHandler):
    """Marker subclass so ``ensure_console_handler`` stays idempotent.

    Distinguishing this from an OTel ``LoggingHandler`` (or any other handler
    a caller may have attached) lets repeated ``configure_logging()`` calls
    (e.g. across ``uvicorn --reload`` cycles) avoid stacking duplicate
    handlers on the root logger.
    """


def ensure_console_handler() -> None:
    """Guarantee the root logger always has a working stdout sink (MET-646).

    Every structlog event in this codebase flows through
    ``structlog.stdlib.LoggerFactory()`` into the stdlib root logger. The only
    handler ever conditionally attached there is the OTel export handler
    (``observability/bootstrap.py`` / ``api_gateway/server.py``'s
    ``_reattach_otel_log_handler``) -- and uvicorn's own ``configure_logging()``
    clears root's handlers on every startup. Net effect, confirmed live on
    fidel-dev: whenever OTel logging is disabled, or its collector is simply
    unreachable (as found in MET-647), essentially every application log
    emitted after startup vanishes -- not in ``docker logs``, not in Loki,
    nowhere -- because OTel export is the *only* sink and it fails silently
    on the network call, not at attach time. A local stdout sink must not
    depend on a remote collector being up.
    """
    root = logging.getLogger()
    if any(isinstance(h, _ConsoleHandler) for h in root.handlers):
        return
    handler = _ConsoleHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)


def configure_logging(config: ObservabilityConfig) -> None:
    """Configure structlog with JSON rendering and optional trace context.

    The processor chain always includes timestamping, log-level addition,
    and JSON rendering.  When OTel is available (and ``config.enable_logs``
    is True) the ``add_trace_context`` processor is inserted so every log
    event carries correlation IDs. Always attaches a console handler to the
    root logger (see ``ensure_console_handler``) so output reaches
    stdout/``docker logs`` regardless of OTel availability -- OTel export is
    a supplementary sink, never a substitute for local visibility.
    """

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if config.enable_logs and _otel_available():
        processors.append(add_trace_context)

    processors.append(structlog.processors.UnicodeDecoder())
    processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    ensure_console_handler()
