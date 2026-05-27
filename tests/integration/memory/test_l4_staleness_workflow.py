"""Integration test for the MET-455 L4-staleness workflow.

Exercises the full spec-change chain end to end against the *real*
constraint engine and event bus:

    L4 spec change
      → StalenessInvalidator marks citing insights STALE_WARN
      → DesignRevalidator finds designs now violating constraints
      → DesignEscalator raises engineer-review approval gates (events)

then the rollback path:

    spec reverted
      → constraint restored to passing
      → DesignRevalidator passes, no escalation
      → StalenessInvalidator restores insights to ACTIVE
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.memory.consolidation.insight import (
    Insight,
    InsightKind,
    InsightStatus,
)
from digital_twin.memory.consolidation.staleness import StalenessInvalidator
from digital_twin.memory.consolidation.themes import ConsolidationTheme
from digital_twin.memory.consolidation.writer import InMemoryInsightStore
from digital_twin.memory.validation.design_revalidator import DesignRevalidator
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventBus, EventSubscriber
from orchestrator.workflows.design_escalation import DesignEscalator
from twin_core.constraint_engine import InMemoryConstraintEngine
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import (
    Constraint,
    ConstraintSeverity,
    WorkProduct,
    WorkProductType,
)


class _ApprovalRecorder(EventSubscriber):
    def __init__(self) -> None:
        self.events: list[Event] = []

    @property
    def subscriber_id(self) -> str:
        return "test-approval-recorder"

    @property
    def event_types(self) -> set[EventType] | None:
        return {EventType.APPROVAL_REQUESTED}

    async def on_event(self, event: Event) -> None:
        self.events.append(event)


def _stale_spec_insight(experience_id) -> Insight:
    return Insight(
        id=uuid4(),
        theme=ConsolidationTheme.COMPONENT_SELECTION,
        kind=InsightKind.PRINCIPLE,
        narrative="The LDO holds regulation down to 100mA across the temp range",
        confidence=0.9,
        supporting_experience_ids=[experience_id],
        status=InsightStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_spec_change_invalidates_revalidates_and_escalates():
    graph = InMemoryGraphEngine()
    engine = InMemoryConstraintEngine(graph)

    # A design validated against the (now superseded) spec.
    design = WorkProduct(
        name="power_tree",
        type=WorkProductType.SCHEMATIC,
        domain="electronics",
        file_path="eda/power_tree.kicad_sch",
        content_hash="abc123",
        format="kicad_sch",
        created_by="human",
    )
    await graph.add_node(design)

    # The spec change breaks a previously-passing ERROR constraint.
    broken = Constraint(
        name="ldo_dropout",
        expression="False",
        severity=ConstraintSeverity.ERROR,
        domain="electronics",
        cross_domain=False,
        source="datasheet",
    )
    await engine.add_constraint(broken, [design.id])

    # An insight synthesized from the experience that cited the old spec.
    spec_experience_id = uuid4()
    store = InMemoryInsightStore()
    insight = _stale_spec_insight(spec_experience_id)
    await store.write(insight)

    bus = EventBus()
    recorder = _ApprovalRecorder()
    bus.subscribe(recorder)

    invalidator = StalenessInvalidator(store)
    revalidator = DesignRevalidator(engine)
    escalator = DesignEscalator(event_bus=bus)

    # 1. L4 reports the spec experience invalidated → insight goes stale.
    inval = await invalidator.invalidate_by_experiences({spec_experience_id})
    assert inval.invalidated_count == 1
    after = await store.get(insight.id)
    assert after is not None and after.status is InsightStatus.STALE_WARN

    # 2. Revalidate the affected design → now violating.
    revalidation = await revalidator.revalidate({design.id})
    assert revalidation.passed is False
    assert design.id in revalidation.violated_design_ids

    # 3. Escalate → engineer-review approval gate raised + event emitted.
    report = await escalator.escalate(revalidation, run_id="l4-run")
    assert report.escalated_count == 1
    assert report.approval_requests[0].work_product_ids == [str(design.id)]
    assert len(recorder.events) == 1
    assert recorder.events[0].data["design_id"] == str(design.id)


@pytest.mark.asyncio
async def test_spec_revert_restores_insights_and_clears_violations():
    graph = InMemoryGraphEngine()
    engine = InMemoryConstraintEngine(graph)

    design = WorkProduct(
        name="power_tree",
        type=WorkProductType.SCHEMATIC,
        domain="electronics",
        file_path="eda/power_tree.kicad_sch",
        content_hash="abc123",
        format="kicad_sch",
        created_by="human",
    )
    await graph.add_node(design)

    broken = Constraint(
        name="ldo_dropout",
        expression="False",
        severity=ConstraintSeverity.ERROR,
        domain="electronics",
        cross_domain=False,
        source="datasheet",
    )
    await engine.add_constraint(broken, [design.id])

    spec_experience_id = uuid4()
    store = InMemoryInsightStore()
    insight = _stale_spec_insight(spec_experience_id)
    await store.write(insight)

    invalidator = StalenessInvalidator(store)
    revalidator = DesignRevalidator(engine)
    escalator = DesignEscalator()

    # Get into the broken/stale state first.
    await invalidator.invalidate_by_experiences({spec_experience_id})
    broken_state = await revalidator.revalidate({design.id})
    assert broken_state.passed is False

    # --- Spec reverted: the datasheet rollback removes the broken constraint
    #     and L4 reports the spec experience valid again. ---
    assert await engine.remove_constraint(broken.id) is True

    revalidation = await revalidator.revalidate({design.id})
    assert revalidation.passed is True

    report = await escalator.escalate(revalidation)
    assert report.escalated is False

    restore = await invalidator.restore_by_experiences({spec_experience_id})
    assert restore.restored_count == 1
    reloaded = await store.get(insight.id)
    assert reloaded is not None and reloaded.status is InsightStatus.ACTIVE
