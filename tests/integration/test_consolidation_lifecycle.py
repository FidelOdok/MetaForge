"""End-to-end consolidation lifecycle test (MET-454 + MET-455).

Exercises the whole surface composed together — there are unit tests
per stage, but this proves they interoperate:

    experiences → BACKGROUND pass (group → synth → validate → write)
                → insight stored ACTIVE
                → time passes (insight ages past the decay half-life)
                → JANITOR pass (decay → re-validate → mark STALE_WARN)
                → insight persisted as STALE_WARN

Plus a contradiction-detection check over the synthesized corpus.
Everything runs against in-memory backends with a deterministic
StubLLMClient, so no database / LLM / network is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation import (
    ConfidenceDecay,
    ConsolidationMode,
    ConsolidationOrchestrator,
    ConsolidationRunRequest,
    ContradictionDetector,
    EventGrouper,
    InMemoryEventFetcher,
    InMemoryInsightStore,
    InsightStatus,
    InsightSynthesizer,
    InsightValidator,
    SemanticMemoryWriter,
    StubLLMClient,
)
from digital_twin.memory.consolidation.decay import DEFAULT_HALF_LIFE_DAYS
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


async def _seed_experiences(store: InMemoryExperienceStore, *, count: int) -> None:
    for i in range(count):
        await store.store(
            ExperienceMemory(
                id=uuid4(),
                run_id=f"run-{i}",
                step_id="s",
                agent_code="mech",
                task_type="stress_check",
                success=True,
                result_summary=f"stress check {i} passed within margin",
                timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
                importance=0.7,
                confidence=ConfidenceTier.VERBATIM,
            )
        )


@pytest.mark.asyncio
async def test_full_lifecycle_active_then_stale():
    experience_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    await _seed_experiences(experience_store, count=3)

    llm = StubLLMClient(
        responses=[
            {
                "narrative": "Stress validation passes reliably under nominal load margins",
                "confidence": 0.9,
                "kind": "principle",
            }
        ]
    )

    # --- BACKGROUND pass: synthesize + write -------------------------
    background = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(experience_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(llm),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
    )
    bg_report = await background.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    assert bg_report.accepted_count == 1

    stored = await insight_store.list()
    assert len(stored) == 1
    insight = stored[0]
    assert insight.status is InsightStatus.ACTIVE

    # --- Age the insight past the decay half-life --------------------
    aged = insight.model_copy(
        update={
            "synthesized_at": datetime.now(UTC)
            - timedelta(days=2 * DEFAULT_HALF_LIFE_DAYS)
        }
    )
    await insight_store.write(aged)

    # --- JANITOR pass: decay → re-validate → mark stale --------------
    janitor = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(experience_store),
        grouper=EventGrouper(),
        synthesizer=InsightSynthesizer(StubLLMClient()),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
        decay=ConfidenceDecay(),
        janitor_marks_stale=True,
    )
    jr_report = await janitor.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    assert jr_report.revalidated_count == 1
    assert jr_report.newly_failed_count == 1
    assert jr_report.marked_stale_count == 1

    final = await insight_store.get(insight.id)
    assert final is not None
    assert final.status is InsightStatus.STALE_WARN


@pytest.mark.asyncio
async def test_contradiction_detection_over_synthesized_corpus():
    experience_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    await _seed_experiences(experience_store, count=2)

    # Synthesize one insight into the corpus.
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(experience_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(
            StubLLMClient(
                responses=[
                    {
                        "narrative": "Titanium brackets always pass stress validation",
                        "confidence": 0.9,
                        "kind": "principle",
                    }
                ]
            )
        ),
        validator=InsightValidator(),
        writer=SemanticMemoryWriter(insight_store),
        insight_store=insight_store,
    )
    await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    existing = await insight_store.list()
    assert len(existing) == 1

    # A new candidate insight that contradicts the stored one.
    candidate = existing[0].model_copy(
        update={
            "id": uuid4(),
            "narrative": "Titanium brackets frequently fail stress validation under load",
        }
    )
    detector = ContradictionDetector(
        StubLLMClient(
            responses=[
                {
                    "contradicts": True,
                    "conflicting_ids": [str(existing[0].id)],
                    "explanation": "One says titanium always passes, the other says it fails.",
                }
            ]
        )
    )
    result = await detector.detect(candidate, existing)
    assert result.contradicts is True
    assert existing[0].id in result.conflicting_insight_ids
