"""Tests for JANITOR persisting status=STALE_WARN (MET-455)."""

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
from digital_twin.memory.consolidation.insight import (
    Insight,
    InsightKind,
    InsightStatus,
)
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
    janitor_marks_stale: bool,
) -> ConsolidationOrchestrator:
    return ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(InMemoryExperienceStore()),
        grouper=EventGrouper(),
        synthesizer=InsightSynthesizer(StubLLMClient()),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        decay=ConfidenceDecay(),
        janitor_marks_stale=janitor_marks_stale,
    )


async def _seed_aged(store: InMemoryInsightStore) -> Insight:
    insight = Insight(
        id=uuid4(),
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        kind=InsightKind.PRINCIPLE,
        narrative="A long enough narrative to satisfy the validator length gate",
        confidence=0.9,
        supporting_experience_ids=[uuid4()],
        synthesized_at=datetime.now(UTC) - timedelta(days=2 * DEFAULT_HALF_LIFE_DAYS),
    )
    await store.write(insight)
    return insight


@pytest.mark.asyncio
async def test_default_insight_status_is_active():
    insight = Insight(
        theme=ConsolidationTheme.MISC,
        narrative="x" * 40,
        confidence=0.8,
        supporting_experience_ids=[uuid4()],
    )
    assert insight.status is InsightStatus.ACTIVE


@pytest.mark.asyncio
async def test_janitor_marks_stale_persists_status():
    store = InMemoryInsightStore()
    insight = await _seed_aged(store)
    orchestrator = _orchestrator(store, janitor_marks_stale=True)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    assert report.newly_failed_count == 1
    assert report.marked_stale_count == 1

    stored = await store.get(insight.id)
    assert stored is not None
    assert stored.status is InsightStatus.STALE_WARN


@pytest.mark.asyncio
async def test_janitor_report_only_does_not_mutate_status():
    store = InMemoryInsightStore()
    insight = await _seed_aged(store)
    orchestrator = _orchestrator(store, janitor_marks_stale=False)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    assert report.newly_failed_count == 1
    assert report.marked_stale_count == 0

    stored = await store.get(insight.id)
    assert stored is not None
    assert stored.status is InsightStatus.ACTIVE


@pytest.mark.asyncio
async def test_already_stale_insight_not_re_marked():
    store = InMemoryInsightStore()
    insight = await _seed_aged(store)
    # Pre-mark it stale.
    await store.write(insight.model_copy(update={"status": InsightStatus.STALE_WARN}))
    orchestrator = _orchestrator(store, janitor_marks_stale=True)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    # Still counted as failed, but not re-marked (already STALE_WARN).
    assert report.newly_failed_count == 1
    assert report.marked_stale_count == 0


@pytest.mark.asyncio
async def test_fresh_insight_stays_active():
    store = InMemoryInsightStore()
    fresh = Insight(
        id=uuid4(),
        theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        kind=InsightKind.PRINCIPLE,
        narrative="A long enough narrative to satisfy the validator length gate",
        confidence=0.9,
        supporting_experience_ids=[uuid4()],
        synthesized_at=datetime.now(UTC),
    )
    await store.write(fresh)
    orchestrator = _orchestrator(store, janitor_marks_stale=True)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    assert report.marked_stale_count == 0
    stored = await store.get(fresh.id)
    assert stored is not None
    assert stored.status is InsightStatus.ACTIVE
