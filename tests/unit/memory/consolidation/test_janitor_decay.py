"""JANITOR-mode confidence-decay integration tests (MET-455)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.decay import (
    DEFAULT_HALF_LIFE_DAYS,
    ConfidenceDecay,
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
from digital_twin.memory.store import InMemoryExperienceStore


def _orchestrator(
    insight_store: InMemoryInsightStore,
    *,
    decay: ConfidenceDecay | None = None,
) -> ConsolidationOrchestrator:
    return ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(InMemoryExperienceStore()),
        grouper=EventGrouper(),
        synthesizer=InsightSynthesizer(StubLLMClient()),
        validator=InsightValidator(),  # default min_confidence 0.70
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        decay=decay,
    )


async def _seed_insight(
    store: InMemoryInsightStore,
    *,
    confidence: float,
    age_days: float,
) -> Insight:
    synthesized = datetime.now(UTC) - timedelta(days=age_days)
    insight = Insight(
        id=uuid4(),
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        kind=InsightKind.PRINCIPLE,
        narrative="A long enough narrative to satisfy the validator length gate",
        confidence=confidence,
        supporting_experience_ids=[uuid4()],
        synthesized_at=synthesized,
    )
    await store.write(insight)
    return insight


@pytest.mark.asyncio
async def test_janitor_without_decay_keeps_fresh_high_confidence_insight():
    store = InMemoryInsightStore()
    await _seed_insight(store, confidence=0.9, age_days=2 * DEFAULT_HALF_LIFE_DAYS)
    orchestrator = _orchestrator(store)  # no decay

    report = await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.JANITOR))
    # Raw confidence 0.9 passes the 0.70 floor — no decay means no failure.
    assert report.revalidated_count == 1
    assert report.newly_failed_count == 0


@pytest.mark.asyncio
async def test_janitor_with_decay_flags_aged_insight():
    store = InMemoryInsightStore()
    # 0.9 confidence, aged two half-lives → decays to ~0.225, below 0.70.
    await _seed_insight(store, confidence=0.9, age_days=2 * DEFAULT_HALF_LIFE_DAYS)
    orchestrator = _orchestrator(store, decay=ConfidenceDecay())

    report = await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.JANITOR))
    assert report.revalidated_count == 1
    assert report.newly_failed_count == 1
    assert any("confidence" in r for r in report.rejected_reasons)


@pytest.mark.asyncio
async def test_janitor_with_decay_keeps_fresh_insight():
    store = InMemoryInsightStore()
    # Fresh insight — decay barely touches it, stays above 0.70.
    await _seed_insight(store, confidence=0.9, age_days=0)
    orchestrator = _orchestrator(store, decay=ConfidenceDecay())

    report = await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.JANITOR))
    assert report.newly_failed_count == 0


@pytest.mark.asyncio
async def test_janitor_decay_does_not_mutate_stored_insight():
    store = InMemoryInsightStore()
    insight = await _seed_insight(store, confidence=0.9, age_days=2 * DEFAULT_HALF_LIFE_DAYS)
    orchestrator = _orchestrator(store, decay=ConfidenceDecay())

    await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.JANITOR))
    # The stored record's confidence is unchanged — decay is read-only.
    stored = await store.get(insight.id)
    assert stored is not None
    assert stored.confidence == 0.9
