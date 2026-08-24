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

import structlog
from fastapi import APIRouter, HTTPException

from api_gateway.runs.schemas import ApprovalRequest, RunListResponse, RunResponse
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    RunNotFoundError,
    RunStatus,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/chat/tool_approvals", tags=["chat-tool-approvals"])

# Process-level (not per-turn) so a separate approval-decision request can
# reach the same live run a paused call_tool() is polling. Pass this same
# instance into build_agent_runtime(runs=...) from the chat harness wiring.
_approval_store = InMemoryRunStore()


def get_approval_store() -> InMemoryRunStore:
    return _approval_store


def reset_approval_store() -> None:
    """Rewire a fresh store — tests only, mirrors api_gateway.runs.routes."""
    global _approval_store
    _approval_store = InMemoryRunStore()


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


@router.post("/{run_id}", response_model=RunResponse)
def submit_tool_approval(run_id: str, body: ApprovalRequest) -> RunResponse:
    try:
        run = _approval_store.submit_approval(run_id, ApprovalDecision(body.decision))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("tool_approval_submitted", run_id=run_id, decision=body.decision)
    return RunResponse.from_run(run)
