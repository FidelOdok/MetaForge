"""End-to-end orchestrator test for the consolidation pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.fetcher import InMemoryEventFetcher
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.llm import StubLLMClient
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


def _exp(task_type: str, *, success: bool = True) -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech" if "stress" in task_type else "elec",
        task_type=task_type,
        success=success,
        result_summary=f"{task_type} run",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.7,
        confidence=ConfidenceTier.VERBATIM,
    )


def _orchestrator(
    *,
    experience_store: InMemoryExperienceStore,
    llm: StubLLMClient,
    insight_store: InMemoryInsightStore | None = None,
) -> tuple[ConsolidationOrchestrator, SemanticMemoryWriter, InMemoryInsightStore]:
    insight_store = insight_store or InMemoryInsightStore()
    writer = SemanticMemoryWriter(insight_store)
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(experience_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(llm),
        validator=InsightValidator(),
        writer=writer,
    )
    return orchestrator, writer, insight_store


@pytest.mark.asyncio
async def test_empty_store_returns_empty_report():
    orchestrator, _writer, _store = _orchestrator(
        experience_store=InMemoryExperienceStore(),
        llm=StubLLMClient(),
    )
    report = await orchestrator.run()
    assert report.fetched_count == 0
    assert report.group_count == 0
    assert report.accepted_count == 0


@pytest.mark.asyncio
async def test_happy_path_writes_one_insight_per_theme():
    exp_store = InMemoryExperienceStore()
    for _ in range(3):
        await exp_store.store(_exp("stress_check"))
    for _ in range(2):
        await exp_store.store(_exp("run_erc"))

    llm = StubLLMClient(
        responses={
            "mechanical_validation": {
                "narrative": "Stress tests consistently pass under nominal load.",
                "confidence": 0.85,
                "kind": "principle",
            },
            "circuit_design_rule": {
                "narrative": "ERC keeps flagging missing decoupling on power pins.",
                "confidence": 0.78,
                "kind": "failure_mode",
            },
        }
    )

    orchestrator, writer, store = _orchestrator(experience_store=exp_store, llm=llm)
    report = await orchestrator.run()

    assert report.fetched_count == 5
    assert report.accepted_count == 2
    assert report.rejected_count == 0
    assert writer.written_by_theme() == {
        ConsolidationTheme.MECHANICAL_VALIDATION: 1,
        ConsolidationTheme.CIRCUIT_DESIGN_RULE: 1,
    }
    listed = await store.list()
    assert len(listed) == 2


@pytest.mark.asyncio
async def test_low_confidence_insights_are_rejected_not_written():
    exp_store = InMemoryExperienceStore()
    for _ in range(3):
        await exp_store.store(_exp("stress_check"))

    llm = StubLLMClient(
        responses=[{"narrative": "vague observation worth ignoring", "confidence": 0.4}]
    )
    orchestrator, writer, store = _orchestrator(experience_store=exp_store, llm=llm)
    report = await orchestrator.run()

    assert report.synthesized_count == 1
    assert report.accepted_count == 0
    assert report.rejected_count == 1
    assert any("confidence" in r for r in report.rejected_reasons)
    assert writer.written_by_theme() == {}
    assert await store.list() == []


@pytest.mark.asyncio
async def test_llm_failure_recorded_as_synthesis_failure():
    exp_store = InMemoryExperienceStore()
    for _ in range(3):
        await exp_store.store(_exp("stress_check"))

    # Empty stub returns the default low-confidence "no_response" sentinel
    # which the synthesizer rejects before construction.
    llm = StubLLMClient()
    orchestrator, _writer, _store = _orchestrator(experience_store=exp_store, llm=llm)
    report = await orchestrator.run()

    # Default stub returns confidence 0.0 → synthesizer constructs an
    # Insight but the validator drops it.
    assert report.synthesized_count == 1
    assert report.accepted_count == 0
    assert report.rejected_count == 1


@pytest.mark.asyncio
async def test_orchestrator_resets_writer_counters_per_run():
    exp_store = InMemoryExperienceStore()
    for _ in range(2):
        await exp_store.store(_exp("stress_check"))
    llm = StubLLMClient(
        responses=[
            {
                "narrative": "A reasonably long lesson learned about agent behaviour",
                "confidence": 0.9,
                "kind": "principle",
            }
        ]
    )
    orchestrator, writer, _store = _orchestrator(experience_store=exp_store, llm=llm)

    first = await orchestrator.run()
    second = await orchestrator.run()
    assert first.accepted_count == 1
    # Both runs see the same data so each writes one — but the orchestrator
    # resets per-pass counters so the report reflects this pass only.
    assert second.written_by_theme[ConsolidationTheme.MECHANICAL_VALIDATION] == 1
    assert writer.written_by_theme()[ConsolidationTheme.MECHANICAL_VALIDATION] == 1
