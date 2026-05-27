"""Unit tests for the consolidation Temporal workflow + activity wrapper."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.fetcher import InMemoryEventFetcher
from digital_twin.memory.consolidation.grouper import EventGrouper
from digital_twin.memory.consolidation.llm import StubLLMClient
from digital_twin.memory.consolidation.modes import ConsolidationMode
from digital_twin.memory.consolidation.orchestrator import ConsolidationOrchestrator
from digital_twin.memory.consolidation.synthesizer import InsightSynthesizer
from digital_twin.memory.consolidation.validator import InsightValidator
from digital_twin.memory.consolidation.workflow import (
    ConsolidationActivities,
    ConsolidationActivityInput,
    ConsolidationActivityOutput,
    ConsolidationWorkflow,
    ConsolidationWorkflowInput,
    register_consolidation_activities,
    run_consolidation_pass_activity,
)
from digital_twin.memory.consolidation.writer import (
    InMemoryInsightStore,
    SemanticMemoryWriter,
)
from digital_twin.memory.models import ConfidenceTier, ExperienceMemory
from digital_twin.memory.store import InMemoryExperienceStore


def _exp() -> ExperienceMemory:
    return ExperienceMemory(
        id=uuid4(),
        run_id="r",
        step_id="s",
        agent_code="mech",
        task_type="stress_check",
        success=True,
        result_summary="stress run",
        timestamp=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        importance=0.7,
        confidence=ConfidenceTier.VERBATIM,
    )


def _build_orchestrator() -> tuple[ConsolidationOrchestrator, InMemoryExperienceStore]:
    exp_store = InMemoryExperienceStore()
    insight_store = InMemoryInsightStore()
    writer = SemanticMemoryWriter(insight_store)
    llm = StubLLMClient(
        responses=[
            {
                "narrative": "A reasonably long lesson learned from runs",
                "confidence": 0.85,
                "kind": "principle",
            }
        ]
    )
    orchestrator = ConsolidationOrchestrator(
        fetcher=InMemoryEventFetcher(exp_store),
        grouper=EventGrouper(min_group_size=2),
        synthesizer=InsightSynthesizer(llm),
        validator=InsightValidator(),
        writer=writer,
        insight_store=insight_store,
    )
    return orchestrator, exp_store


@pytest.mark.asyncio
async def test_activity_runs_orchestrator_pass():
    orchestrator, exp_store = _build_orchestrator()
    for _ in range(2):
        await exp_store.store(_exp())

    activities = ConsolidationActivities(orchestrator=orchestrator)
    out = await activities.run_consolidation_pass(
        ConsolidationActivityInput(mode=ConsolidationMode.BACKGROUND)
    )
    assert isinstance(out, ConsolidationActivityOutput)
    assert out.fetched_count == 2
    assert out.accepted_count == 1
    assert activities.call_log == [out]


@pytest.mark.asyncio
async def test_activity_raises_when_orchestrator_unbound():
    activities = ConsolidationActivities()
    with pytest.raises(RuntimeError, match="not bound"):
        await activities.run_consolidation_pass(ConsolidationActivityInput())


@pytest.mark.asyncio
async def test_register_consolidation_activities_binds_module_level():
    orchestrator, exp_store = _build_orchestrator()
    for _ in range(2):
        await exp_store.store(_exp())

    activities = register_consolidation_activities(orchestrator)
    out = await run_consolidation_pass_activity(
        ConsolidationActivityInput(mode=ConsolidationMode.BACKGROUND)
    )
    assert out.fetched_count == 2
    assert activities.orchestrator is orchestrator


@pytest.mark.asyncio
async def test_workflow_loops_max_iterations_times(monkeypatch):
    orchestrator, exp_store = _build_orchestrator()
    for _ in range(2):
        await exp_store.store(_exp())
    register_consolidation_activities(orchestrator)

    sleep_calls: list[int] = []

    async def _no_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("digital_twin.memory.consolidation.workflow._sleep", _no_sleep)

    workflow = ConsolidationWorkflow()
    result = await workflow.run(
        ConsolidationWorkflowInput(
            activity_input=ConsolidationActivityInput(mode=ConsolidationMode.ON_DEMAND),
            interval_seconds=1,
            max_iterations=3,
        )
    )
    assert result.iterations == 3
    assert len(result.per_iteration) == 3
    # Sleep is skipped on the final iteration since the loop exits first.
    assert sleep_calls == [1, 1]


@pytest.mark.asyncio
async def test_workflow_aggregates_accepted_and_rejected(monkeypatch):
    fixed_output = ConsolidationActivityOutput(
        mode=ConsolidationMode.BACKGROUND,
        accepted_count=2,
        rejected_count=1,
    )

    async def _fake_pass(_input: ConsolidationActivityInput) -> ConsolidationActivityOutput:
        return fixed_output

    monkeypatch.setattr("digital_twin.memory.consolidation.workflow._execute_pass", _fake_pass)

    async def _no_sleep(_seconds: int) -> None:
        return None

    monkeypatch.setattr("digital_twin.memory.consolidation.workflow._sleep", _no_sleep)

    workflow = ConsolidationWorkflow()
    result = await workflow.run(ConsolidationWorkflowInput(interval_seconds=1, max_iterations=4))
    assert result.total_accepted == 8
    assert result.total_rejected == 4


def test_default_interval_is_30_minutes():
    input_model = ConsolidationWorkflowInput()
    assert input_model.interval_seconds == 1800


def test_activity_input_round_trips_through_request():
    project_id = uuid4()
    activity_input = ConsolidationActivityInput(
        mode=ConsolidationMode.PROACTIVE,
        project_id=project_id,
        min_importance=0.5,
    )
    request = activity_input.to_request()
    assert request.mode == ConsolidationMode.PROACTIVE
    assert request.project_id == project_id
    assert request.min_importance == 0.5
