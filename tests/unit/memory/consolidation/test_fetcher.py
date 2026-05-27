"""Unit tests for ``digital_twin.memory.consolidation.fetcher``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.consolidation.fetcher import (
    DEFAULT_MIN_IMPORTANCE,
    InMemoryEventFetcher,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


def _exp(
    *,
    importance: float = 0.6,
    project_id: UUID | None = None,
    ts: datetime | None = None,
) -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech",
        task_type="stress",
        success=True,
        result_summary="",
        timestamp=ts or datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=importance,
        confidence=ConfidenceTier.VERBATIM,
        project_id=project_id,
    )


@pytest.mark.asyncio
async def test_fetch_returns_all_above_threshold():
    store = InMemoryExperienceStore()
    await store.store(_exp(importance=0.8))
    await store.store(_exp(importance=0.4))
    fetcher = InMemoryEventFetcher(store)

    out = await fetcher.fetch(min_importance=DEFAULT_MIN_IMPORTANCE)
    assert {e.importance for e in out} == {0.8, 0.4}


@pytest.mark.asyncio
async def test_fetch_filters_below_min_importance():
    store = InMemoryExperienceStore()
    await store.store(_exp(importance=0.5))
    await store.store(_exp(importance=0.1))
    fetcher = InMemoryEventFetcher(store)

    out = await fetcher.fetch(min_importance=0.4)
    assert {round(e.importance, 2) for e in out} == {0.5}


@pytest.mark.asyncio
async def test_fetch_filters_by_project_id():
    store = InMemoryExperienceStore()
    project_a = UUID("00000000-0000-0000-0000-000000000001")
    project_b = UUID("00000000-0000-0000-0000-000000000002")
    await store.store(_exp(project_id=project_a))
    await store.store(_exp(project_id=project_b))
    fetcher = InMemoryEventFetcher(store)

    only_a = await fetcher.fetch(project_id=project_a, min_importance=0.0)
    assert all(e.project_id == project_a for e in only_a)
    assert len(only_a) == 1


@pytest.mark.asyncio
async def test_fetch_respects_since_until_window():
    store = InMemoryExperienceStore()
    base = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    await store.store(_exp(ts=base - timedelta(hours=2)))
    await store.store(_exp(ts=base))
    await store.store(_exp(ts=base + timedelta(hours=2)))
    fetcher = InMemoryEventFetcher(store)

    out = await fetcher.fetch(
        since=base - timedelta(hours=1),
        until=base + timedelta(hours=1),
        min_importance=0.0,
    )
    assert len(out) == 1


@pytest.mark.asyncio
async def test_fetch_caps_at_limit_after_sorting():
    store = InMemoryExperienceStore()
    base = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        await store.store(_exp(ts=base + timedelta(minutes=i)))
    fetcher = InMemoryEventFetcher(store)

    out = await fetcher.fetch(limit=2, min_importance=0.0)
    assert len(out) == 2
    # Newest first
    assert out[0].timestamp > out[1].timestamp


@pytest.mark.asyncio
async def test_fetch_returns_newest_first():
    store = InMemoryExperienceStore()
    base = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    for offset in (3, 1, 4, 0, 2):
        await store.store(_exp(ts=base + timedelta(minutes=offset)))
    fetcher = InMemoryEventFetcher(store)

    out = await fetcher.fetch(min_importance=0.0)
    timestamps = [e.timestamp for e in out]
    assert timestamps == sorted(timestamps, reverse=True)
