"""Tests for Kafka event persistence — producer, consumer, and EventBus integration (MET-197)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from orchestrator.event_bus.bus import (
    TOPIC_AGENT_CHAT,
    TOPIC_AGENT_EVENTS,
    TOPIC_APPROVAL_EVENTS,
    TOPIC_SESSION_EVENTS,
    TOPIC_TWIN_EVENTS,
)
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.kafka_consumer import KafkaEventConsumer
from orchestrator.event_bus.kafka_producer import KafkaEventPublisher, resolve_topic
from orchestrator.event_bus.subscribers import EventBus, EventSubscriber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: EventType = EventType.WORK_PRODUCT_CREATED) -> Event:
    return Event(
        id=str(uuid4()),
        type=event_type,
        timestamp="2026-03-08T12:00:00Z",
        source="test",
    )


class _StubSubscriber(EventSubscriber):
    """Collects received events for assertion."""

    def __init__(self, *, fail: bool = False) -> None:
        self.received: list[Event] = []
        self._fail = fail

    @property
    def subscriber_id(self) -> str:
        return "stub"

    @property
    def event_types(self) -> set[EventType] | None:
        return None

    async def on_event(self, event: Event) -> None:
        if self._fail:
            raise RuntimeError("subscriber boom")
        self.received.append(event)


class _NamedStubSubscriber(EventSubscriber):
    """Stub subscriber with a configurable name and optional failure."""

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self._name = name
        self._fail = fail
        self.received: list[Event] = []

    @property
    def subscriber_id(self) -> str:
        return self._name

    @property
    def event_types(self) -> set[EventType] | None:
        return None

    async def on_event(self, event: Event) -> None:
        if self._fail:
            raise RuntimeError(f"{self._name} failure")
        self.received.append(event)


class _FailingSubscriber(EventSubscriber):
    """Always fails — used to test DLQ path."""

    @property
    def subscriber_id(self) -> str:
        return "always_fail"

    @property
    def event_types(self) -> set[EventType] | None:
        return None

    async def on_event(self, event: Event) -> None:
        raise RuntimeError("permanent failure")


# ---------------------------------------------------------------------------
# TestResolveTopicRouting
# ---------------------------------------------------------------------------


class TestResolveTopicRouting:
    """Verify EventType → TopicConfig mapping."""

    @pytest.mark.parametrize(
        "event_type,expected_topic",
        [
            (EventType.WORK_PRODUCT_CREATED, TOPIC_TWIN_EVENTS),
            (EventType.WORK_PRODUCT_UPDATED, TOPIC_TWIN_EVENTS),
            (EventType.WORK_PRODUCT_DELETED, TOPIC_TWIN_EVENTS),
            (EventType.CONSTRAINT_VIOLATED, TOPIC_TWIN_EVENTS),
            (EventType.BRANCH_CREATED, TOPIC_TWIN_EVENTS),
            (EventType.BRANCH_MERGED, TOPIC_TWIN_EVENTS),
            (EventType.SESSION_STARTED, TOPIC_SESSION_EVENTS),
            (EventType.SESSION_COMPLETED, TOPIC_SESSION_EVENTS),
            (EventType.SESSION_FAILED, TOPIC_SESSION_EVENTS),
            (EventType.AGENT_TASK_STARTED, TOPIC_AGENT_EVENTS),
            (EventType.AGENT_TASK_COMPLETED, TOPIC_AGENT_EVENTS),
            (EventType.AGENT_TASK_FAILED, TOPIC_AGENT_EVENTS),
            (EventType.APPROVAL_REQUESTED, TOPIC_APPROVAL_EVENTS),
            (EventType.APPROVAL_GRANTED, TOPIC_APPROVAL_EVENTS),
            (EventType.APPROVAL_REJECTED, TOPIC_APPROVAL_EVENTS),
            (EventType.CHAT_MESSAGE_SENT, TOPIC_AGENT_CHAT),
            (EventType.CHAT_MESSAGE_CHUNK, TOPIC_AGENT_CHAT),
            (EventType.CHAT_THREAD_CREATED, TOPIC_AGENT_CHAT),
            (EventType.CHAT_AGENT_TYPING, TOPIC_AGENT_CHAT),
        ],
    )
    def test_topic_routing(self, event_type: EventType, expected_topic: Any) -> None:
        assert resolve_topic(event_type) is expected_topic

    def test_all_event_types_routed(self) -> None:
        """Every EventType member must have a topic mapping."""
        for et in EventType:
            topic = resolve_topic(et)
            assert topic is not None


# ---------------------------------------------------------------------------
# TestKafkaEventPublisher
# ---------------------------------------------------------------------------


class TestKafkaEventPublisher:
    """KafkaEventPublisher unit tests with mocked aiokafka."""

    async def test_publish_sends_to_correct_topic(self) -> None:
        publisher = KafkaEventPublisher()
        mock_producer = AsyncMock()
        publisher._producer = mock_producer
        publisher._started = True

        event = _make_event(EventType.SESSION_STARTED)
        await publisher.publish(event)

        mock_producer.send_and_wait.assert_awaited_once()
        call_kwargs = mock_producer.send_and_wait.call_args
        assert call_kwargs.kwargs["topic"] == "session.events"

    async def test_publish_serialises_event_as_json(self) -> None:
        publisher = KafkaEventPublisher()
        mock_producer = AsyncMock()
        publisher._producer = mock_producer
        publisher._started = True

        event = _make_event(EventType.WORK_PRODUCT_UPDATED)
        await publisher.publish(event)

        call_kwargs = mock_producer.send_and_wait.call_args
        value = call_kwargs.kwargs["value"]
        parsed = json.loads(value)
        assert parsed["id"] == event.id
        assert parsed["type"] == str(EventType.WORK_PRODUCT_UPDATED)

    async def test_publish_uses_event_id_as_key(self) -> None:
        publisher = KafkaEventPublisher()
        mock_producer = AsyncMock()
        publisher._producer = mock_producer
        publisher._started = True

        event = _make_event()
        await publisher.publish(event)

        call_kwargs = mock_producer.send_and_wait.call_args
        assert call_kwargs.kwargs["key"] == event.id

    async def test_publish_uses_source_id_as_key_when_present(self) -> None:
        publisher = KafkaEventPublisher()
        mock_producer = AsyncMock()
        publisher._producer = mock_producer
        publisher._started = True

        event = _make_event()
        event.data = {"source_id": "custom-key"}
        await publisher.publish(event)

        call_kwargs = mock_producer.send_and_wait.call_args
        assert call_kwargs.kwargs["key"] == "custom-key"

    async def test_publish_skips_when_producer_not_started(self) -> None:
        """If Kafka is unavailable the publish should not raise."""
        publisher = KafkaEventPublisher()
        event = _make_event()
        # Should not raise
        await publisher.publish(event)

    async def test_publish_handles_send_failure(self) -> None:
        """A Kafka send failure should be logged, not raised."""
        publisher = KafkaEventPublisher()
        mock_producer = AsyncMock()
        mock_producer.send_and_wait.side_effect = RuntimeError("broker down")
        publisher._producer = mock_producer
        publisher._started = True

        event = _make_event()
        # Should not raise
        await publisher.publish(event)

    async def test_start_graceful_degradation(self) -> None:
        """start() should not crash when aiokafka import fails."""
        with patch.dict("sys.modules", {"aiokafka": None}):
            publisher = KafkaEventPublisher()
            await publisher.start()
            assert not publisher.is_started

    async def test_stop_when_not_started(self) -> None:
        """stop() should be safe to call even when not started."""
        publisher = KafkaEventPublisher()
        await publisher.stop()
        assert not publisher.is_started

    async def test_stop_calls_producer_stop(self) -> None:
        publisher = KafkaEventPublisher()
        mock_producer = AsyncMock()
        publisher._producer = mock_producer
        publisher._started = True

        await publisher.stop()
        mock_producer.stop.assert_awaited_once()
        assert not publisher.is_started


# ---------------------------------------------------------------------------
# TestKafkaEventConsumer
# ---------------------------------------------------------------------------


class TestKafkaEventConsumer:
    """KafkaEventConsumer unit tests with mocked aiokafka."""

    async def test_register_subscriber(self) -> None:
        consumer = KafkaEventConsumer()
        stub = _StubSubscriber()
        consumer.register_subscriber(stub)
        assert "stub" in consumer._subscribers

    async def test_unregister_subscriber(self) -> None:
        consumer = KafkaEventConsumer()
        stub = _StubSubscriber()
        consumer.register_subscriber(stub)
        consumer.unregister_subscriber("stub")
        assert "stub" not in consumer._subscribers

    async def test_handle_message_dispatches_to_subscriber(self) -> None:
        consumer = KafkaEventConsumer()
        stub = _StubSubscriber()
        consumer.register_subscriber(stub)

        event = _make_event(EventType.AGENT_TASK_COMPLETED)
        msg = MagicMock()
        msg.topic = "agent.events"
        msg.value = event.model_dump_json()

        await consumer._handle_message(msg)
        assert len(stub.received) == 1
        assert stub.received[0].id == event.id

    async def test_handle_message_bad_json_sends_to_dlq(self) -> None:
        consumer = KafkaEventConsumer()
        consumer._dlq_producer = AsyncMock()

        msg = MagicMock()
        msg.topic = "agent.events"
        msg.value = "not valid json{{"

        await consumer._handle_message(msg)
        consumer._dlq_producer.send_and_wait.assert_awaited_once()

    async def test_dispatch_retries_on_all_subscribers_failing(self) -> None:
        consumer = KafkaEventConsumer(max_retries=2)
        failing = _FailingSubscriber()
        consumer.register_subscriber(failing)
        consumer._dlq_producer = AsyncMock()

        event = _make_event()
        msg = MagicMock()
        msg.topic = "twin.events"
        msg.value = event.model_dump_json()

        await consumer._handle_message(msg)
        # Should have sent to DLQ after retries exhausted
        consumer._dlq_producer.send_and_wait.assert_awaited_once()

    async def test_dispatch_succeeds_with_partial_subscriber_failure(self) -> None:
        consumer = KafkaEventConsumer(max_retries=1)
        good = _NamedStubSubscriber("good")
        bad = _NamedStubSubscriber("bad", fail=True)
        consumer.register_subscriber(good)
        consumer.register_subscriber(bad)
        consumer._dlq_producer = AsyncMock()

        event = _make_event()
        msg = MagicMock()
        msg.topic = "twin.events"
        msg.value = event.model_dump_json()

        await consumer._handle_message(msg)
        # Partial failure = success, so no DLQ
        assert len(good.received) == 1

    async def test_dlq_payload_structure(self) -> None:
        consumer = KafkaEventConsumer()
        consumer._dlq_producer = AsyncMock()

        msg = MagicMock()
        msg.topic = "session.events"
        msg.value = "broken"

        await consumer._handle_message(msg)

        call_kwargs = consumer._dlq_producer.send_and_wait.call_args
        dlq_value = json.loads(call_kwargs.kwargs["value"])
        assert dlq_value["source_topic"] == "session.events"
        assert "error" in dlq_value
        assert "original_value" in dlq_value

    async def test_consumer_defaults_to_all_topics(self) -> None:
        consumer = KafkaEventConsumer()
        assert len(consumer._topics) == 5

    async def test_consumer_custom_topics(self) -> None:
        consumer = KafkaEventConsumer(topics=["twin.events", "agent.events"])
        assert consumer._topics == ["twin.events", "agent.events"]

    async def test_consume_forever_skips_when_not_started(self) -> None:
        consumer = KafkaEventConsumer()
        # Should return immediately without error
        await consumer.consume_forever()

    async def test_stop_when_not_started(self) -> None:
        consumer = KafkaEventConsumer()
        await consumer.stop()
        assert not consumer.is_started

    async def test_start_graceful_degradation(self) -> None:
        """start() should not crash when aiokafka is unavailable."""
        with patch.dict("sys.modules", {"aiokafka": None}):
            consumer = KafkaEventConsumer()
            await consumer.start()
            assert not consumer.is_started

    async def test_consumer_group_id(self) -> None:
        consumer = KafkaEventConsumer(group_id="my-group")
        assert consumer._group_id == "my-group"


# ---------------------------------------------------------------------------
# TestEventBusKafkaIntegration
# ---------------------------------------------------------------------------


class TestEventBusKafkaIntegration:
    """Verify EventBus forwards events to KafkaEventPublisher."""

    async def test_publish_forwards_to_kafka(self) -> None:
        mock_publisher = AsyncMock()
        bus = EventBus(kafka_publisher=mock_publisher)

        event = _make_event()
        await bus.publish(event)

        mock_publisher.publish.assert_awaited_once_with(event)

    async def test_publish_without_kafka(self) -> None:
        """EventBus works normally without a Kafka publisher."""
        bus = EventBus()
        event = _make_event()
        await bus.publish(event)
        assert bus.get_event_log()[-1].id == event.id

    async def test_kafka_failure_does_not_block_bus(self) -> None:
        """A Kafka publish error must not prevent in-memory dispatch."""
        mock_publisher = AsyncMock()
        mock_publisher.publish.side_effect = RuntimeError("kafka down")
        bus = EventBus(kafka_publisher=mock_publisher)

        stub = _StubSubscriber()
        bus.subscribe(stub)

        event = _make_event()
        await bus.publish(event)
        # Subscriber still received the event
        assert len(stub.received) == 1
        assert stub.received[0].id == event.id

    async def test_event_log_populated_with_kafka(self) -> None:
        mock_publisher = AsyncMock()
        bus = EventBus(kafka_publisher=mock_publisher)

        event = _make_event()
        await bus.publish(event)
        assert len(bus.get_event_log()) == 1
