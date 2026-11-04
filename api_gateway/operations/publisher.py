"""What producers call to report an operation's progress (FORGE-785).

``publish(project_id, operation_id, type, state, **fields)`` stores the event
in the durable log, folds it into the operation's snapshot and streams it to
the project's subscribers. It assigns ``sequence`` per operation (the next one
after the operation's last, read from the store in the append's own
transaction, so it survives a restart) unless the producer gives one.

Publishing is best effort and **never raises into the caller**: a run
transition or a tool job must not fail because its status line could not be
written. A refusal (out of order, or after the operation ended) and an error
are logged and counted, and ``None`` is returned.

The process-wide publisher is in-memory until the gateway's start-up calls
:func:`init_operations` with a file-backed store.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import structlog

from api_gateway.operations.hub import OperationHub
from api_gateway.operations.schemas import (
    OperationEvent,
    OperationEventIn,
    is_terminal,
    kind_of,
)
from api_gateway.operations.store import AppendResult, OperationStore
from observability.metrics import MetricsCollector
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.publisher")

__all__ = [
    "OperationPublisher",
    "get_operation_publisher",
    "init_operations",
    "publish",
    "publish_payload",
    "set_metrics_collector",
]

_collector: MetricsCollector | None = None


def set_metrics_collector(collector: MetricsCollector | None) -> None:
    """Wire the gateway's collector in (``server.py`` at start-up)."""
    global _collector  # noqa: PLW0603
    _collector = collector


def _metrics() -> MetricsCollector:
    return _collector if _collector is not None else MetricsCollector()


class OperationPublisher:
    """Stores events, folds snapshots and fans them out. Never raises."""

    def __init__(
        self,
        store: OperationStore,
        hub: OperationHub | None = None,
        *,
        metrics: Callable[[], MetricsCollector] = _metrics,
    ) -> None:
        self.store = store
        self.hub = hub if hub is not None else OperationHub(store, metrics=metrics)
        self._metrics = metrics
        try:
            for kind, count in store.count_active().items():
                metrics().record_operation_active(kind, count)
        except Exception as exc:  # noqa: BLE001
            logger.info("operation_active_gauge_unavailable", error=str(exc))

    def ingest(self, project_id: str, incoming: OperationEventIn) -> AppendResult:
        """Append one event; raises only what the store raises (the route's 5xx)."""
        kind = str(kind_of(incoming.operation_id) or "unknown")
        with tracer.start_as_current_span("operations.publish") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("operation_id", incoming.operation_id)
            span.set_attribute("event_type", str(incoming.type))
            previous = self.store.get(project_id, incoming.operation_id)
            try:
                result = self.store.append(project_id, incoming)
            except Exception as exc:
                span.record_exception(exc)
                self._metrics().record_operation_publish_failure(kind, "error")
                raise
            span.set_attribute("outcome", result.status)
        if result.status == "stored" and result.event is not None:
            self._after_store(result.event, previous_state=previous.state if previous else None)
        elif result.status == "refused":
            reason = "after_terminal" if "already" in (result.reason or "") else "out_of_order"
            self._metrics().record_operation_publish_failure(kind, reason)
            logger.info(
                "operation_event_refused",
                project_id=project_id,
                operation_id=incoming.operation_id,
                type=str(incoming.type),
                sequence=incoming.sequence,
                reason=result.reason,
            )
        return result

    def _after_store(self, event: OperationEvent, *, previous_state: Any) -> None:
        kind = str(event.kind)
        lag = 0.0
        if event.occurred_at is not None:
            lag = time.time() - event.occurred_at.timestamp()
        metrics = self._metrics()
        metrics.record_operation_event(kind, str(event.type), lag)
        was_active = previous_state is not None and not is_terminal(previous_state)
        now_active = not is_terminal(event.state)
        if now_active and not was_active:
            metrics.record_operation_active(kind, 1)
        elif was_active and not now_active:
            metrics.record_operation_active(kind, -1)
        log = logger.warning if str(event.severity) == "error" else logger.info
        log(
            "operation_event_published",
            project_id=event.project_id,
            project_seq=event.project_seq,
            operation_id=event.operation_id,
            sequence=event.sequence,
            type=str(event.type),
            state=str(event.state),
            stage_id=event.stage_id,
        )
        self.hub.publish(event)

    def publish(
        self,
        project_id: str,
        operation_id: str,
        type: str,  # noqa: A002 - the contract's field name
        state: str,
        **fields: Any,
    ) -> OperationEvent | None:
        """Store and stream one event. ``None`` when refused or on any error."""
        try:
            incoming = OperationEventIn(
                operation_id=operation_id,
                type=type,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                **fields,
            )
            result = self.ingest(project_id, incoming)
        except Exception as exc:  # noqa: BLE001 - publishing never fails the caller
            kind = str(kind_of(operation_id) or "unknown")
            try:
                self._metrics().record_operation_publish_failure(kind, "error")
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "operation_event_publish_failed",
                project_id=project_id,
                operation_id=operation_id,
                type=type,
                error=str(exc),
            )
            return None
        return result.event if result.status != "refused" else None

    def publish_payload(self, project_id: str, payload: dict[str, Any]) -> OperationEvent | None:
        """:meth:`publish` from a dict (the cross-process seam's shape)."""
        try:
            fields = dict(payload)
            fields.pop("project_id", None)
            operation_id = str(fields.pop("operation_id"))
            event_type = str(fields.pop("type"))
            state = str(fields.pop("state"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("operation_event_payload_invalid", project_id=project_id, error=str(exc))
            return None
        return self.publish(project_id, operation_id, event_type, state, **fields)


_publisher: OperationPublisher | None = None


def get_operation_publisher() -> OperationPublisher:
    """The process publisher; in-memory until :func:`init_operations` runs."""
    global _publisher  # noqa: PLW0603
    if _publisher is None:
        _publisher = OperationPublisher(OperationStore(":memory:"))
    return _publisher


def init_operations(store: OperationStore | None) -> OperationPublisher:
    """Install a publisher over ``store`` (``None``: a fresh in-memory one)."""
    global _publisher  # noqa: PLW0603
    _publisher = OperationPublisher(store if store is not None else OperationStore(":memory:"))
    logger.info("operation_store_opened", path=_publisher.store.path)
    return _publisher


def publish(
    project_id: str,
    operation_id: str,
    type: str,  # noqa: A002 - the contract's field name
    state: str,
    **fields: Any,
) -> OperationEvent | None:
    """Best-effort publish through the process publisher. Never raises."""
    try:
        publisher = get_operation_publisher()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation_store_unavailable", error=str(exc))
        return None
    return publisher.publish(project_id, operation_id, type, state, **fields)


def publish_payload(project_id: str, payload: dict[str, Any]) -> OperationEvent | None:
    """Best-effort publish of a dict event. Never raises."""
    try:
        publisher = get_operation_publisher()
    except Exception as exc:  # noqa: BLE001
        logger.warning("operation_store_unavailable", error=str(exc))
        return None
    return publisher.publish_payload(project_id, payload)
