"""Unit tests for ``DesignEscalator`` (MET-455 Phase 3)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from digital_twin.memory.validation.design_revalidator import (
    DesignRevalidationResult,
    DesignViolation,
)
from orchestrator.event_bus.events import Event, EventType
from orchestrator.workflows.design_escalation import DesignEscalator


class _CapturingBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


def _violation(design_id=None) -> DesignViolation:
    return DesignViolation(design_id=design_id or uuid4(), violations=())


@pytest.mark.asyncio
async def test_clean_result_does_not_escalate():
    bus = _CapturingBus()
    escalator = DesignEscalator(event_bus=bus)
    report = await escalator.escalate(DesignRevalidationResult())
    assert report.escalated is False
    assert report.escalated_count == 0
    assert bus.events == []


@pytest.mark.asyncio
async def test_each_violated_design_produces_an_approval_request():
    d1, d2 = uuid4(), uuid4()
    result = DesignRevalidationResult(
        revalidated_design_ids=(d1, d2),
        violated=(_violation(d1), _violation(d2)),
    )
    escalator = DesignEscalator()
    report = await escalator.escalate(result, run_id="run-1")

    assert report.escalated_count == 2
    work_products = {req.work_product_ids[0] for req in report.approval_requests}
    assert work_products == {str(d1), str(d2)}
    assert all(req.required_role == "engineer" for req in report.approval_requests)
    assert all(req.run_id == "run-1" for req in report.approval_requests)


@pytest.mark.asyncio
async def test_publishes_approval_requested_event_per_design():
    d1 = uuid4()
    result = DesignRevalidationResult(
        revalidated_design_ids=(d1,),
        violated=(_violation(d1),),
    )
    bus = _CapturingBus()
    await DesignEscalator(event_bus=bus).escalate(result)

    assert len(bus.events) == 1
    event = bus.events[0]
    assert event.type is EventType.APPROVAL_REQUESTED
    assert event.data["design_id"] == str(d1)
    assert event.data["required_role"] == "engineer"


@pytest.mark.asyncio
async def test_works_without_an_event_bus():
    d1 = uuid4()
    result = DesignRevalidationResult(
        revalidated_design_ids=(d1,),
        violated=(_violation(d1),),
    )
    report = await DesignEscalator().escalate(result)
    assert report.escalated_count == 1


@pytest.mark.asyncio
async def test_custom_required_role_is_honored():
    d1 = uuid4()
    result = DesignRevalidationResult(
        revalidated_design_ids=(d1,),
        violated=(_violation(d1),),
    )
    report = await DesignEscalator(required_role="lead_engineer").escalate(result)
    assert report.approval_requests[0].required_role == "lead_engineer"
