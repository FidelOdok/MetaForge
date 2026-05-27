"""Unit tests for ``digital_twin.memory.event_embedder`` (MET-458)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from digital_twin.knowledge.embedding_service import EmbeddingService
from digital_twin.memory.event_embedder import EventEmbedder
from digital_twin.memory.models import ConfidenceTier
from orchestrator.event_bus.events import Event, EventType


def _event(
    event_type: EventType,
    *,
    run_id: str = "run-1",
    step_id: str = "step-1",
    agent_code: str = "mechanical",
    extra: dict | None = None,
    timestamp: str | None = None,
) -> Event:
    data = {"run_id": run_id, "step_id": step_id, "agent_code": agent_code}
    if extra:
        data.update(extra)
    return Event(
        id=f"evt-{run_id}-{step_id}-{event_type.value}",
        type=event_type,
        timestamp=timestamp or datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC).isoformat(),
        source="scheduler",
        data=data,
    )


class _BoomEmbeddings(EmbeddingService):
    async def embed(self, text: str) -> list[float]:
        raise RuntimeError("embedding backend down")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding backend down")


@pytest.mark.asyncio
async def test_embed_event_is_deterministic(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    event = _event(EventType.AGENT_TASK_COMPLETED)

    v1 = await embedder.embed_event(event)
    v2 = await embedder.embed_event(event)

    assert v1 == v2
    assert len(v1) == fake_embeddings.DIM


@pytest.mark.asyncio
async def test_build_experience_completed(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    event = _event(EventType.AGENT_TASK_COMPLETED, extra={"result": {"summary": "ERC pass"}})

    exp = await embedder.build_experience(event, importance=0.42)

    assert exp.success is True
    assert exp.error is None
    assert exp.importance == 0.42
    assert exp.confidence is ConfidenceTier.VERBATIM
    assert "ERC pass" in exp.result_summary
    assert "outcome=completed" in exp.result_summary
    assert exp.metadata["event_id"] == event.id
    assert exp.metadata["event_type"] == str(EventType.AGENT_TASK_COMPLETED)
    assert exp.metadata["source"] == "scheduler"
    assert len(exp.embedding) == fake_embeddings.DIM


@pytest.mark.asyncio
async def test_build_experience_failed_captures_error(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    event = _event(EventType.AGENT_TASK_FAILED, extra={"error": "stress exceeded allowable"})

    exp = await embedder.build_experience(event, importance=0.9)

    assert exp.success is False
    assert exp.error == "stress exceeded allowable"
    assert "outcome=failed" in exp.result_summary


@pytest.mark.asyncio
async def test_build_experience_coerces_duration_and_project_id(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    pid = "00000000-0000-0000-0000-000000000009"
    event = _event(
        EventType.AGENT_TASK_COMPLETED,
        extra={"duration": "1.5", "project_id": pid},
    )

    exp = await embedder.build_experience(event, importance=0.5)

    assert exp.duration_seconds == 1.5
    assert exp.project_id == UUID(pid)


@pytest.mark.asyncio
async def test_build_experience_tolerates_bad_duration_and_project_id(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    event = _event(
        EventType.AGENT_TASK_COMPLETED,
        extra={"duration": "not-a-number", "project_id": "not-a-uuid"},
    )

    exp = await embedder.build_experience(event, importance=0.5)

    assert exp.duration_seconds is None
    assert exp.project_id is None


@pytest.mark.asyncio
async def test_build_experience_bad_timestamp_falls_back_to_now(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    event = _event(EventType.AGENT_TASK_COMPLETED, timestamp="not-a-timestamp")

    exp = await embedder.build_experience(event, importance=0.5)

    assert exp.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_build_experience_honors_explicit_confidence(fake_embeddings):
    embedder = EventEmbedder(fake_embeddings)
    event = _event(EventType.AGENT_TASK_COMPLETED)

    exp = await embedder.build_experience(
        event, importance=0.5, confidence=ConfidenceTier.LLM_INFERRED
    )

    assert exp.confidence is ConfidenceTier.LLM_INFERRED


@pytest.mark.asyncio
async def test_embedding_backend_error_propagates():
    embedder = EventEmbedder(_BoomEmbeddings())
    event = _event(EventType.AGENT_TASK_COMPLETED)

    with pytest.raises(RuntimeError, match="embedding backend down"):
        await embedder.build_experience(event, importance=0.5)
