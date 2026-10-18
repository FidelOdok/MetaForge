"""An approved tool-call hold stays approved (FORGE-639).

Seen live: every approval of a held MCP write, given on the dashboard or in
the client's own prompt, read as "canceled" an hour later. The hold went
RUNNING at the decision, nothing ever completed it, and the heartbeat reaper
cancelled it as abandoned; the approver lived only on ``Run.approved_by``,
which the ledger does not persist. Now the decision is logged on the hold's
request (persisted) and an approved hold is closed COMPLETED at once.
"""

from __future__ import annotations

import asyncio

import pytest

from api_gateway.approvals import service
from api_gateway.chat import tool_approvals
from api_gateway.chat.tool_approvals import (
    decide_tool_approval,
    get_approval_store,
    record_inline_answer,
    reset_approval_store,
)
from mcp_core.guardrails import Approver
from orchestrator.harness.heartbeat import HeartbeatMonitor
from orchestrator.harness.runs import (
    ApprovalWait,
    RunStatus,
    await_approval_decision,
)


@pytest.fixture(autouse=True)
def _fresh_store() -> None:
    reset_approval_store()


def _hold(route: str = "dashboard") -> str:
    store = get_approval_store()
    run = store.create({"tool": "twin.commit_geometry", "arguments": {}, "route": route})
    store.start(run.id)
    store.request_approval(run.id, reason="writes; held for approval")
    return run.id


def test_a_dashboard_approval_closes_the_hold_and_logs_the_approver() -> None:
    run_id = _hold()
    run = decide_tool_approval(run_id, "approve", Approver(actor_id="user:fidel", verified=True))
    assert run.status is RunStatus.COMPLETED
    (entry,) = run.request["decisions"]
    assert (entry["decision"], entry["approver"], entry["approver_verified"]) == (
        "approve",
        "user:fidel",
        True,
    )
    item = service.tool_item(run)
    assert item.status == "approved"
    assert item.decision is not None and item.decision.approver == "user:fidel"


def test_an_answer_in_the_clients_prompt_is_recorded_too() -> None:
    run_id = _hold(route="elicitation")
    run = record_inline_answer(run_id, approved=True, approver=None, verified=False)
    assert run.status is RunStatus.COMPLETED
    assert run.request["decisions"][0]["approver"] == "local:elicitation"
    assert service.tool_item(run).status == "approved"


def test_a_rejection_is_logged_and_stays_rejected() -> None:
    run_id = _hold()
    run = decide_tool_approval(run_id, "reject", Approver(actor_id="user:fidel", verified=False))
    assert run.status is RunStatus.REJECTED
    assert run.request["decisions"][0]["decision"] == "reject"
    assert service.tool_item(run).status == "rejected"


def test_the_reaper_cannot_cancel_an_approved_hold_any_more() -> None:
    run_id = _hold()
    decide_tool_approval(run_id, "approve", Approver(actor_id="user:fidel", verified=True))
    report = HeartbeatMonitor(clock=lambda: 10**10).sweep(get_approval_store(), stale_after=3600)
    assert run_id not in report.abandoned
    assert get_approval_store().get(run_id).status is RunStatus.COMPLETED


def test_a_hold_decided_before_the_fix_and_then_reaped_shows_its_decision() -> None:
    """What the sandbox ledger holds today: a logged approval, status canceled."""
    store = get_approval_store()
    run_id = _hold()
    store.get(run_id).request["decisions"] = [
        {"decision": "approve", "approver": "local:dashboard", "approver_verified": False}
    ]
    store.submit_approval(run_id, tool_approvals.ApprovalDecision.APPROVE)
    store.cancel(run_id, reason="abandoned")
    assert service.tool_item(store.get(run_id)).status == "approved"


def test_a_hold_reaped_with_no_logged_decision_still_reads_canceled() -> None:
    store = get_approval_store()
    run_id = _hold()
    store.submit_approval(run_id, tool_approvals.ApprovalDecision.APPROVE)
    store.cancel(run_id, reason="abandoned")
    assert service.tool_item(store.get(run_id)).status == "canceled"


def test_the_waiter_takes_a_closed_hold_as_approved() -> None:
    run_id = _hold()

    async def approve_soon(_seconds: float) -> None:
        decide_tool_approval(run_id, "approve", Approver(actor_id="user:fidel", verified=True))

    outcome = asyncio.run(
        await_approval_decision(
            get_approval_store(), run_id, timeout_seconds=5, poll_interval=0, sleep=approve_soon
        )
    )
    assert outcome is ApprovalWait.APPROVED


def test_the_decision_survives_a_gateway_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The ledger has no approved_by column; the logged decision rides on request."""
    from api_gateway.chat.tool_approvals import init_approval_ledger
    from orchestrator.harness.ledger import SqliteRunLedger

    db = str(tmp_path / "approvals.db")
    init_approval_ledger(SqliteRunLedger(db))
    run_id = _hold()
    decide_tool_approval(run_id, "approve", Approver(actor_id="user:fidel", verified=True))
    init_approval_ledger(None)

    reset_approval_store()  # the restart
    init_approval_ledger(SqliteRunLedger(db))
    try:
        restored = get_approval_store().get(run_id)
        item = service.tool_item(restored)
        assert restored.status is RunStatus.COMPLETED
        assert item.status == "approved"
        assert item.decision is not None and item.decision.approver == "user:fidel"
    finally:
        init_approval_ledger(None)
