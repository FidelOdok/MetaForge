"""Unit tests for ``digital_twin.memory.consolidation.writer``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import (
    InMemoryInsightStore,
    SemanticMemoryWriter,
)


def _insight(
    *,
    theme: ConsolidationTheme = ConsolidationTheme.MECHANICAL_VALIDATION,
    confidence: float = 0.9,
    synthesized_at: datetime | None = None,
    insight_id: UUID | None = None,
) -> Insight:
    return Insight(
        id=insight_id or uuid4(),
        theme=theme,
        kind=InsightKind.OBSERVATION,
        narrative="A long enough narrative for testing the writer flow",
        confidence=confidence,
        supporting_experience_ids=[uuid4()],
        synthesized_at=synthesized_at or datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_store_persists_and_retrieves():
    store = InMemoryInsightStore()
    insight = _insight()
    await store.write(insight)
    fetched = await store.get(insight.id)
    assert fetched is not None
    assert fetched.id == insight.id


@pytest.mark.asyncio
async def test_store_lists_newest_first():
    store = InMemoryInsightStore()
    base = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    await store.write(_insight(synthesized_at=base))
    await store.write(_insight(synthesized_at=base + timedelta(minutes=10)))
    await store.write(_insight(synthesized_at=base - timedelta(minutes=10)))
    listed = await store.list()
    timestamps = [i.synthesized_at for i in listed]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_store_filters_by_theme():
    store = InMemoryInsightStore()
    await store.write(_insight(theme=ConsolidationTheme.MECHANICAL_VALIDATION))
    await store.write(_insight(theme=ConsolidationTheme.POWER_ANALYSIS))
    only_mech = await store.list(theme=ConsolidationTheme.MECHANICAL_VALIDATION)
    assert all(i.theme == ConsolidationTheme.MECHANICAL_VALIDATION for i in only_mech)
    assert len(only_mech) == 1


@pytest.mark.asyncio
async def test_writer_counts_by_theme():
    writer = SemanticMemoryWriter(InMemoryInsightStore())
    await writer.write(_insight(theme=ConsolidationTheme.MECHANICAL_VALIDATION))
    await writer.write(_insight(theme=ConsolidationTheme.MECHANICAL_VALIDATION))
    await writer.write(_insight(theme=ConsolidationTheme.POWER_ANALYSIS))
    counts = writer.written_by_theme()
    assert counts[ConsolidationTheme.MECHANICAL_VALIDATION] == 2
    assert counts[ConsolidationTheme.POWER_ANALYSIS] == 1


@pytest.mark.asyncio
async def test_writer_reset_counters_zeros_state():
    writer = SemanticMemoryWriter(InMemoryInsightStore())
    await writer.write(_insight())
    writer.reset_counters()
    assert writer.written_by_theme() == {}


@pytest.mark.asyncio
async def test_get_returns_none_when_missing():
    store = InMemoryInsightStore()
    assert await store.get(uuid4()) is None
