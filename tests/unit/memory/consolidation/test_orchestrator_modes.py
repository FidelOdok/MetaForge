"""Mode-aware orchestrator tests (MET-454 configurable modes)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

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


def _exp(
    *,
    task_type: str = "stress_check",
    importance: float = 0.7,
    project_id: UUID | None = None,
) -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech",
        task_type=task_type,
        success=True,
        result_summary=f"{task_type} run",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=importance,
        confidence=ConfidenceTier.VERBATIM,
        project_id=project_id,
    )


def _build_orchestrator(
    exp_store: InMemoryExperienceStore,
    llm: StubLLMClient,
    *,
    insight_store: InMemoryInsightStore | None = None,
    validator: InsightValidator | None = None,
) -> tuple[ConsolidationOrchestrator, InMemoryInsightStore]:
    insight_store = insight_store or InMemoryInsightStore()
    writer = SemanticMemoryWriter(insight_store)
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(exp_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(llm),
        validator=validator or InsightValidator(),
        writer=writer,
        insight_store=insight_store,
    )
    return orchestrator, insight_store


@pytest.mark.asyncio
async def test_on_demand_processes_low_importance_events():
    exp_store = InMemoryExperienceStore()
    # 0.15 is well below DEFAULT_MIN_IMPORTANCE (0.30) for the fetcher
    for _ in range(2):
        await exp_store.store(_exp(importance=0.15))

    llm = StubLLMClient(
        responses=[
            {"narrative": "x" * 40, "confidence": 0.85, "kind": "principle"}
        ]
    )
    orchestrator, _store = _build_orchestrator(exp_store, llm)

    background = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.BACKGROUND)
    )
    on_demand = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.ON_DEMAND)
    )
    assert background.fetched_count == 0  # below floor
    assert on_demand.fetched_count == 2  # floor relaxed to 0
    assert on_demand.mode == ConsolidationMode.ON_DEMAND


@pytest.mark.asyncio
async def test_proactive_narrows_to_one_theme():
    exp_store = InMemoryExperienceStore()
    project_id = UUID("11111111-2222-3333-4444-555555555555")
    for _ in range(2):
        await exp_store.store(_exp(task_type="stress_check", project_id=project_id))
    for _ in range(2):
        await exp_store.store(_exp(task_type="run_erc", project_id=project_id))

    llm = StubLLMClient(
        responses=[
            {"narrative": "x" * 40, "confidence": 0.85, "kind": "principle"}
        ]
    )
    orchestrator, _store = _build_orchestrator(exp_store, llm)

    report = await orchestrator.run_request(
        ConsolidationRunRequest(
            mode=ConsolidationMode.PROACTIVE,
            project_id=project_id,
            theme=ConsolidationTheme.MECHANICAL_VALIDATION,
        )
    )
    assert report.group_count == 1
    assert report.accepted_count == 1
    assert report.insights[0].theme == ConsolidationTheme.MECHANICAL_VALIDATION


@pytest.mark.asyncio
async def test_janitor_revalidates_existing_insights_without_synthesis():
    # Stronger validator catches insights that previously passed.
    strict_validator = InsightValidator(min_confidence=0.95)
    exp_store = InMemoryExperienceStore()
    llm = StubLLMClient()  # never called in janitor mode

    orchestrator, insight_store = _build_orchestrator(
        exp_store, llm, validator=strict_validator
    )

    # Seed the store with insights that pass the *default* validator
    # but fail the strict one.
    for i in range(3):
        await insight_store.write(
            Insight(
                theme=ConsolidationTheme.POWER_ANALYSIS,
                kind=InsightKind.OBSERVATION,
                narrative=f"observation {i} long enough to pass the length gate",
                confidence=0.8,
                supporting_experience_ids=[uuid4()],
            )
        )

    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    assert report.mode == ConsolidationMode.JANITOR
    assert report.revalidated_count == 3
    assert report.newly_failed_count == 3
    assert report.synthesized_count == 0
    assert report.accepted_count == 0
    # Make sure the synthesizer was never called.
    assert llm.calls == []


@pytest.mark.asyncio
async def test_janitor_without_insight_store_returns_empty_report():
    exp_store = InMemoryExperienceStore()
    llm = StubLLMClient()
    # Build the orchestrator with insight_store=None
    writer = SemanticMemoryWriter(InMemoryInsightStore())
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(exp_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(llm),
        validator=InsightValidator(),
        writer=writer,
        insight_store=None,
    )
    report = await orchestrator.run_request(
        ConsolidationRunRequest(mode=ConsolidationMode.JANITOR)
    )
    assert report.mode == ConsolidationMode.JANITOR
    assert report.revalidated_count == 0
    assert report.newly_failed_count == 0


@pytest.mark.asyncio
async def test_legacy_run_kwargs_still_work():
    """The pre-modes ``run(**kwargs)`` signature must keep working."""
    exp_store = InMemoryExperienceStore()
    for _ in range(2):
        await exp_store.store(_exp())
    llm = StubLLMClient(
        responses=[
            {"narrative": "x" * 40, "confidence": 0.85, "kind": "principle"}
        ]
    )
    orchestrator, _store = _build_orchestrator(exp_store, llm)

    report = await orchestrator.run()
    assert report.mode == ConsolidationMode.BACKGROUND
    assert report.accepted_count == 1
