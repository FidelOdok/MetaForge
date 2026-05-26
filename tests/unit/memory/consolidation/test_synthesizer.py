"""Unit tests for ``digital_twin.memory.consolidation.synthesizer``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.grouper import ExperienceGroup
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.llm import LLMClient, StubLLMClient
from digital_twin.memory.consolidation.synthesizer import InsightSynthesizer
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory


def _exp(*, success: bool = True, task_type: str = "stress") -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech",
        task_type=task_type,
        success=success,
        result_summary="stress check pass" if success else "stress exceeded",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.7,
        confidence=ConfidenceTier.VERBATIM,
    )


def _group(size: int = 3, success: bool = True) -> ExperienceGroup:
    return ExperienceGroup(
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        experiences=tuple(_exp(success=success) for _ in range(size)),
    )


@pytest.mark.asyncio
async def test_synthesize_happy_path_returns_insight():
    stub = StubLLMClient(
        responses=[
            {
                "narrative": "Stress validation consistently passes when load < 80% allowable.",
                "confidence": 0.85,
                "kind": "principle",
            }
        ]
    )
    synth = InsightSynthesizer(stub)
    insight = await synth.synthesize(_group(size=3))
    assert isinstance(insight, Insight)
    assert insight.theme == ConsolidationTheme.MECHANICAL_VALIDATION
    assert insight.kind == InsightKind.PRINCIPLE
    assert insight.confidence == pytest.approx(0.85)
    assert len(insight.supporting_experience_ids) == 3


@pytest.mark.asyncio
async def test_synthesize_empty_group_returns_none():
    stub = StubLLMClient()
    synth = InsightSynthesizer(stub)
    empty = ExperienceGroup(theme=ConsolidationTheme.MISC, experiences=())
    assert await synth.synthesize(empty) is None


@pytest.mark.asyncio
async def test_synthesize_returns_none_when_narrative_missing():
    stub = StubLLMClient(responses=[{"confidence": 0.9}])
    synth = InsightSynthesizer(stub)
    assert await synth.synthesize(_group()) is None


@pytest.mark.asyncio
async def test_synthesize_returns_none_when_narrative_blank():
    stub = StubLLMClient(responses=[{"narrative": "   ", "confidence": 0.9}])
    synth = InsightSynthesizer(stub)
    assert await synth.synthesize(_group()) is None


@pytest.mark.asyncio
async def test_synthesize_clamps_confidence():
    stub = StubLLMClient(
        responses=[{"narrative": "valid lesson learned text here", "confidence": 1.7}]
    )
    synth = InsightSynthesizer(stub)
    insight = await synth.synthesize(_group())
    assert insight is not None
    assert insight.confidence == 1.0


@pytest.mark.asyncio
async def test_synthesize_handles_nan_confidence():
    stub = StubLLMClient(
        responses=[{"narrative": "valid lesson learned", "confidence": float("nan")}]
    )
    synth = InsightSynthesizer(stub)
    assert await synth.synthesize(_group()) is None


@pytest.mark.asyncio
async def test_synthesize_swallows_llm_exceptions():
    class _BoomClient(LLMClient):
        async def synthesize_insight(self, prompt: str) -> dict:
            raise RuntimeError("network down")

    synth = InsightSynthesizer(_BoomClient())
    assert await synth.synthesize(_group()) is None


@pytest.mark.asyncio
async def test_prompt_includes_theme_and_examples():
    stub = StubLLMClient(responses=[{"narrative": "x" * 40, "confidence": 0.8}])
    synth = InsightSynthesizer(stub)
    group = _group(size=2)
    prompt = synth.build_prompt(group)
    assert "Theme: mechanical_validation" in prompt
    assert "Total experiences: 2" in prompt
    assert "agent=mech" in prompt


@pytest.mark.asyncio
async def test_unknown_kind_defaults_to_observation():
    stub = StubLLMClient(
        responses=[
            {
                "narrative": "Some observation about the system",
                "confidence": 0.8,
                "kind": "totally_unknown_kind",
            }
        ]
    )
    synth = InsightSynthesizer(stub)
    insight = await synth.synthesize(_group())
    assert insight is not None
    assert insight.kind == InsightKind.OBSERVATION
