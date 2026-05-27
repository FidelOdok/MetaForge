"""Unit tests for ``digital_twin.memory.consolidation.staleness``."""

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
async def test_empty_invalidation_set_is_noop():
    store = InMemoryInsightStore()
    await store.write(_insight(supporting=[uuid4()]))
    invalidator = StalenessInvalidator(store)
    result = await invalidator.invalidate_by_experiences(set())
    assert result.scanned_count == 0
    assert result.invalidated_count == 0


@pytest.mark.asyncio
async def test_marks_insight_citing_invalidated_experience():
    store = InMemoryInsightStore()
    exp_a = uuid4()
    exp_b = uuid4()
    citing = _insight(supporting=[exp_a, uuid4()])
    not_citing = _insight(supporting=[exp_b])
    await store.write(citing)
    await store.write(not_citing)

    invalidator = StalenessInvalidator(store)
    result = await invalidator.invalidate_by_experiences({exp_a})

    assert result.invalidated_count == 1
    assert citing.id in result.invalidated_insight_ids

    refreshed_citing = await store.get(citing.id)
    refreshed_other = await store.get(not_citing.id)
    assert refreshed_citing is not None and refreshed_citing.status is InsightStatus.STALE_WARN
    assert refreshed_other is not None and refreshed_other.status is InsightStatus.ACTIVE


@pytest.mark.asyncio
async def test_already_stale_insight_is_skipped():
    store = InMemoryInsightStore()
    exp = uuid4()
    already = _insight(supporting=[exp], status=InsightStatus.STALE_WARN)
    await store.write(already)

    invalidator = StalenessInvalidator(store)
    result = await invalidator.invalidate_by_experiences({exp})
    # Scanned but not re-invalidated.
    assert result.scanned_count == 1
    assert result.invalidated_count == 0


@pytest.mark.asyncio
async def test_multiple_invalidated_experiences():
    store = InMemoryInsightStore()
    exp_a, exp_b, exp_c = uuid4(), uuid4(), uuid4()
    i1 = _insight(supporting=[exp_a])
    i2 = _insight(supporting=[exp_b])
    i3 = _insight(supporting=[exp_c])  # not invalidated
    for i in (i1, i2, i3):
        await store.write(i)

    invalidator = StalenessInvalidator(store)
    result = await invalidator.invalidate_by_experiences({exp_a, exp_b})
    assert result.invalidated_count == 2
    assert set(result.invalidated_insight_ids) == {i1.id, i2.id}


@pytest.mark.asyncio
async def test_theme_filter_narrows_scan():
    store = InMemoryInsightStore()
    exp = uuid4()
    mech = _insight(supporting=[exp], theme=ConsolidationTheme.MECHANICAL_VALIDATION)
    power = _insight(supporting=[exp], theme=ConsolidationTheme.POWER_ANALYSIS)
    await store.write(mech)
    await store.write(power)

    invalidator = StalenessInvalidator(store)
    result = await invalidator.invalidate_by_experiences(
        {exp}, theme=ConsolidationTheme.POWER_ANALYSIS
    )
    assert result.invalidated_count == 1
    assert power.id in result.invalidated_insight_ids
    # Mechanical one untouched.
    refreshed = await store.get(mech.id)
    assert refreshed is not None and refreshed.status is InsightStatus.ACTIVE


@pytest.mark.asyncio
async def test_no_matching_experiences_invalidates_nothing():
    store = InMemoryInsightStore()
    await store.write(_insight(supporting=[uuid4()]))
    invalidator = StalenessInvalidator(store)
    result = await invalidator.invalidate_by_experiences({uuid4()})
    assert result.scanned_count == 1
    assert result.invalidated_count == 0
