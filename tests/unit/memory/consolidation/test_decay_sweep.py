"""Tests for the daily-tick decay sweep (MET-472)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from digital_twin.memory.consolidation.decay import (
    DEFAULT_STALE_THRESHOLD,
    ConfidenceDecay,
    DecayOutcome,
    apply_decay_to_insight,
    decay_insights_in_store,
)
from digital_twin.memory.consolidation.insight import (
    Insight,
    InsightKind,
    InsightStatus,
)
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import (
    InMemoryInsightStore,
    InsightStore,
)


def _insight(
    *,
    confidence: float = 1.0,
    synthesized_at: datetime,
    status: InsightStatus = InsightStatus.ACTIVE,
    theme: ConsolidationTheme = ConsolidationTheme.COMPONENT_SELECTION,
    narrative: str = "Prefer X over Y for low-power",
) -> Insight:
    return Insight(
        id=uuid4(),
        theme=theme,
        kind=InsightKind.PATTERN,
        narrative=narrative,
        supporting_experience_ids=[],
        confidence=confidence,
        status=status,
        synthesized_at=synthesized_at,
    )


# ---------------------------------------------------------------------------
# apply_decay_to_insight — pure outcome
# ---------------------------------------------------------------------------


def test_fresh_insight_outcome_matches_input_confidence():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(synthesized_at=now)  # zero age
    out = apply_decay_to_insight(insight, now=now)
    assert isinstance(out, DecayOutcome)
    assert out.confidence_before == 1.0
    assert out.confidence_after == pytest.approx(1.0)
    assert out.status_changed is False
    assert out.insight.status == InsightStatus.ACTIVE


def test_one_half_life_halves_confidence():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    ninety_days_ago = now - timedelta(days=90)
    insight = _insight(confidence=1.0, synthesized_at=ninety_days_ago)
    out = apply_decay_to_insight(insight, now=now)
    assert out.confidence_after == pytest.approx(0.5)


def test_aged_insight_flips_to_stale_when_below_threshold():
    # 90 days at half-life=90 → confidence drops from 1.0 to 0.5 → stale
    # (default threshold is 0.70).
    now = datetime(2026, 6, 1, tzinfo=UTC)
    ninety_days_ago = now - timedelta(days=90)
    insight = _insight(confidence=1.0, synthesized_at=ninety_days_ago)
    out = apply_decay_to_insight(insight, now=now)
    assert out.status_changed is True
    assert out.insight.status == InsightStatus.STALE_WARN


def test_aged_insight_above_threshold_stays_active():
    # Need to find an age where 1.0 * 0.5^(d/90) > 0.70
    # 0.7 = 0.5^(d/90) → d/90 = log(0.7)/log(0.5) → d ≈ 46.3 days
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(confidence=1.0, synthesized_at=now - timedelta(days=30))
    out = apply_decay_to_insight(insight, now=now)
    assert out.status_changed is False
    assert out.insight.status == InsightStatus.ACTIVE
    assert out.confidence_after > DEFAULT_STALE_THRESHOLD


def test_pure_decay_never_unstales_an_already_stale_insight():
    # Even if confidence is artificially high, pure decay never flips
    # STALE_WARN back to ACTIVE — that escape hatch belongs to the
    # consolidation re-validation pass, not the decay tick.
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(
        confidence=1.0,
        synthesized_at=now,
        status=InsightStatus.STALE_WARN,
    )
    out = apply_decay_to_insight(insight, now=now)
    # Confidence preserved; status remains stale.
    assert out.confidence_after == pytest.approx(1.0)
    assert out.insight.status == InsightStatus.STALE_WARN


def test_apply_decay_to_insight_is_pure():
    """The input model must not be mutated by apply_decay_to_insight."""
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(synthesized_at=now - timedelta(days=90))
    confidence_before = insight.confidence
    apply_decay_to_insight(insight, now=now)
    assert insight.confidence == confidence_before


def test_apply_decay_to_insight_rejects_out_of_range_threshold():
    insight = _insight(synthesized_at=datetime.now(UTC))
    with pytest.raises(ValueError, match="stale_threshold"):
        apply_decay_to_insight(insight, stale_threshold=1.5)
    with pytest.raises(ValueError, match="stale_threshold"):
        apply_decay_to_insight(insight, stale_threshold=-0.1)


# ---------------------------------------------------------------------------
# decay_insights_in_store — async sweep
# ---------------------------------------------------------------------------


async def _populated_store(insights: list[Insight]) -> InsightStore:
    store = InMemoryInsightStore()
    for i in insights:
        await store.write(i)
    return store


@pytest.mark.asyncio
async def test_sweep_returns_zero_for_empty_store():
    store = InMemoryInsightStore()
    result = await decay_insights_in_store(store)
    assert result.scanned == 0
    assert result.updated == 0
    assert result.newly_stale == 0


@pytest.mark.asyncio
async def test_sweep_persists_decayed_confidence():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(confidence=1.0, synthesized_at=now - timedelta(days=45))
    store = await _populated_store([insight])
    result = await decay_insights_in_store(store, now=now)
    assert result.scanned == 1
    assert result.updated == 1
    fresh = await store.get(insight.id)
    assert fresh is not None
    assert fresh.confidence < 1.0


@pytest.mark.asyncio
async def test_sweep_counts_newly_stale_transitions():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    # Three insights: fresh (no change), aged-but-above-threshold,
    # aged-below-threshold (newly stale).
    fresh = _insight(synthesized_at=now)
    mid = _insight(synthesized_at=now - timedelta(days=20))
    stale = _insight(synthesized_at=now - timedelta(days=90))
    store = await _populated_store([fresh, mid, stale])

    result = await decay_insights_in_store(store, now=now)
    assert result.scanned == 3
    # ``fresh`` skipped (delta below epsilon, status unchanged).
    assert result.updated >= 1
    assert result.newly_stale == 1
    # Verify the persisted status flip
    stored = await store.get(stale.id)
    assert stored is not None
    assert stored.status == InsightStatus.STALE_WARN


@pytest.mark.asyncio
async def test_sweep_respects_persist_epsilon():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    # Zero-age insight has no confidence delta → must not be re-written.
    insight = _insight(synthesized_at=now)
    store = await _populated_store([insight])
    result = await decay_insights_in_store(store, now=now)
    assert result.scanned == 1
    assert result.updated == 0
    assert result.newly_stale == 0


@pytest.mark.asyncio
async def test_sweep_swallows_write_failures():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(confidence=1.0, synthesized_at=now - timedelta(days=90))
    store = await _populated_store([insight])

    failures: list[UUID] = []

    async def boom(_: Insight) -> Insight:
        failures.append(insight.id)
        raise RuntimeError("disk full")

    store.write = boom  # type: ignore[method-assign]
    # Sweep should not raise even when every write blows up.
    result = await decay_insights_in_store(store, now=now)
    assert result.scanned == 1
    assert result.updated == 0
    assert failures == [insight.id]


@pytest.mark.asyncio
async def test_sweep_honors_custom_decay_params():
    # 7-day half-life decays much faster — same 30-day-old insight that
    # stays active with default params should flip to stale here.
    now = datetime(2026, 6, 1, tzinfo=UTC)
    insight = _insight(confidence=1.0, synthesized_at=now - timedelta(days=30))
    store = await _populated_store([insight])
    result = await decay_insights_in_store(
        store,
        decay=ConfidenceDecay(half_life_days=7.0),
        now=now,
    )
    assert result.newly_stale == 1


@pytest.mark.asyncio
async def test_sweep_filters_by_theme_when_requested():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    a = _insight(
        synthesized_at=now - timedelta(days=90),
        theme=ConsolidationTheme.COMPONENT_SELECTION,
    )
    b = _insight(
        synthesized_at=now - timedelta(days=90),
        theme=ConsolidationTheme.POWER_ANALYSIS,
    )
    store = await _populated_store([a, b])
    result = await decay_insights_in_store(
        store,
        theme=ConsolidationTheme.COMPONENT_SELECTION,
        now=now,
    )
    # Only ``a`` is scanned.
    assert result.scanned == 1
    # ``b`` remains untouched.
    b_after = await store.get(b.id)
    assert b_after is not None
    assert b_after.confidence == 1.0
    assert b_after.status == InsightStatus.ACTIVE
