"""Unit tests for ``digital_twin.memory.consolidation.decay``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.decay import (
    DEFAULT_HALF_LIFE_DAYS,
    ConfidenceDecay,
    is_stale,
)
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.themes import ConsolidationTheme


def _insight(*, confidence: float = 0.9, synthesized_at: datetime | None = None) -> Insight:
    return Insight(
        id=uuid4(),
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        kind=InsightKind.PRINCIPLE,
        narrative="A long enough narrative to satisfy the model constraints",
        confidence=confidence,
        supporting_experience_ids=[uuid4()],
        synthesized_at=synthesized_at or datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )


def test_fresh_insight_keeps_full_confidence():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    insight = _insight(confidence=0.9, synthesized_at=now)
    decay = ConfidenceDecay()
    assert decay.decayed_confidence(insight, now=now) == pytest.approx(0.9)


def test_one_half_life_halves_confidence():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(days=DEFAULT_HALF_LIFE_DAYS)
    insight = _insight(confidence=0.8, synthesized_at=old)
    decay = ConfidenceDecay()
    assert decay.decayed_confidence(insight, now=now) == pytest.approx(0.4, abs=1e-6)


def test_two_half_lives_quarters_confidence():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(days=2 * DEFAULT_HALF_LIFE_DAYS)
    insight = _insight(confidence=0.8, synthesized_at=old)
    decay = ConfidenceDecay()
    assert decay.decayed_confidence(insight, now=now) == pytest.approx(0.2, abs=1e-6)


def test_floor_is_respected():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(days=10 * DEFAULT_HALF_LIFE_DAYS)
    insight = _insight(confidence=0.9, synthesized_at=old)
    decay = ConfidenceDecay(floor=0.1)
    assert decay.decayed_confidence(insight, now=now) == pytest.approx(0.1)


def test_decay_never_inflates_confidence():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    # Future-dated insight (clock skew) — age clamps to 0, factor 1.0.
    future = now + timedelta(days=5)
    insight = _insight(confidence=0.7, synthesized_at=future)
    decay = ConfidenceDecay()
    assert decay.decayed_confidence(insight, now=now) == pytest.approx(0.7)


def test_with_decayed_confidence_returns_copy():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(days=DEFAULT_HALF_LIFE_DAYS)
    insight = _insight(confidence=0.8, synthesized_at=old)
    decay = ConfidenceDecay()
    copy = decay.with_decayed_confidence(insight, now=now)
    assert copy.confidence == pytest.approx(0.4, abs=1e-6)
    # Original is untouched.
    assert insight.confidence == 0.8
    assert copy.id == insight.id


def test_naive_synthesized_at_is_normalised():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    naive_old = datetime(2026, 5, 26, 12, 0, 0) - timedelta(days=DEFAULT_HALF_LIFE_DAYS)
    insight = _insight(confidence=0.8, synthesized_at=naive_old)
    decay = ConfidenceDecay()
    # Should not raise on naive vs aware subtraction.
    assert decay.decayed_confidence(insight, now=now) == pytest.approx(0.4, abs=1e-6)


def test_is_stale_true_when_decayed_below_threshold():
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    old = now - timedelta(days=DEFAULT_HALF_LIFE_DAYS)
    insight = _insight(confidence=0.8, synthesized_at=old)  # decays to 0.4
    assert is_stale(insight, threshold=0.7, now=now) is True
    assert is_stale(insight, threshold=0.3, now=now) is False


def test_invalid_half_life_rejected():
    with pytest.raises(ValueError, match="half_life_days"):
        ConfidenceDecay(half_life_days=0)


def test_invalid_floor_rejected():
    with pytest.raises(ValueError, match="floor"):
        ConfidenceDecay(floor=1.5)


def test_factor_monotonically_decreases():
    decay = ConfidenceDecay()
    f0 = decay.factor(0)
    f30 = decay.factor(30)
    f90 = decay.factor(90)
    assert f0 == 1.0
    assert f0 > f30 > f90
