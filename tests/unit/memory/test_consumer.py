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
