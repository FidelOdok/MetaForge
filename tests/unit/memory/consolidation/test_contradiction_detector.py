"""Unit tests for ``digital_twin.memory.consolidation.contradiction_detector``."""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.contradiction_detector import (
    ContradictionDetector,
    ContradictionResult,
)
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.llm import LLMClient, StubLLMClient
from digital_twin.memory.consolidation.themes import ConsolidationTheme


def _insight(
    *,
    narrative: str,
    theme: ConsolidationTheme = ConsolidationTheme.COMPONENT_SELECTION,
) -> Insight:
    return Insight(
        id=uuid4(),
        theme=theme,
        kind=InsightKind.PRINCIPLE,
        narrative=narrative,
        confidence=0.85,
        supporting_experience_ids=[uuid4()],
    )


@pytest.mark.asyncio
async def test_no_existing_insights_returns_no_contradiction():
    detector = ContradictionDetector(StubLLMClient())
    candidate = _insight(narrative="ESP32 is the low-power WiFi pick")
    result = await detector.detect(candidate, [])
    assert result.contradicts is False
    # No LLM call should have happened.
    assert StubLLMClient().calls == []


@pytest.mark.asyncio
async def test_detects_contradiction_and_resolves_ids():
    existing = _insight(narrative="ESP32 burns too much current in deep sleep")
    candidate = _insight(narrative="ESP32 is the best low-power WiFi choice")

    stub = StubLLMClient(
        responses=[
            {
                "contradicts": True,
                "conflicting_ids": [str(existing.id)],
                "explanation": "One says ESP32 is low-power, the other says it isn't.",
            }
        ]
    )
    detector = ContradictionDetector(stub)
    result = await detector.detect(candidate, [existing])

    assert result.contradicts is True
    assert result.conflicting_insight_ids == (existing.id,)
    assert "ESP32" in result.explanation


@pytest.mark.asyncio
async def test_no_contradiction_verdict():
    existing = _insight(narrative="STM32H7 is a strong high-performance MCU")
    candidate = _insight(narrative="ESP32 is a good low-cost WiFi MCU")
    stub = StubLLMClient(responses=[{"contradicts": False}])
    detector = ContradictionDetector(stub)
    result = await detector.detect(candidate, [existing])
    assert result.contradicts is False
    assert result.conflicting_insight_ids == ()


@pytest.mark.asyncio
async def test_fabricated_conflicting_ids_are_dropped():
    existing = _insight(narrative="ESP32 deep-sleep current is high")
    candidate = _insight(narrative="ESP32 is the low-power pick")
    # LLM returns an id that isn't in the comparison set + a malformed one.
    stub = StubLLMClient(
        responses=[
            {
                "contradicts": True,
                "conflicting_ids": [str(uuid4()), "not-a-uuid"],
                "explanation": "conflict",
            }
        ]
    )
    detector = ContradictionDetector(stub)
    result = await detector.detect(candidate, [existing])
    assert result.contradicts is True
    # Neither id is valid → empty tuple, but the verdict still stands.
    assert result.conflicting_insight_ids == ()


@pytest.mark.asyncio
async def test_only_same_theme_insights_are_compared():
    existing_other_theme = _insight(
        narrative="Stress tests pass under nominal load",
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
    )
    candidate = _insight(
        narrative="ESP32 low-power pick",
        theme=ConsolidationTheme.COMPONENT_SELECTION,
    )
    stub = StubLLMClient(responses=[{"contradicts": True, "conflicting_ids": []}])
    detector = ContradictionDetector(stub)
    # Different theme → no comparable insights → trivially no contradiction,
    # and the LLM is never consulted.
    result = await detector.detect(candidate, [existing_other_theme])
    assert result.contradicts is False
    assert stub.calls == []


@pytest.mark.asyncio
async def test_llm_error_fails_open():
    class _BoomClient(LLMClient):
        async def synthesize_insight(self, prompt: str) -> dict:
            raise RuntimeError("llm down")

    existing = _insight(narrative="ESP32 deep-sleep current is high")
    candidate = _insight(narrative="ESP32 is the low-power pick")
    detector = ContradictionDetector(_BoomClient())
    result = await detector.detect(candidate, [existing])
    assert result.contradicts is False


@pytest.mark.asyncio
async def test_prompt_includes_candidate_and_existing():
    existing = _insight(narrative="ESP32 deep-sleep current is high")
    candidate = _insight(narrative="ESP32 is the low-power pick")
    detector = ContradictionDetector(StubLLMClient())
    prompt = detector.build_prompt(candidate, [existing])
    assert "CANDIDATE" in prompt
    assert "ESP32 is the low-power pick" in prompt
    assert str(existing.id) in prompt


@pytest.mark.asyncio
async def test_excludes_self_from_comparison():
    candidate = _insight(narrative="ESP32 is the low-power pick")
    stub = StubLLMClient(responses=[{"contradicts": True, "conflicting_ids": []}])
    detector = ContradictionDetector(stub)
    # Passing the candidate itself in `existing` must not make it compare
    # against itself — it's filtered by id, leaving nothing to compare.
    result = await detector.detect(candidate, [candidate])
    assert result.contradicts is False
    assert stub.calls == []


def test_contradiction_result_defaults():
    result = ContradictionResult(contradicts=False)
    assert result.conflicting_insight_ids == ()
    assert result.explanation == ""
