"""Integration test: ExperienceConsumer on the event bus (MET-453).

Mirrors the gateway wiring — subscribe an ExperienceConsumer to an
EventBus and verify an AGENT_TASK_COMPLETED event flows through into the
experience store. This is the production path that makes the rest of the
memory pipeline (retrieval, consolidation) non-inert.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.consumer import ExperienceConsumer
from digital_twin.memory.store import InMemoryExperienceStore
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventBus


class _FakeEmbeddings(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [(digest[i] - 128) / 128.0 for i in range(8)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


def _completed_event(run_id: str = "run-1") -> Event:
    return Event(
        id=f"evt-{run_id}",
        type=EventType.AGENT_TASK_COMPLETED,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data={
            "run_id": run_id,
            "step_id": "s1",
            "agent_code": "mechanical",
            "result": {"summary": "stress validation passed"},
        },
    )


@pytest.mark.asyncio
async def test_completed_event_flows_into_experience_store():
    store = InMemoryExperienceStore()
    bus = EventBus()
    bus.subscribe(ExperienceConsumer(store, _FakeEmbeddings()))

    await bus.publish(_completed_event())

    hits = await store.search([1.0] * 8, limit=5)
    assert len(hits) == 1
    exp = hits[0].experience
    assert exp.agent_code == "mechanical"
    assert exp.success is True
    assert "stress validation passed" in exp.result_summary


@pytest.mark.asyncio
async def test_started_event_alone_does_not_persist():
    store = InMemoryExperienceStore()
    bus = EventBus()
    bus.subscribe(ExperienceConsumer(store, _FakeEmbeddings()))

    started = Event(
        id="evt-started",
        type=EventType.AGENT_TASK_STARTED,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data={"run_id": "r", "step_id": "s", "agent_code": "mech"},
    )
    await bus.publish(started)

    hits = await store.search([1.0] * 8, limit=5)
    assert hits == []


@pytest.mark.asyncio
async def test_failed_event_persists_with_error():
    store = InMemoryExperienceStore()
    bus = EventBus()
    bus.subscribe(ExperienceConsumer(store, _FakeEmbeddings()))

    failed = Event(
        id="evt-failed",
        type=EventType.AGENT_TASK_FAILED,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data={
            "run_id": "r",
            "step_id": "s",
            "agent_code": "mech",
            "error": "stress exceeded allowable",
        },
    )
    await bus.publish(failed)

    hits = await store.search([1.0] * 8, limit=5)
    assert len(hits) == 1
    assert hits[0].experience.success is False
    assert hits[0].experience.error == "stress exceeded allowable"
