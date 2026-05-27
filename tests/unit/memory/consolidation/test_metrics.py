"""Tests for consolidation metrics emission (MET-454/455)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.decay import ConfidenceDecay
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
from observability.metrics import MetricsCollector, MetricsRegistry


class _SpyCollector:
    """Captures record_consolidation_pass calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_consolidation_pass(self, mode, duration, **kwargs):
        self.calls.append({"mode": mode, "duration": duration, **kwargs})


async def _seed(store: InMemoryExperienceStore, count: int) -> None:
    for i in range(count):
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id=f"r{i}",
                step_id="s",
                agent_code="mech",
                task_type="stress_check",
                success=True,
                result_summary=f"stress run {i}",
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.7,
                confidence=ConfidenceTier.VERBATIM,
            )
        )


@pytest.mark.asyncio
async def test_background_pass_records_metric():
    exp_store = InMemoryExperienceStore()
    await _seed(exp_store, 3)
    spy = _SpyCollector()
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(exp_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(
            StubLLMClient(
                responses=[
                    {
                        "narrative": "Stress validation passes reliably under load",
                        "confidence": 0.9,
                        "kind": "principle",
                    }
                ]
            )
        ),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(InMemoryInsightStore()),
        collector=spy,
    )

    await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND))
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["mode"] == "background"
    assert call["accepted"] == 1
    assert call["duration"] >= 0.0


@pytest.mark.asyncio
async def test_janitor_pass_records_stale_marked_metric():
    insight_store = InMemoryInsightStore()
    from datetime import timedelta

    from digital_twin.memory.consolidation.decay import DEFAULT_HALF_LIFE_DAYS

    await insight_store.write(
        Insight(
            id=uuid4(),
            theme=ConsolidationTheme.MECHANICAL_VALIDATION,
            kind=InsightKind.PRINCIPLE,
            narrative="A long enough narrative to satisfy the validator gate",
            confidence=0.9,
            supporting_experience_ids=[uuid4()],
            synthesized_at=datetime.now(UTC) - timedelta(days=2 * DEFAULT_HALF_LIFE_DAYS),
        )
    )
    spy = _SpyCollector()
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(InMemoryExperienceStore()),
        grouper=EventGrouper(),
        synthesizer=InsightSynthesizer(StubLLMClient()),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        decay=ConfidenceDecay(),
        janitor_marks_stale=True,
        collector=spy,
    )

    await orchestrator.run_request(ConsolidationRunRequest(mode=ConsolidationMode.JANITOR))
    assert len(spy.calls) == 1
    assert spy.calls[0]["mode"] == "janitor"
    assert spy.calls[0]["stale_marked"] == 1


@pytest.mark.asyncio
async def test_no_collector_is_safe():
    exp_store = InMemoryExperienceStore()
    await _seed(exp_store, 2)
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(exp_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(
            StubLLMClient(responses=[{"narrative": "x" * 40, "confidence": 0.9}])
        ),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(InMemoryInsightStore()),
        collector=None,
    )
    # Should not raise.
    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    assert report.accepted_count == 1


def test_consolidation_metrics_registered():
    names = {m.name for m in MetricsRegistry.consolidation_metrics()}
    assert "metaforge_consolidation_pass_duration_seconds" in names
    assert "metaforge_consolidation_stale_marked_total" in names
    # And included in all_metrics().
    all_names = {m.name for m in MetricsRegistry.all_metrics()}
    assert names.issubset(all_names)


def test_real_collector_record_is_noop_without_meter():
    # A no-op MetricsCollector (no meter) must accept the call silently.
    collector = MetricsCollector()
    collector.record_consolidation_pass("background", 0.1, accepted=1, stale_marked=2)
