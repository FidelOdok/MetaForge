"""In-memory event bus with subscriber pattern for the orchestrator.

Provides pub/sub for design change events. Subscribers register for
specific ``EventType`` values and receive matching events asynchronously.
Degrades gracefully when individual subscribers fail — one broken handler
never blocks delivery to others.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Any

import structlog

from observability.metrics import MetricsCollector
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType

if TYPE_CHECKING:
    from orchestrator.workflow_dag import WorkflowEngine

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.event_bus")

_MAX_EVENT_LOG = 10_000


class EventSubscriber(ABC):
    """Base class for event bus subscribers."""

    @property
    @abstractmethod
    def subscriber_id(self) -> str:
        """Unique subscriber identifier."""
        ...

    @property
    @abstractmethod
    def event_types(self) -> set[EventType] | None:
        """Event types this subscriber listens to.  ``None`` = all."""
        ...

    @abstractmethod
    async def on_event(self, event: Event) -> None:
        """Handle an incoming event."""
        ...


class EventBus:
    """In-memory event bus with filtered dispatch and audit log."""

    def __init__(self, collector: MetricsCollector | None = None) -> None:
        self._subscribers: dict[str, EventSubscriber] = {}
        self._event_log: deque[Event] = deque(maxlen=_MAX_EVENT_LOG)
        self._collector = collector

    def subscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers[subscriber.subscriber_id] = subscriber
        logger.debug(
            "subscriber_added",
            subscriber_id=subscriber.subscriber_id,
            event_types=(
                [str(t) for t in subscriber.event_types]
                if subscriber.event_types
                else "all"
            ),
        )

    def unsubscribe(self, subscriber_id: str) -> None:
        removed = self._subscribers.pop(subscriber_id, None)
        if removed:
            logger.debug("subscriber_removed", subscriber_id=subscriber_id)

    async def publish(self, event: Event) -> None:
        """Dispatch *event* to all matching subscribers."""
        with tracer.start_as_current_span("eventbus.publish") as span:
            span.set_attribute("event.type", str(event.type))
            span.set_attribute("event.id", event.id)

            self._event_log.append(event)
            dispatched = 0
            t0 = time.monotonic()

            for sub in list(self._subscribers.values()):
                if sub.event_types is not None and event.type not in sub.event_types:
                    continue
                try:
                    await sub.on_event(event)
                    dispatched += 1
                except Exception:
                    logger.exception(
                        "subscriber_error",
                        subscriber_id=sub.subscriber_id,
                        event_type=str(event.type),
                        event_id=event.id,
                    )

            elapsed_ms = (time.monotonic() - t0) * 1000
            span.set_attribute("eventbus.dispatched_count", dispatched)
            span.set_attribute("eventbus.duration_ms", elapsed_ms)

            if self._collector:
                self._collector.record_message_produced(topic="eventbus")

            logger.info(
                "event_published",
                event_type=str(event.type),
                event_id=event.id,
                dispatched=dispatched,
                duration_ms=round(elapsed_ms, 2),
            )

    def get_event_log(
        self,
        event_type: EventType | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Return recent events, optionally filtered by type."""
        events: list[Event] = list(self._event_log)
        if event_type is not None:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def clear(self) -> None:
        self._subscribers.clear()
        self._event_log.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# ---------------------------------------------------------------------------
# Built-in subscribers
# ---------------------------------------------------------------------------


class AuditEventSubscriber(EventSubscriber):
    """Logs every event via structlog for audit purposes."""

    @property
    def subscriber_id(self) -> str:
        return "audit"

    @property
    def event_types(self) -> set[EventType] | None:
        return None  # all events

    async def on_event(self, event: Event) -> None:
        logger.info(
            "audit_event",
            event_type=str(event.type),
            event_id=event.id,
            source=event.source,
            timestamp=event.timestamp,
        )


class WorkflowEventSubscriber(EventSubscriber):
    """Forwards agent task events to the workflow engine."""

    _WATCHED = {
        EventType.AGENT_TASK_STARTED,
        EventType.AGENT_TASK_COMPLETED,
        EventType.AGENT_TASK_FAILED,
    }

    def __init__(self, workflow_engine: WorkflowEngine) -> None:
        self._engine = workflow_engine

    @property
    def subscriber_id(self) -> str:
        return "workflow_forwarder"

    @property
    def event_types(self) -> set[EventType] | None:
        return self._WATCHED

    async def on_event(self, event: Event) -> None:
        run_id = event.data.get("run_id")
        step_id = event.data.get("step_id")
        if not run_id or not step_id:
            return

        from orchestrator.workflow_dag import StepStatus

        status_map: dict[EventType, StepStatus] = {
            EventType.AGENT_TASK_STARTED: StepStatus.RUNNING,
            EventType.AGENT_TASK_COMPLETED: StepStatus.COMPLETED,
            EventType.AGENT_TASK_FAILED: StepStatus.FAILED,
        }
        new_status = status_map.get(event.type)
        if new_status is None:
            return

        result_data: dict[str, Any] = {}
        if event.type == EventType.AGENT_TASK_COMPLETED:
            result_data = event.data.get("result", {})
        elif event.type == EventType.AGENT_TASK_FAILED:
            result_data = {"error": event.data.get("error", "unknown")}

        await self._engine.update_step(
            run_id=run_id,
            step_id=step_id,
            status=new_status,
            result=result_data,
        )
        logger.info(
            "workflow_step_updated",
            run_id=run_id,
            step_id=step_id,
            new_status=str(new_status),
        )


def create_default_bus(
    workflow_engine: WorkflowEngine | None = None,
    collector: MetricsCollector | None = None,
) -> EventBus:
    """Create an event bus with the standard subscriber set."""
    bus = EventBus(collector=collector)
    bus.subscribe(AuditEventSubscriber())
    if workflow_engine is not None:
        bus.subscribe(WorkflowEventSubscriber(workflow_engine))
    return bus
