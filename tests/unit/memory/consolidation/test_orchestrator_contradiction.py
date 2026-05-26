"""BACKGROUND-pass contradiction-detection integration tests (MET-455)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.contradiction_detector import (
    ContradictionDetector,
)
from digital_twin.memory.consolidation.fetcher import InMemoryEventFetcher
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.llm import StubLLMClient
from digital_twin.memory.consolidation.modes import (
    ConsolidationMode,
    ConsolidationRunRequest,
)
from digital_twin.memory.consolidation.orchestrator import ConsolidationOrchestrator
from digital_twin.memory.consolidation.synthesizer import InsightSynthesizer
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.validator import InsightValidator
from digital_twin.memory.consolidation.writer import (
    InMemoryInsightStore,
    SemanticMemoryWriter,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


async def _seed_experiences(store: InMemoryExperienceStore, count: int) -> None:
    for i in range(count):
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id=f"r{i}",
                step_id="s",
                agent_code="elec",
                task_type="component_select",
                success=True,
                result_summary=f"selected ESP32 for run {i}",
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.7,
                confidence=ConfidenceTier.VERBATIM,
            )
        )


def _orchestrator(
    exp_store: InMemoryExperienceStore,
    insight_store: InMemoryInsightStore,
    synth_llm: StubLLMClient,
    detector: ContradictionDetector | None,
) -> ConsolidationOrchestrator:
    return ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(exp_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(synth_llm),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        contradiction_detector=detector,
    )


@pytest.mark.asyncio
async def test_background_records_contradiction_against_existing():
    exp_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    await _seed_experiences(exp_store, 2)

    # Pre-existing insight in the same theme that the new one contradicts.
    prior = Insight(
        id=uuid4(),
        theme=ConsolidationTheme.COMPONENT_SELECTION,
        kind=InsightKind.PRINCIPLE,
        narrative="ESP32 is unreliable for low-power designs",
        confidence=0.85,
        supporting_experience_ids=[uuid4()],
    )
    await insight_store.write(prior)

    synth_llm = StubLLMClient(
        responses=[
            {
                "narrative": "ESP32 is the reliable low-power WiFi choice",
                "confidence": 0.9,
                "kind": "principle",
            }
        ]
    )
    detector = ContradictionDetector(
        StubLLMClient(
            responses=[
                {
                    "contradicts": True,
                    "conflicting_ids": [str(prior.id)],
                    "explanation": "reliable vs unreliable for low-power",
                }
            ]
        )
    )
    orchestrator = _orchestrator(exp_store, insight_store, synth_llm, detector)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    assert report.accepted_count == 1
    assert len(report.contradictions) == 1
    assert str(prior.id) in report.contradictions[0]
    # The new insight is still written (newest lesson wins).
    assert len(await insight_store.list()) == 2


@pytest.mark.asyncio
async def test_no_detector_means_no_contradictions_recorded():
    exp_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    await _seed_experiences(exp_store, 2)

    synth_llm = StubLLMClient(
        responses=[
            {
                "narrative": "ESP32 is a fine low-power WiFi pick for most IoT designs",
                "confidence": 0.9,
            }
        ]
    )
    orchestrator = _orchestrator(exp_store, insight_store, synth_llm, detector=None)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    assert report.accepted_count == 1
    assert report.contradictions == []


@pytest.mark.asyncio
async def test_no_contradiction_when_corpus_empty():
    exp_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    await _seed_experiences(exp_store, 2)

    synth_llm = StubLLMClient(
        responses=[
            {"narrative": "ESP32 is a fine low-power pick here", "confidence": 0.9}
        ]
    )
    # Detector that would say "contradicts" — but the corpus is empty
    # (this is the first insight), so detect short-circuits to no-conflict.
    detector = ContradictionDetector(
        StubLLMClient(responses=[{"contradicts": True, "conflicting_ids": []}])
    )
    orchestrator = _orchestrator(exp_store, insight_store, synth_llm, detector)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    assert report.accepted_count == 1
    assert report.contradictions == []
