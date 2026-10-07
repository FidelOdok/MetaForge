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

from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.runs.schemas import (
    ApprovalRequest,
    RunListResponse,
    RunResponse,
    filter_by_project,
)
from mcp_core.guardrails import Approver
from observability.metrics import MetricsCollector, collector_for
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.runs import (
    UNANSWERED,
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

#: How long a held call's waiter says it will wait, when it does not say
#: (FORGE-466). Matches the sidecar's and the in-process gate's window.
DEFAULT_HOLD_TIMEOUT_SECONDS = 180.0
#: Slack past the waiter's own window before the gateway expires a hold on
#: its behalf. The waiter is meant to close its hold itself; this only
#: catches the one that died without saying so, and must not race it.
HOLD_DEADLINE_GRACE_SECONDS = 30.0
#: Upper bound on a requested window, so a client cannot park an approval
#: that outlives every reviewer's attention.
MAX_HOLD_TIMEOUT_SECONDS = 3600.0

_metrics: MetricsCollector | None = None


def _get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = collector_for("metaforge-gateway")
    return _metrics


def set_approval_metrics(metrics: MetricsCollector | None) -> None:
    """Inject a collector (tests), or ``None`` to resolve it lazily again."""
    global _metrics
    _metrics = metrics


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


# FORGE-490: only the gateway serves the routes that answer a hold in this
# store. A worker process imports the same module but never mounts them, so its
# store has no approver. The gateway lifespan flips this on.
_approver_reachable = False


def mark_approver_reachable() -> None:
    global _approver_reachable
    _approver_reachable = True


def approver_reachable() -> bool:
    return _approver_reachable


def reset_approval_store(*, clock: Any = None) -> None:
    """Rewire a fresh store — tests only, mirrors api_gateway.runs.routes."""
    global _approval_store, _ledger
    _approval_store = (
        InMemoryRunStore(on_transition=_on_transition)
        if clock is None
        else InMemoryRunStore(on_transition=_on_transition, clock=clock)
    )
    _ledger = None


def expire_overdue_holds() -> list[Run]:
    """Time out every hold whose waiter is past its deadline (FORGE-466).

    Called on every read of the ledger, so the Approvals page never offers a
    call nobody is waiting for, even when the waiter crashed and could not
    close its own hold. Reads are frequent enough (the dashboard polls) that
    a separate sweeper would add a task to keep alive and nothing else.
    """
    expired = _approval_store.expire_overdue()
    for run in expired:
        logger.warning(
            "tool_approval_hold_expired",
            run_id=run.id,
            tool=run.request.get("tool"),
            source=run.request.get("source"),
            deadline=run.approval_deadline,
        )
        _get_metrics().record_tool_approval_resolution("timed_out", "deadline", "resolved")
    return expired


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
_FLOW_VERSION_KINDS = frozenset(
    {"design_flow_proposal", "design_flow_version", "design_flow_patch"}  # FORGE-539
)


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
def list_pending_approvals(
    status: Literal["pending", "all"] = "pending",
    project_id: str | None = None,
) -> RunListResponse:
    """Tool-call approvals: the ones awaiting a decision, or every one.

    Overdue holds are expired first, so nothing listed as pending is a call
    whose waiter has already given up (FORGE-466). ``status=all`` is the audit
    view (FORGE-473): every entry whichever route answered it, each carrying
    its ``route``, outcome and approver.

    ``project_id`` scopes the queue. Unscoped before, so a reviewer working
    on one project saw every project's held writes in one list -- and a held
    write names a tool and a caller, not a product, so there was no way to
    tell from the row which one it belonged to. Approvals whose tool call
    carried no project are counted rather than dropped: an approval that
    quietly disappears is the one nobody answers.
    """
    expire_overdue_holds()
    runs = _approval_store.list()
    if status == "pending":
        runs = [r for r in runs if r.status is RunStatus.AWAITING_APPROVAL]
    runs, unscoped = filter_by_project(list(runs), project_id)
    return RunListResponse(runs=[RunResponse.from_run(r) for r in runs], unscoped_count=unscoped)


@router.get("/{run_id}", response_model=RunResponse)
def get_approval(run_id: str) -> RunResponse:
    expire_overdue_holds()
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
    #: How the question is being asked (FORGE-473): ``dashboard`` waits for a
    #: click on the Approvals page, ``elicitation`` is asked inside the
    #: client's own prompt and closed by the sidecar with the answer.
    route: Literal["dashboard", "elicitation"] = "dashboard"
    #: The MCP client that made the call, for the audit trail.
    client: str | None = None
    #: How long the caller will wait for an answer (FORGE-466). The gateway
    #: stores a deadline from it and expires the hold if the caller never
    #: closes it, so a crashed waiter cannot leave it pending forever.
    timeout_seconds: float | None = Field(default=None, gt=0, le=MAX_HOLD_TIMEOUT_SECONDS)


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
            "route": body.route,
            "client": body.client,
        }
    )
    _approval_store.start(run.id)
    window = body.timeout_seconds or DEFAULT_HOLD_TIMEOUT_SECONDS
    run = _approval_store.request_approval(
        run.id,
        reason=body.reason,
        deadline=_approval_store.now() + window + HOLD_DEADLINE_GRACE_SECONDS,
    )
    logger.info(
        "tool_approval_held",
        run_id=run.id,
        tool=body.tool,
        caller=body.caller,
        source=body.source,
        route=body.route,
        deadline=run.approval_deadline,
    )
    return RunResponse.from_run(run)


class ResolveHoldRequest(BaseModel):
    """Close a hold nobody is waiting for any more (FORGE-466)."""

    #: ``approved`` and ``rejected`` are an inline answer (FORGE-473): the
    #: person answered in the client's own prompt, and the sidecar that saw
    #: it records the result here.
    outcome: Literal["timed_out", "canceled", "approved", "rejected"]
    reason: str | None = None
    #: Who answered, as established by the sidecar from the authenticated MCP
    #: session. Only used with ``approved`` / ``rejected``.
    approver: str | None = None
    approver_verified: bool = False


@router.post("/{run_id}/resolve", response_model=RunResponse)
def resolve_unanswered_hold(run_id: str, body: ResolveHoldRequest, request: Request) -> RunResponse:
    """The waiting side stopped waiting: close the hold so nobody answers it.

    Idempotent. A hold already ``timed_out`` or ``canceled`` comes back as it
    is with 200, so a retry after a lost response is harmless. A hold a human
    already decided is 409: the waiter must read the decision back rather
    than overwrite it, because an approval that landed in the last instant is
    still an approval.
    """
    try:
        run = _approval_store.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc
    if run.status in UNANSWERED:
        logger.info(
            "tool_approval_hold_already_resolved",
            run_id=run_id,
            status=run.status.value,
            requested=body.outcome,
        )
        return RunResponse.from_run(run)
    if run.status is not RunStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"approval '{run_id}' was already decided ({run.status.value}); "
            "read the decision instead of closing it",
        )
    if body.outcome in ("approved", "rejected"):
        run = _record_inline_answer(run_id, body, request)
    elif body.outcome == "timed_out":
        run = _approval_store.time_out(
            run_id, reason=body.reason or "the waiting call timed out before anyone answered"
        )
    else:
        run = _approval_store.cancel(
            run_id, reason=body.reason or "the waiting call was cancelled before anyone answered"
        )
    logger.info(
        "tool_approval_hold_resolved",
        run_id=run_id,
        status=run.status.value,
        tool=run.request.get("tool"),
        source=run.request.get("source"),
    )
    return RunResponse.from_run(run)


def record_inline_answer(
    run_id: str, *, approved: bool, approver: str | None, verified: bool
) -> Run:
    """Close a hold with the answer a person gave in the client's own prompt.

    Shared by the sidecar's route and the in-process ledger so both write the
    same entry. ``approved_by`` falls back to ``local:elicitation`` rather than
    being left empty: somebody answered, and what is known is that they did it
    inline on an identity-less session.
    """
    from mcp_core.elicitation import LOCAL_ELICITATION_ACTOR

    run = _approval_store.submit_approval(
        run_id,
        ApprovalDecision.APPROVE if approved else ApprovalDecision.REJECT,
        approved_by=approver or LOCAL_ELICITATION_ACTOR,
        approver_verified=verified if approver else False,
    )
    logger.info(
        "tool_approval_answered_inline",
        run_id=run_id,
        approved=approved,
        approved_by=run.approved_by,
        approver_verified=run.approver_verified,
    )
    return run


def _record_inline_answer(run_id: str, body: ResolveHoldRequest, request: Request) -> Run:
    # A claim of "verified" is honoured only when this request itself came in
    # authenticated. On an open gateway anyone can POST here, and an identity
    # nobody checked must not be recorded as one somebody did (FORGE-393).
    from api_gateway.auth.dependencies import current_principal

    verified = body.approver_verified and current_principal(request) is not None
    try:
        return record_inline_answer(
            run_id,
            approved=body.outcome == "approved",
            approver=body.approver,
            verified=verified,
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}", response_model=RunResponse)
def submit_tool_approval(run_id: str, body: ApprovalRequest, request: Request) -> RunResponse:
    """Record the decision, attributed to whoever made this request.

    The identity comes from the request, not the body (FORGE-393). A client
    cannot nominate the approver, which is the whole point: some tools write
    the approver's name down as their result.
    """
    run = decide_tool_approval(run_id, body.decision, approver_from_request(request))
    return RunResponse.from_run(run)


def decide_tool_approval(run_id: str, decision: str, approver: Approver) -> Run:
    """Answer a held tool call. Shared by this route and ``/v1/approvals`` (FORGE-507).

    Refusals are ``HTTPException`` s (404, 409, 422) so both surfaces apply
    one set of rules.
    """
    expire_overdue_holds()
    try:
        current = _approval_store.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc
    if current.status in UNANSWERED:
        # FORGE-466: a success here would record an approval for a call
        # nobody is waiting for. Say why it cannot be answered instead.
        logger.info(
            "tool_approval_submit_refused",
            run_id=run_id,
            status=current.status.value,
            decision=decision,
        )
        raise HTTPException(
            status_code=409,
            detail=f"approval '{run_id}' is {current.status.value}: "
            f"{current.error or 'nobody is waiting for this call any more'}. "
            "Nothing was recorded; ask for the call again if it is still wanted.",
        )
    if (
        current.request.get("route") == "elicitation"
        and current.status is RunStatus.AWAITING_APPROVAL
    ):
        # FORGE-473: the question is on screen in the client right now, and
        # that prompt is the one that gets the answer. A click here would be
        # recorded and then overwritten by (or fight with) the inline answer.
        raise HTTPException(
            status_code=409,
            detail=f"approval '{run_id}' is being answered in the client's own approval "
            "prompt. Answer it there; it appears here as resolved once you do.",
        )
    if decision in (ApprovalDecision.RETRY.value, ApprovalDecision.REWORK.value):
        # FORGE-495/500: retry and rework re-run design-flow phases; a tool call has none.
        raise HTTPException(
            status_code=422,
            detail=f"'{decision}' applies to design-flow gates, not tool approvals",
        )
    try:
        run = _approval_store.submit_approval(
            run_id,
            ApprovalDecision(decision),
            approved_by=approver.label,
            approver_verified=approver.verified,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _decide_flow_version(run, approved=decision == ApprovalDecision.APPROVE.value)
    logger.info(
        "tool_approval_submitted",
        run_id=run_id,
        decision=decision,
        approved_by=approver.actor_id,
        approver_verified=approver.verified,
    )
    return run
