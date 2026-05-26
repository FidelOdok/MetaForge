"""Unit tests for ``digital_twin.memory.consolidation.dual_write``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.dual_write import DualWriteInsightStore
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InMemoryInsightStore, InsightStore


def _insight() -> Insight:
    return Insight(
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        kind=InsightKind.PRINCIPLE,
        narrative="A long enough narrative to satisfy the model constraints",
        confidence=0.85,
        supporting_experience_ids=[uuid4()],
    )


class _BoomStore(InsightStore):
    """Secondary store whose write always raises."""

    def __init__(self) -> None:
        self.write_calls = 0

    async def write(self, insight: Insight) -> Insight:
        self.write_calls += 1
        raise RuntimeError("neo4j unavailable")

    async def list(self, *, theme=None, limit=50):  # type: ignore[no-untyped-def]
        raise AssertionError("secondary list must never be called")

    async def get(self, insight_id):  # type: ignore[no-untyped-def]
        raise AssertionError("secondary get must never be called")


@pytest.mark.asyncio
async def test_write_fans_out_to_both_stores():
    primary = InMemoryInsightStore()
    secondary = InMemoryInsightStore()
    store = DualWriteInsightStore(primary, secondary)

    insight = _insight()
    await store.write(insight)

    assert await primary.get(insight.id) is not None
    assert await secondary.get(insight.id) is not None
    assert store.secondary_failures == 0


@pytest.mark.asyncio
async def test_secondary_failure_is_swallowed():
    primary = InMemoryInsightStore()
    secondary = _BoomStore()
    store = DualWriteInsightStore(primary, secondary)

    insight = _insight()
    # Must not raise even though the secondary blows up.
    returned = await store.write(insight)

    assert returned.id == insight.id
    assert await primary.get(insight.id) is not None  # primary still wrote
    assert secondary.write_calls == 1
    assert store.secondary_failures == 1


@pytest.mark.asyncio
async def test_primary_failure_propagates():
    secondary = InMemoryInsightStore()
    store = DualWriteInsightStore(_BoomStore(), secondary)

    with pytest.raises(RuntimeError, match="neo4j unavailable"):
        await store.write(_insight())


@pytest.mark.asyncio
async def test_reads_come_from_primary_only():
    primary = InMemoryInsightStore()
    secondary = _BoomStore()  # would AssertionError if read
    store = DualWriteInsightStore(primary, secondary)

    insight = _insight()
    await primary.write(insight)

    listed = await store.list()
    fetched = await store.get(insight.id)
    assert len(listed) == 1
    assert fetched is not None
    assert fetched.id == insight.id


@pytest.mark.asyncio
async def test_secondary_failures_accumulate():
    primary = InMemoryInsightStore()
    secondary = _BoomStore()
    store = DualWriteInsightStore(primary, secondary)

    for _ in range(3):
        await store.write(_insight())
    assert store.secondary_failures == 3


@pytest.mark.asyncio
async def test_list_forwards_theme_and_limit():
    primary = InMemoryInsightStore()
    secondary = InMemoryInsightStore()
    store = DualWriteInsightStore(primary, secondary)

    mech = _insight()
    await store.write(mech)
    power = Insight(
        theme=ConsolidationTheme.POWER_ANALYSIS,
        narrative="Power budget consistently under target across runs",
        confidence=0.8,
        supporting_experience_ids=[uuid4()],
    )
    await store.write(power)

    only_power = await store.list(theme=ConsolidationTheme.POWER_ANALYSIS)
    assert {i.theme for i in only_power} == {ConsolidationTheme.POWER_ANALYSIS}
