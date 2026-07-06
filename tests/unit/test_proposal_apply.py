"""Apply-on-approve executor + workflow.mark_applied (MET-548, Phase 3b)."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.assistant.apply import make_apply_executor
from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.schemas import ChangeStatus, EventType, WebSocketEvent


@pytest.mark.asyncio
async def test_apply_executor_runs_record_decision() -> None:
    seen: dict = {}

    async def decision_recorder(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"node_id": "n-1", "content_hash": "abc"}

    apply = make_apply_executor(decision_recorder)
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="Record the slot decision",
        diff={"action": "record_decision", "rationale": "slots relieve stress"},
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is True
    assert result["node_id"] == "n-1"
    assert seen["rationale"] == "slots relieve stress"


@pytest.mark.asyncio
async def test_apply_executor_unsupported_action_is_explicit() -> None:
    apply = make_apply_executor(decision_recorder=None)
    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME",
        description="regen",
        diff={"action": "regenerate_geometry"},
        work_products=[],
    )
    result = await apply(proposal)
    assert result["applied"] is False
    assert "not yet supported" in result["reason"]


@pytest.mark.asyncio
async def test_mark_applied_sets_status_and_emits() -> None:
    from api_gateway.assistant.schemas import ApprovalDecisionType

    wf = ApprovalWorkflow()
    proposal = await wf.propose_change(
        agent_code="ME", description="d", diff={"action": "record_decision"}, work_products=[]
    )
    queue = wf.subscribe(proposal.session_id)

    # can't apply before approval
    await wf.mark_applied(proposal.change_id)
    assert proposal.status == ChangeStatus.PENDING

    await wf.decide(
        change_id=proposal.change_id,
        decision=ApprovalDecisionType.APPROVE,
        reason="ok",
        reviewer="me",
    )
    await wf.mark_applied(proposal.change_id, {"node_id": "n-9"})
    assert proposal.status == ChangeStatus.APPLIED

    seen: list[WebSocketEvent] = []
    while not queue.empty():
        seen.append(queue.get_nowait())
    assert any(e.event_type == EventType.CHANGE_APPLIED for e in seen)
