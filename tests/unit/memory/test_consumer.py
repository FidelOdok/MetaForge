"""Unit tests for ``digital_twin.memory.consumer``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from digital_twin.memory.consumer import ExperienceConsumer
from digital_twin.memory.store import InMemoryExperienceStore
from orchestrator.event_bus.events import Event, EventType


def _event(
    event_type: EventType,
    *,
    run_id: str = "run-1",
    step_id: str = "step-1",
    agent_code: str = "mechanical",
    extra: dict | None = None,
) -> Event:
    data = {"run_id": run_id, "step_id": step_id, "agent_code": agent_code}
    if extra:
        data.update(extra)
    return Event(
        id=f"evt-{run_id}-{step_id}-{event_type.value}",
        type=event_type,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data=data,
    )


@pytest.mark.asyncio
async def test_consumer_indexes_failed_event(fake_embeddings):
    store = InMemoryExperienceStore()
    consumer = ExperienceConsumer(store, fake_embeddings)

    event = _event(
        EventType.AGENT_TASK_FAILED,
        extra={"error": "stress exceeded allowable"},
    )
    await consumer.on_event(event)

    hits = await store.search([1.0] * 8, limit=5)
    assert len(hits) == 1
    exp = hits[0].experience
    assert exp.success is False
    assert exp.error == "stress exceeded allowable"
    assert "outcome=failed" in exp.result_summary


@pytest.mark.asyncio
async def test_consumer_indexes_completed_event(fake_embeddings):
    store = InMemoryExperienceStore()
    consumer = ExperienceConsumer(store, fake_embeddings)

    event = _event(
        EventType.AGENT_TASK_COMPLETED,
        extra={"result": {"summary": "ERC pass"}},
    )
    await consumer.on_event(event)

    hits = await store.search([1.0] * 8, limit=5)
    assert len(hits) == 1
    exp = hits[0].experience
    assert exp.success is True
    assert exp.error is None
    assert "ERC pass" in exp.result_summary


@pytest.mark.asyncio
async def test_consumer_drops_low_importance_started_event(fake_embeddings):
    store = InMemoryExperienceStore()
    consumer = ExperienceConsumer(store, fake_embeddings)

    started = _event(EventType.AGENT_TASK_STARTED)
    await consumer.on_event(started)

    hits = await store.search([1.0] * 8, limit=5)
    assert hits == []


@pytest.mark.asyncio
async def test_consumer_replay_is_idempotent(fake_embeddings):
    store = InMemoryExperienceStore()
    consumer = ExperienceConsumer(store, fake_embeddings)

    await consumer.on_event(_event(EventType.AGENT_TASK_STARTED))
    await consumer.on_event(
        _event(EventType.AGENT_TASK_COMPLETED, extra={"result": {"status": "ok"}})
    )
    # Replay
    await consumer.on_event(_event(EventType.AGENT_TASK_STARTED))
    await consumer.on_event(
        _event(EventType.AGENT_TASK_COMPLETED, extra={"result": {"status": "ok"}})
    )

    hits = await store.search([1.0] * 8, limit=5)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_consumer_skips_events_without_run_id(fake_embeddings):
    store = InMemoryExperienceStore()
    consumer = ExperienceConsumer(store, fake_embeddings)

    event = Event(
        id="evt-no-run",
        type=EventType.AGENT_TASK_COMPLETED,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data={"agent_code": "stub"},
    )
    await consumer.on_event(event)

    hits = await store.search([1.0] * 8, limit=5)
    assert hits == []


@pytest.mark.asyncio
async def test_consumer_event_types_includes_all_three(fake_embeddings):
    consumer = ExperienceConsumer(InMemoryExperienceStore(), fake_embeddings)
    assert consumer.event_types == {
        EventType.AGENT_TASK_STARTED,
        EventType.AGENT_TASK_COMPLETED,
        EventType.AGENT_TASK_FAILED,
    }


# ---------------------------------------------------------------------------
# Batch indexing (MET-459)
# ---------------------------------------------------------------------------


class _BatchCountingEmbeddings:
    """EmbeddingService-shaped fake that counts embed vs embed_batch calls."""

    DIM = 8

    def __init__(self) -> None:
        self.embed_calls = 0
        self.embed_batch_calls = 0

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return [1.0] * self.DIM

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.embed_batch_calls += 1
        return [[1.0] * self.DIM for _ in texts]


@pytest.mark.asyncio
async def test_index_batch_indexes_all_in_one_embed_call():
    store = InMemoryExperienceStore()
    embeddings = _BatchCountingEmbeddings()
    consumer = ExperienceConsumer(store, embeddings)

    events = [
        _event(EventType.AGENT_TASK_COMPLETED, run_id=f"r{i}", extra={"result": {"status": "ok"}})
        for i in range(5)
    ]
    indexed = await consumer.index_batch(events)

    assert indexed == 5
    assert embeddings.embed_batch_calls == 1
    assert embeddings.embed_calls == 0
    hits = await store.search([1.0] * 8, limit=10)
    assert len(hits) == 5


@pytest.mark.asyncio
async def test_index_batch_filters_low_importance_and_missing_run_id(fake_embeddings):
    store = InMemoryExperienceStore()
    consumer = ExperienceConsumer(store, fake_embeddings)

    good = _event(EventType.AGENT_TASK_COMPLETED, run_id="keep", extra={"result": {"status": "ok"}})
    started = _event(EventType.AGENT_TASK_STARTED, run_id="low")  # below importance floor
    no_run = Event(
        id="evt-no-run",
        type=EventType.AGENT_TASK_COMPLETED,
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data={"agent_code": "stub"},
    )

    indexed = await consumer.index_batch([good, started, no_run])

    assert indexed == 1
    hits = await store.search([1.0] * 8, limit=10)
    assert len(hits) == 1
    assert hits[0].experience.run_id == "keep"


@pytest.mark.asyncio
async def test_index_batch_empty_returns_zero(fake_embeddings):
    consumer = ExperienceConsumer(InMemoryExperienceStore(), fake_embeddings)
    assert await consumer.index_batch([]) == 0


@pytest.mark.asyncio
async def test_index_batch_embed_failure_does_not_crash(fake_embeddings):
    class _BoomStore(InMemoryExperienceStore):
        async def store(self, experience):
            raise RuntimeError("store down")

    store = _BoomStore()
    consumer = ExperienceConsumer(store, fake_embeddings)
    event = _event(EventType.AGENT_TASK_COMPLETED, extra={"result": {"status": "ok"}})

    # A store failure for every item is logged and swallowed; count is 0.
    indexed = await consumer.index_batch([event])
    assert indexed == 0
