"""Unit tests for ``StalenessInvalidator.restore_by_experiences`` (MET-455 rollback)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.insight import (
    Insight,
    InsightKind,
    InsightStatus,
)
from digital_twin.memory.consolidation.staleness import StalenessInvalidator
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InMemoryInsightStore


def _insight(
    *,
    supporting: list,
    theme: ConsolidationTheme = ConsolidationTheme.COMPONENT_SELECTION,
    status: InsightStatus = InsightStatus.ACTIVE,
) -> Insight:
    return Insight(
        id=uuid4(),
        theme=theme,
        kind=InsightKind.PRINCIPLE,
        narrative="A long enough narrative to satisfy the model length gate",
        confidence=0.85,
        supporting_experience_ids=supporting,
        status=status,
    )


@pytest.mark.asyncio
async def test_empty_restore_set_is_noop():
    store = InMemoryInsightStore()
    await store.write(_insight(supporting=[uuid4()], status=InsightStatus.STALE_WARN))
    invalidator = StalenessInvalidator(store)

    result = await invalidator.restore_by_experiences(set())

    assert result.scanned_count == 0
    assert result.restored_count == 0


@pytest.mark.asyncio
async def test_restore_flips_stale_insight_back_to_active():
    store = InMemoryInsightStore()
    exp = uuid4()
    stale = _insight(supporting=[exp, uuid4()], status=InsightStatus.STALE_WARN)
    await store.write(stale)
    invalidator = StalenessInvalidator(store)

    result = await invalidator.restore_by_experiences({exp})

    assert result.restored_count == 1
    assert stale.id in result.restored_insight_ids
    reloaded = await store.get(stale.id)
    assert reloaded is not None
    assert reloaded.status is InsightStatus.ACTIVE


@pytest.mark.asyncio
async def test_restore_skips_active_insights():
    store = InMemoryInsightStore()
    exp = uuid4()
    active = _insight(supporting=[exp], status=InsightStatus.ACTIVE)
    await store.write(active)
    invalidator = StalenessInvalidator(store)

    result = await invalidator.restore_by_experiences({exp})

    assert result.scanned_count == 1
    assert result.restored_count == 0


@pytest.mark.asyncio
async def test_restore_leaves_unrelated_stale_insights_untouched():
    store = InMemoryInsightStore()
    reverted_exp = uuid4()
    other_exp = uuid4()
    target = _insight(supporting=[reverted_exp], status=InsightStatus.STALE_WARN)
    bystander = _insight(supporting=[other_exp], status=InsightStatus.STALE_WARN)
    await store.write(target)
    await store.write(bystander)
    invalidator = StalenessInvalidator(store)

    result = await invalidator.restore_by_experiences({reverted_exp})

    assert result.restored_count == 1
    assert target.id in result.restored_insight_ids
    bystander_reloaded = await store.get(bystander.id)
    assert bystander_reloaded is not None
    assert bystander_reloaded.status is InsightStatus.STALE_WARN


@pytest.mark.asyncio
async def test_invalidate_then_restore_round_trip():
    store = InMemoryInsightStore()
    exp = uuid4()
    insight = _insight(supporting=[exp], status=InsightStatus.ACTIVE)
    await store.write(insight)
    invalidator = StalenessInvalidator(store)

    inval = await invalidator.invalidate_by_experiences({exp})
    assert inval.invalidated_count == 1
    after_inval = await store.get(insight.id)
    assert after_inval is not None and after_inval.status is InsightStatus.STALE_WARN

    restore = await invalidator.restore_by_experiences({exp})
    assert restore.restored_count == 1
    after_restore = await store.get(insight.id)
    assert after_restore is not None and after_restore.status is InsightStatus.ACTIVE
