"""Kafka event consumer for persistent event streaming (MET-197).

Subscribes to configured Kafka topics, deserialises JSON payloads back into
``Event`` Pydantic models, and dispatches them to registered
``EventSubscriber`` instances.  Failed messages are forwarded to a dead-letter
queue (DLQ) topic after exhausting retries.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from observability.tracing import get_tracer
from orchestrator.event_bus.bus import ALL_TOPICS
from orchestrator.event_bus.events import Event
from orchestrator.event_bus.subscribers import EventSubscriber

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.event_bus.kafka_consumer")

_DEFAULT_DLQ_TOPIC = "metaforge.dlq"
_DEFAULT_MAX_RETRIES = 3


class KafkaEventConsumer:
    """Async Kafka consumer that dispatches deserialised events to subscribers.

    Parameters
    ----------
    bootstrap_servers:
        Comma-separated Kafka broker addresses.
    group_id:
        Consumer group identifier for coordinated consumption.
    topics:
        List of topic names to subscribe to.  Defaults to all configured
        topics from ``bus.ALL_TOPICS``.
    dlq_topic:
        Dead-letter queue topic name.
    max_retries:
        Number of dispatch attempts before sending to DLQ.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        group_id: str = "metaforge-consumers",
        topics: list[str] | None = None,
        dlq_topic: str = _DEFAULT_DLQ_TOPIC,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._topics = topics or [t.name for t in ALL_TOPICS]
        self._dlq_topic = dlq_topic
        self._max_retries = max_retries

        self._consumer: Any | None = None
        self._dlq_producer: Any | None = None
        self._started = False
        self._running = False
        self._subscribers: dict[str, EventSubscriber] = {}

    # -- subscriber management -----------------------------------------------

    def register_subscriber(self, subscriber: EventSubscriber) -> None:
        """Register an ``EventSubscriber`` for dispatched events."""
        self._subscribers[subscriber.subscriber_id] = subscriber
        logger.debug(
            "consumer_subscriber_registered",
            subscriber_id=subscriber.subscriber_id,
        )

    def unregister_subscriber(self, subscriber_id: str) -> None:
        """Remove a previously registered subscriber."""
        removed = self._subscribers.pop(subscriber_id, None)
        if removed:
            logger.debug("consumer_subscriber_removed", subscriber_id=subscriber_id)

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the Kafka consumer and DLQ producer."""
        if self._started:
            return
        try:
            from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

            self._consumer = AIOKafkaConsumer(
                *self._topics,
                bootstrap_servers=self._bootstrap_servers,
                group_id=self._group_id,
                value_deserializer=lambda v: v.decode("utf-8") if isinstance(v, bytes) else v,
                enable_auto_commit=True,
                auto_offset_reset="earliest",
            )
            self._dlq_producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                client_id=f"{self._group_id}-dlq-producer",
                value_serializer=lambda v: v.encode("utf-8") if isinstance(v, str) else v,
                key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            )
            await self._consumer.start()
            await self._dlq_producer.start()
            self._started = True
            logger.info(
                "kafka_consumer_started",
                bootstrap_servers=self._bootstrap_servers,
                group_id=self._group_id,
                topics=self._topics,
            )
        except Exception as exc:
            logger.warning(
                "kafka_consumer_start_failed",
                error=str(exc),
                bootstrap_servers=self._bootstrap_servers,
            )
            self._consumer = None
            self._dlq_producer = None
            self._started = False

    async def stop(self) -> None:
        """Stop the consumer and DLQ producer."""
        self._running = False
        if self._consumer is not None:
            try:
                await self._consumer.stop()
            except Exception as exc:
                logger.warning("kafka_consumer_stop_error", error=str(exc))
            finally:
                self._consumer = None
        if self._dlq_producer is not None:
            try:
                await self._dlq_producer.stop()
            except Exception as exc:
                logger.warning("kafka_dlq_producer_stop_error", error=str(exc))
            finally:
                self._dlq_producer = None
        self._started = False
        logger.info("kafka_consumer_stopped", group_id=self._group_id)

    # -- consumption ---------------------------------------------------------

    async def consume_forever(self) -> None:
        """Poll Kafka and dispatch events until ``stop()`` is called.

        This is a long-running coroutine intended to be wrapped in an
        ``asyncio.Task``.
        """
        if not self._started or self._consumer is None:
            logger.warning("kafka_consume_skipped", reason="consumer_not_started")
            return

        self._running = True
        logger.info("kafka_consume_loop_started", group_id=self._group_id)

        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                await self._handle_message(msg)
        except asyncio.CancelledError:
            logger.info("kafka_consume_loop_cancelled", group_id=self._group_id)
        except Exception as exc:
            logger.exception(
                "kafka_consume_loop_error",
                error=str(exc),
                group_id=self._group_id,
            )

    async def _handle_message(self, msg: Any) -> None:
        """Deserialise a single Kafka message and dispatch to subscribers."""
        with tracer.start_as_current_span("kafka.consume") as span:
            topic = msg.topic
            span.set_attribute("messaging.destination", topic)
            span.set_attribute("messaging.consumer_group", self._group_id)

            raw_value = msg.value
            t0 = time.monotonic()

            try:
                event = Event.model_validate_json(raw_value)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning(
                    "kafka_event_deserialize_failed",
                    topic=topic,
                    error=str(exc),
                    raw_value=raw_value[:200]
                    if isinstance(raw_value, str)
                    else str(raw_value)[:200],
                )
                await self._send_to_dlq(raw_value, topic, str(exc))
                return

            span.set_attribute("event.type", str(event.type))
            span.set_attribute("event.id", event.id)

            success = await self._dispatch_with_retries(event)
            elapsed_ms = (time.monotonic() - t0) * 1000

            if success:
                logger.info(
                    "kafka_event_consumed",
                    topic=topic,
                    event_id=event.id,
                    event_type=str(event.type),
                    group_id=self._group_id,
                    duration_ms=round(elapsed_ms, 2),
                )
            else:
                logger.warning(
                    "kafka_event_dispatch_exhausted",
                    topic=topic,
                    event_id=event.id,
                    event_type=str(event.type),
                    max_retries=self._max_retries,
                )
                await self._send_to_dlq(
                    event.model_dump_json(), topic, "dispatch_retries_exhausted"
                )

    async def _dispatch_with_retries(self, event: Event) -> bool:
        """Dispatch *event* to matching subscribers with retry logic.

        Returns ``True`` if at least one attempt succeeds without all
        subscribers failing.
        """
        for attempt in range(1, self._max_retries + 1):
            failures = 0
            total = 0
            for sub in list(self._subscribers.values()):
                if sub.event_types is not None and event.type not in sub.event_types:
                    continue
                total += 1
                try:
                    await sub.on_event(event)
                except Exception:
                    failures += 1
                    logger.exception(
                        "consumer_subscriber_error",
                        subscriber_id=sub.subscriber_id,
                        event_id=event.id,
                        attempt=attempt,
                    )
            # If no subscribers matched, consider it successful
            if total == 0 or failures < total:
                return True
            # All subscribers failed — retry after a brief back-off
            if attempt < self._max_retries:
                await asyncio.sleep(0.1 * attempt)
        return False

    async def _send_to_dlq(self, value: str, source_topic: str, error: str) -> None:
        """Forward a failed message to the dead-letter queue topic."""
        if self._dlq_producer is None:
            logger.warning("kafka_dlq_skipped", reason="dlq_producer_not_available")
            return
        try:
            import json

            dlq_payload = json.dumps(
                {
                    "source_topic": source_topic,
                    "error": error,
                    "original_value": value,
                }
            )
            await self._dlq_producer.send_and_wait(
                topic=self._dlq_topic,
                value=dlq_payload,
            )
            logger.info(
                "kafka_event_sent_to_dlq",
                source_topic=source_topic,
                dlq_topic=self._dlq_topic,
                error=error,
            )
        except Exception as exc:
            logger.warning(
                "kafka_dlq_send_failed",
                error=str(exc),
                source_topic=source_topic,
            )

    @property
    def is_started(self) -> bool:
        """Return ``True`` if the consumer is running."""
        return self._started

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the consume loop is active."""
        return self._running
