"""Unit tests for ``digital_twin.memory.consolidation.validator``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.validator import (
    DEFAULT_MIN_CONFIDENCE,
    InsightValidator,
    ValidationResult,
)


def _insight(
    *,
    confidence: float = 0.9,
    narrative: str = "A reasonably long lesson learned about agent behaviour",
    supporting: list | None = None,
    kind: InsightKind = InsightKind.OBSERVATION,
) -> Insight:
    return Insight(
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        kind=kind,
        narrative=narrative,
        confidence=confidence,
        supporting_experience_ids=supporting if supporting is not None else [uuid4()],
    )


def test_accept_valid_insight():
    result = InsightValidator().validate(_insight())
    assert result.accepted
    assert result.reason == ""


def test_reject_below_default_confidence():
    result = InsightValidator().validate(_insight(confidence=DEFAULT_MIN_CONFIDENCE - 0.01))
    assert not result.accepted
    assert "confidence" in result.reason


def test_accept_at_exactly_default_confidence():
    result = InsightValidator().validate(_insight(confidence=DEFAULT_MIN_CONFIDENCE))
    assert result.accepted


def test_reject_short_narrative():
    result = InsightValidator().validate(_insight(narrative="too short"))
    assert not result.accepted
    assert "too short" in result.reason


def test_reject_hallucination_phrase():
    result = InsightValidator().validate(
        _insight(narrative="I don't know what the pattern here is, sorry about that")
    )
    assert not result.accepted
    assert "hallucination" in result.reason


def test_reject_missing_supporting_ids():
    result = InsightValidator().validate(_insight(supporting=[]))
    assert not result.accepted
    assert "supporting_experience_ids" in result.reason


def test_custom_min_confidence_propagates():
    validator = InsightValidator(min_confidence=0.95)
    assert not validator.validate(_insight(confidence=0.9)).accepted
    assert validator.validate(_insight(confidence=0.97)).accepted


def test_invalid_min_confidence_rejected():
    with pytest.raises(ValueError, match="min_confidence"):
        InsightValidator(min_confidence=1.5)


def test_invalid_min_length_rejected():
    with pytest.raises(ValueError, match="min_narrative_length"):
        InsightValidator(min_narrative_length=0)


def test_validation_result_helpers():
    assert ValidationResult.accept().accepted
    rejected = ValidationResult.reject("nope")
    assert not rejected.accepted
    assert rejected.reason == "nope"
