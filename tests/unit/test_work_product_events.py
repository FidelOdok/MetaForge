"""Unit tests for ``WORK_PRODUCT_CREATED`` publication (MET-567).

``KnowledgeConsumer`` has subscribed to this event since MET-307 while nothing
published one, so a decision recorded via ``twin.record_decision`` never became
searchable knowledge. These tests cover the publisher and the end of that path:
a recorded decision reaching a consumer's ``ingest``.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.twin import work_product_events
from api_gateway.twin.work_product_events import (
    init_work_product_events,
    publish_work_product_created,
    work_product_events_enabled,
)
from orchestrator.event_bus.events import EventType


class RecordingBus:
    def __init__(self, *, raises: bool = False) -> None:
        self.events: list[Any] = []
        self._raises = raises

    async def publish(self, event: Any) -> None:
        self.events.append(event)
        if self._raises:
            raise RuntimeError("kafka is down")


@pytest.fixture(autouse=True)
def _reset_bus():
    yield
    work_product_events.init_work_product_events(None)


@pytest.mark.asyncio
async def test_publishes_a_classifiable_indexable_event():
    bus = RecordingBus()
    init_work_product_events(bus)

    published = await publish_work_product_created(
        work_product_id="wp-1",
        work_product_type="design_decision",
        name="Use aluminium",
        content="# Use aluminium\n\nStiffness per gram.",
        project_id="proj-1",
        source="twin.record_decision",
        metadata={"content_sha256": "abc"},
    )

    assert published is True
    event = bus.events[0]
    assert event.type is EventType.WORK_PRODUCT_CREATED
    # These three keys are exactly what KnowledgeConsumer reads: `content` to
    # index, `work_product_type` to classify, `work_product_id` for the dedup
    # source_path. A rename here silently stops indexing.
    assert event.data["content"].startswith("# Use aluminium")
    assert event.data["work_product_type"] == "design_decision"
    assert event.data["work_product_id"] == "wp-1"
    assert event.data["content_sha256"] == "abc"


@pytest.mark.asyncio
async def test_publishing_is_a_no_op_without_a_bus():
    init_work_product_events(None)

    assert work_product_events_enabled() is False
    assert (
        await publish_work_product_created(
            work_product_id="wp-1",
            work_product_type="design_decision",
            name="n",
            content="c",
        )
        is False
    )


@pytest.mark.asyncio
async def test_a_bus_failure_is_swallowed():
    init_work_product_events(RecordingBus(raises=True))

    assert (
        await publish_work_product_created(
            work_product_id="wp-1",
            work_product_type="design_decision",
            name="n",
            content="c",
        )
        is False
    )


@pytest.mark.asyncio
async def test_a_published_decision_reaches_the_knowledge_service():
    # The whole point of the event: the consumer turns it into a
    # DESIGN_DECISION knowledge chunk that knowledge.search can find.
    from digital_twin.knowledge.consumer import KnowledgeConsumer
    from digital_twin.knowledge.types import KnowledgeType
    from orchestrator.event_bus.subscribers import EventBus

    class FakeService:
        def __init__(self) -> None:
            self.ingested: list[dict[str, Any]] = []

        async def ingest(self, **kwargs: Any) -> Any:
            self.ingested.append(kwargs)

            class _Result:
                chunks_indexed = 2

            return _Result()

        async def delete_by_source(self, source_path: str) -> int:
            return 0

    service = FakeService()
    bus = EventBus()
    bus.subscribe(KnowledgeConsumer(service))  # type: ignore[arg-type]
    init_work_product_events(bus)

    await publish_work_product_created(
        work_product_id="00000000-0000-0000-0000-0000000000ab",
        work_product_type="design_decision",
        name="Use aluminium",
        content="# Use aluminium\n\nStiffness per gram.",
        project_id="proj-1",
    )

    assert len(service.ingested) == 1
    call = service.ingested[0]
    assert call["knowledge_type"] is KnowledgeType.DESIGN_DECISION
    assert call["source_path"] == "work_product://00000000-0000-0000-0000-0000000000ab"
