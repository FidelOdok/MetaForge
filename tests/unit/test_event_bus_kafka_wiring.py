"""The Kafka event bus is reachable and correctly configured (MET-197 wiring).

MET-197 landed the publisher on 2026-03-08 and nothing ever constructed it:
``KAFKA_BOOTSTRAP_SERVERS`` was passed to the container and read by zero
Python code, so the broker ran with **zero topics ever created** while the
in-process bus carried every event. These tests pin the wiring contract so
the durable path cannot silently disappear again.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import (
    AuditEventSubscriber,
    EventBus,
    create_default_bus,
    create_kafka_bus,
)


class _Collector:
    def __init__(self) -> None:
        self.produced: list[str] = []

    def record_message_produced(self, topic: str) -> None:
        self.produced.append(topic)


class _Publisher:
    def __init__(self) -> None:
        self.published: list[Event] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def publish(self, event: Event) -> None:
        self.published.append(event)


def _event() -> Event:
    return Event(
        id="evt-1",
        type=EventType.WORK_PRODUCT_CREATED,
        timestamp="2026-09-07T00:00:00+00:00",
        source="test",
        data={"work_product_id": "wp-1", "content": "x"},
    )


def test_kafka_bus_carries_a_publisher_and_the_default_subscribers():
    bus, publisher = create_kafka_bus(bootstrap_servers="kafka:29092")

    assert publisher is not None
    # Audit is always on, same as the in-process bus.
    assert bus.subscriber_count >= 1


def test_kafka_bus_forwards_the_metrics_collector():
    # The factory previously hardcoded ``collector=None``, so switching to
    # Kafka would have silently dropped the event-bus metric the in-process
    # bus reports. Wiring the durable path must not cost observability.
    collector = _Collector()

    bus, _publisher = create_kafka_bus(bootstrap_servers="kafka:29092", collector=collector)

    assert bus._collector is collector  # noqa: SLF001


def test_kafka_bus_subscribes_the_knowledge_consumer_when_a_service_is_given():
    class _Service:
        async def ingest(self, **kw: Any) -> Any: ...
        async def delete_by_source(self, source_path: str) -> int:
            return 0

    plain, _ = create_kafka_bus(bootstrap_servers="kafka:29092")
    with_knowledge, _ = create_kafka_bus(
        bootstrap_servers="kafka:29092", knowledge_service=_Service()
    )

    # The deposit path that MET-567 depends on must exist on the durable bus
    # too, not only on the in-process one.
    assert with_knowledge.subscriber_count == plain.subscriber_count + 1


@pytest.mark.asyncio
async def test_publishing_reaches_both_the_subscribers_and_the_topic():
    # The durable bus is ADDITIVE: in-process dispatch is unchanged and the
    # event is also persisted, so replay becomes possible without requiring a
    # separate consumer process first.
    publisher = _Publisher()
    collector = _Collector()
    bus = EventBus(kafka_publisher=publisher, collector=collector)
    seen: list[Event] = []

    class _Sub(AuditEventSubscriber):
        @property
        def subscriber_id(self) -> str:
            return "spy"

        async def on_event(self, event: Event) -> None:
            seen.append(event)

    bus.subscribe(_Sub())
    await bus.publish(_event())

    assert [e.id for e in seen] == ["evt-1"]
    assert [e.id for e in publisher.published] == ["evt-1"]
    assert collector.produced == ["eventbus"]


@pytest.mark.asyncio
async def test_a_broken_publisher_never_breaks_a_publish():
    # A broker outage must not take the gateway with it: the in-process bus is
    # a complete implementation, just not durable.
    class _Broken(_Publisher):
        async def publish(self, event: Event) -> None:
            raise RuntimeError("broker unreachable")

    seen: list[Event] = []
    bus = EventBus(kafka_publisher=_Broken())

    class _Sub(AuditEventSubscriber):
        @property
        def subscriber_id(self) -> str:
            return "spy"

        async def on_event(self, event: Event) -> None:
            seen.append(event)

    bus.subscribe(_Sub())
    await bus.publish(_event())

    assert [e.id for e in seen] == ["evt-1"]


def test_the_in_process_bus_has_no_publisher():
    bus = create_default_bus()

    assert bus._kafka_publisher is None  # noqa: SLF001


class TestHonestReadiness:
    """``start()`` never raises, so ``started`` is what tells the truth.

    Caught live while testing end to end: the gateway logged
    ``event_bus_kafka_initialized`` while ``aiokafka`` was absent from the
    image and every event was being dropped. ``start()`` swallows a missing
    SDK and an unreachable broker alike (correctly -- neither should fail
    boot), which makes a public readiness signal load-bearing rather than
    cosmetic. Without it the wiring reproduces the exact false positive that
    let this tier stay dark for six months.
    """

    @pytest.mark.asyncio
    async def test_started_is_false_when_the_sdk_or_broker_is_missing(self):
        from orchestrator.event_bus.kafka_producer import KafkaEventPublisher

        publisher = KafkaEventPublisher(bootstrap_servers="nonexistent-broker:9092")

        # Never raises, by design.
        await publisher.start()

        # But it must not claim to be running.
        assert publisher.started is False

    @pytest.mark.asyncio
    async def test_started_is_true_only_with_a_live_producer(self):
        from orchestrator.event_bus.kafka_producer import KafkaEventPublisher

        publisher = KafkaEventPublisher(bootstrap_servers="kafka:29092")
        assert publisher.started is False

        class _Producer:
            async def stop(self) -> None: ...

        publisher._producer = _Producer()  # noqa: SLF001
        publisher._started = True  # noqa: SLF001
        assert publisher.started is True

        # A stopped producer is not started, even if the flag lingers.
        await publisher.stop()
        assert publisher.started is False
