"""Kafka event producer for persistent event streaming (MET-197).

Publishes ``Event`` instances to the appropriate Kafka topic based on
``EventType``.  Degrades gracefully when Kafka is unavailable — a warning
is logged and the event is silently dropped rather than crashing the caller.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from observability.tracing import get_tracer
from orchestrator.event_bus.bus import (
    TOPIC_AGENT_CHAT,
    TOPIC_AGENT_EVENTS,
    TOPIC_APPROVAL_EVENTS,
    TOPIC_SESSION_EVENTS,
    TOPIC_TWIN_EVENTS,
    TopicConfig,
)
from orchestrator.event_bus.events import Event, EventType

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.event_bus.kafka_producer")

# ---------------------------------------------------------------------------
# EventType → TopicConfig routing
# ---------------------------------------------------------------------------

_TOPIC_ROUTING: dict[EventType, TopicConfig] = {
    # Twin events
    EventType.WORK_PRODUCT_CREATED: TOPIC_TWIN_EVENTS,
    EventType.WORK_PRODUCT_UPDATED: TOPIC_TWIN_EVENTS,
    EventType.WORK_PRODUCT_DELETED: TOPIC_TWIN_EVENTS,
    EventType.CONSTRAINT_VIOLATED: TOPIC_TWIN_EVENTS,
    EventType.BRANCH_CREATED: TOPIC_TWIN_EVENTS,
    EventType.BRANCH_MERGED: TOPIC_TWIN_EVENTS,
    # Session events
    EventType.SESSION_STARTED: TOPIC_SESSION_EVENTS,
    EventType.SESSION_COMPLETED: TOPIC_SESSION_EVENTS,
    EventType.SESSION_FAILED: TOPIC_SESSION_EVENTS,
    # Agent events
    EventType.AGENT_TASK_STARTED: TOPIC_AGENT_EVENTS,
    EventType.AGENT_TASK_COMPLETED: TOPIC_AGENT_EVENTS,
    EventType.AGENT_TASK_FAILED: TOPIC_AGENT_EVENTS,
    # Approval events
    EventType.APPROVAL_REQUESTED: TOPIC_APPROVAL_EVENTS,
    EventType.APPROVAL_GRANTED: TOPIC_APPROVAL_EVENTS,
    EventType.APPROVAL_REJECTED: TOPIC_APPROVAL_EVENTS,
    # Gate events (MET-171)
    EventType.GATE_REQUESTED: TOPIC_APPROVAL_EVENTS,
    EventType.GATE_APPROVED: TOPIC_APPROVAL_EVENTS,
    EventType.GATE_REJECTED: TOPIC_APPROVAL_EVENTS,
    # Chat events
    EventType.CHAT_MESSAGE_SENT: TOPIC_AGENT_CHAT,
    EventType.CHAT_MESSAGE_CHUNK: TOPIC_AGENT_CHAT,
    EventType.CHAT_THREAD_CREATED: TOPIC_AGENT_CHAT,
    EventType.CHAT_AGENT_TYPING: TOPIC_AGENT_CHAT,
}


def resolve_topic(event_type: EventType) -> TopicConfig:
    """Return the Kafka topic for a given event type."""
    topic = _TOPIC_ROUTING.get(event_type)
    if topic is None:
        raise ValueError(f"No topic mapping for event type {event_type!r}")
    return topic


# ---------------------------------------------------------------------------
# KafkaEventPublisher
# ---------------------------------------------------------------------------


class KafkaEventPublisher:
    """Async Kafka producer that publishes serialised ``Event`` payloads.

    Parameters
    ----------
    bootstrap_servers:
        Comma-separated Kafka broker addresses (e.g. ``"localhost:9092"``).
    client_id:
        Optional Kafka client identifier.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        client_id: str = "metaforge-producer",
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._producer: Any | None = None
        self._started = False

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the underlying ``AIOKafkaProducer``."""
        if self._started:
            return
        try:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                client_id=self._client_id,
                value_serializer=lambda v: v.encode("utf-8") if isinstance(v, str) else v,
                key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            )
            await self._producer.start()
            self._started = True
            logger.info(
                "kafka_producer_started",
                bootstrap_servers=self._bootstrap_servers,
                client_id=self._client_id,
            )
        except Exception as exc:
            logger.warning(
                "kafka_producer_start_failed",
                error=str(exc),
                bootstrap_servers=self._bootstrap_servers,
            )
            self._producer = None
            self._started = False

    async def stop(self) -> None:
        """Stop the underlying ``AIOKafkaProducer``."""
        if self._producer is not None:
            try:
                await self._producer.stop()
                logger.info("kafka_producer_stopped")
            except Exception as exc:
                logger.warning("kafka_producer_stop_error", error=str(exc))
            finally:
                self._producer = None
                self._started = False

    # -- publishing ----------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Serialize *event* and send it to the matching Kafka topic.

        If Kafka is unavailable the error is logged and the call returns
        without raising — callers are never blocked by Kafka failures.
        """
        with tracer.start_as_current_span("kafka.produce") as span:
            topic_cfg = resolve_topic(event.type)
            topic_name = topic_cfg.name
            message_key = event.data.get("source_id", event.id)

            span.set_attribute("messaging.destination", topic_name)
            span.set_attribute("messaging.message_id", event.id)
            span.set_attribute("event.type", str(event.type))

            if self._producer is None:
                logger.warning(
                    "kafka_publish_skipped",
                    reason="producer_not_started",
                    event_id=event.id,
                    event_type=str(event.type),
                )
                return

            t0 = time.monotonic()
            try:
                value = event.model_dump_json()
                await self._producer.send_and_wait(
                    topic=topic_name,
                    value=value,
                    key=message_key,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                span.set_attribute("kafka.produce.duration_ms", elapsed_ms)

                logger.info(
                    "kafka_event_produced",
                    topic=topic_name,
                    event_id=event.id,
                    event_type=str(event.type),
                    duration_ms=round(elapsed_ms, 2),
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.warning(
                    "kafka_publish_failed",
                    error=str(exc),
                    event_id=event.id,
                    event_type=str(event.type),
                    topic=topic_name,
                )

    @property
    def is_started(self) -> bool:
        """Return ``True`` if the producer is running."""
        return self._started
