"""Tool-call approval REST surface (production-harness audit follow-up).

The third permission tier, "ask" (`orchestrator.harness.tools.ToolSpec.requires_approval`)
pauses `HarnessRuntime.call_tool` on a shared, process-level `InMemoryRunStore`
(see `HarnessRuntime._await_approval`). This module owns that process-level
store and exposes the one thing an external caller needs: a way to submit an
approve/reject decision for a paused tool call, so a separate HTTP request
(from a chat UI, a CLI, curl) can resolve it while the streaming chat turn is
still waiting.

A dashboard UI affordance (an actual approve/reject button) is out of scope
for this backend-focused pass -- this endpoint is the complete mechanism,
just without a frontend wired to it yet.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.runs.schemas import ApprovalRequest, RunListResponse, RunResponse
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    Run,
    RunNotFoundError,
    RunStatus,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/chat/tool_approvals", tags=["chat-tool-approvals"])

_ledger: SqliteRunLedger | None = None

# A gateway restart drops any in-flight `HarnessRuntime._await_approval()`
# coroutine along with the process, so a restored AWAITING_APPROVAL row can
# never actually be resolved -- the chat turn that was waiting on it is gone.
# Persist the record for audit/history (FORGE-89), but mark it FAILED/orphaned
# rather than resumable, unlike design-flow's own init_run_ledger() (which
# restores AWAITING_APPROVAL as-is because a design-flow run genuinely can
# resume). Do not copy that precedent here.
_ORPHANED_ERROR = "orphaned: gateway restarted while this approval was pending"


def _on_transition(run: Run) -> None:
    if _ledger is not None:
        try:
            _ledger.record_run(run)
        except Exception as exc:
            logger.warning("tool_approval_ledger_write_failed", run_id=run.id, error=str(exc))


# Process-level (not per-turn) so a separate approval-decision request can
# reach the same live run a paused call_tool() is polling. Pass this same
# instance into build_agent_runtime(runs=...) from the chat harness wiring.
_approval_store = InMemoryRunStore(on_transition=_on_transition)


def get_approval_store() -> InMemoryRunStore:
    return _approval_store


def reset_approval_store() -> None:
    """Rewire a fresh store — tests only, mirrors api_gateway.runs.routes."""
    global _approval_store, _ledger
    _approval_store = InMemoryRunStore(on_transition=_on_transition)
    _ledger = None


def init_approval_ledger(ledger: SqliteRunLedger | None) -> None:
    """Wire a durable ledger for chat tool-approvals (FORGE-89).

    Any row restored in AWAITING_APPROVAL is marked FAILED/orphaned instead
    of resumable -- see the module-level note on `_ORPHANED_ERROR`.
    """
    global _ledger
    _ledger = ledger
    if ledger is None:
        return
    restored = 0
    orphaned = 0
    for row in ledger.list_runs():
        status = RunStatus(row["status"])
        error = row["error"]
        if status is RunStatus.AWAITING_APPROVAL:
            status = RunStatus.FAILED
            error = _ORPHANED_ERROR
            orphaned += 1
        _approval_store.restore(
            Run(
                id=row["id"],
                status=status,
                request=row["request"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                error=error,
                result=row["result"],
                history=[status],
            )
        )
        restored += 1
    logger.info("tool_approval_ledger_wired", restored=restored, orphaned=orphaned)


#: Approvals whose answer is also the decision on a stored flow version.
_FLOW_VERSION_KINDS = frozenset({"design_flow_proposal", "design_flow_version"})


def _decide_flow_version(run: Run, *, approved: bool) -> None:
    """Carry an approval's answer to the flow version it was about (FORGE-462).

    ``flow.propose`` and ``POST /v1/design-flows/versions`` hold a version for
    a person and park an approval for it. Answering that approval moved the
    approval and nothing else: the version stayed ``proposed``, so
    ``POST /v1/runs`` refused it with 409 forever, and "approve, then start"
    could not be done by anyone.

    The approver is the one recorded on the approval run, never anything the
    request body said (FORGE-393).
    """
    if run.request.get("kind") not in _FLOW_VERSION_KINDS:
        return
    version_id = run.request.get("flow_version_id")
    if not version_id:
        return
    from orchestrator.design_flow.versions import VersionNotFoundError, get_version_store

    try:
        get_version_store().decide(
            str(version_id), approved=approved, decided_by=run.approved_by or ""
        )
    except (VersionNotFoundError, ValueError) as exc:
        # The approval itself is recorded either way. A version that is gone
        # (gateway restart) or already decided is reported, not raised: the
        # person's answer must not be turned into an error after it landed.
        logger.warning(
            "flow_version_decision_not_applied",
            approval_id=run.id,
            version_id=version_id,
            error=str(exc),
        )
        return
    logger.info(
        "flow_version_decided_by_approval",
        approval_id=run.id,
        version_id=version_id,
        approved=approved,
    )


@router.get("", response_model=RunListResponse)
def list_pending_approvals() -> RunListResponse:
    """All tool-call approvals currently awaiting a decision."""
    pending = [r for r in _approval_store.list() if r.status is RunStatus.AWAITING_APPROVAL]
    return RunListResponse(runs=[RunResponse.from_run(r) for r in pending])


@router.get("/{run_id}", response_model=RunResponse)
def get_approval(run_id: str) -> RunResponse:
    try:
        return RunResponse.from_run(_approval_store.get(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc


class HoldToolCallRequest(BaseModel):
    """Park a tool call from another process in this ledger (FORGE-406)."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str
    caller: str = "untrusted"
    source: str = "mcp"
    session_id: str | None = None
    project: str | None = None


@router.post("", response_model=RunResponse, status_code=201)
def hold_tool_call(body: HoldToolCallRequest) -> RunResponse:
    """Create a held approval and return it.

    This exists because the approval store is process-level (an
    ``InMemoryRunStore`` in *this* process), and the MCP sidecar is a
    different process. Before FORGE-406 a sidecar could only hold calls in
    its own memory, where the dashboard — served from here — would never see
    them. So the sidecar parks them here instead, and there is exactly one
    ledger rather than one per process.

    A second store would have been the more obvious fix and the wrong one:
    two queues means a reviewer clearing one while the other fills, and no
    page that shows both.
    """
    run = _approval_store.create(
        {
            "tool": body.tool,
            "arguments": body.arguments,
            "caller": body.caller,
            "source": body.source,
            "session_id": body.session_id,
            "project": body.project,
        }
    )
    _approval_store.start(run.id)
    run = _approval_store.request_approval(run.id, reason=body.reason)
    logger.info(
        "tool_approval_held",
        run_id=run.id,
        tool=body.tool,
        caller=body.caller,
        source=body.source,
    )
    return RunResponse.from_run(run)


@router.post("/{run_id}", response_model=RunResponse)
def submit_tool_approval(run_id: str, body: ApprovalRequest, request: Request) -> RunResponse:
    """Record the decision, attributed to whoever made this request.

    The identity comes from the request, not the body (FORGE-393). A client
    cannot nominate the approver, which is the whole point: some tools write
    the approver's name down as their result.
    """
    approver = approver_from_request(request)
    try:
        run = _approval_store.submit_approval(
            run_id,
            ApprovalDecision(body.decision),
            approved_by=approver.label,
            approver_verified=approver.verified,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _decide_flow_version(run, approved=body.decision == ApprovalDecision.APPROVE.value)
    logger.info(
        "tool_approval_submitted",
        run_id=run_id,
        decision=body.decision,
        approved_by=approver.actor_id,
        approver_verified=approver.verified,
    )
    return RunResponse.from_run(run)
