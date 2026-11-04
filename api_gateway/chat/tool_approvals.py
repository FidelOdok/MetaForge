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

import threading
import time
from datetime import UTC, datetime
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
    project_of,
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
        if run.request.get(APPROVAL_KEY):
            # FORGE-780: a durable hold nobody decided within its lifetime.
            logger.info(
                "tool_approval_expired",
                run_id=run.id,
                tool=run.request.get("tool"),
                project=run.request.get("project"),
                state="pending",
            )
            _record_outcome("expired")
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
    durable = 0
    now = _approval_store.now()
    for row in ledger.list_runs():
        status = RunStatus(row["status"])
        error = row["error"]
        request = row["request"] or {}
        deadline: float | None = None
        if status is RunStatus.AWAITING_APPROVAL:
            expires = request.get("expires_at")
            if request.get(APPROVAL_KEY) and isinstance(expires, (int, float)):
                # FORGE-780: a durable hold has no waiter to lose. It stays
                # decidable for the rest of its lifetime, and the identical
                # retry still finds it.
                if expires > now:
                    deadline = float(expires) + HOLD_DEADLINE_GRACE_SECONDS
                    durable += 1
                else:
                    status = RunStatus.TIMED_OUT
                    error = "expired while the gateway was down"
            else:
                status = RunStatus.FAILED
                error = _ORPHANED_ERROR
                orphaned += 1
        decided = _last_decision(request)
        _approval_store.restore(
            Run(
                id=row["id"],
                status=status,
                request=request,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                error=error,
                result=row["result"],
                history=[status],
                approval_deadline=deadline,
                # The approver lives in the persisted decision log; the run's
                # own field is not a ledger column (FORGE-507).
                approved_by=decided.get("approver") if decided else None,
                approver_verified=bool(decided.get("approver_verified")) if decided else False,
            )
        )
        restored += 1
    logger.info("tool_approval_ledger_wired", restored=restored, orphaned=orphaned, durable=durable)


def _last_decision(request: dict[str, Any]) -> dict[str, Any] | None:
    log = request.get(_DECISIONS_KEY)
    if isinstance(log, list) and log and isinstance(log[-1], dict):
        return log[-1]
    return None


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


def project_for_hold(project: str | None, arguments: dict[str, Any]) -> str | None:
    """The project a held call lands in: the caller's, else its run's (FORGE-732).

    ``phase.claim`` and the other task tools name a run (``run_id``, or a
    ``task_id`` of ``<run_id>:<phase>:<n>``) and no project, so their holds
    were written unscoped and hidden on the project-scoped Approvals page the
    refusal sent the person to. The run knows its project. Best-effort: an
    unknown run leaves the hold unscoped, which the page still counts.
    """
    if project:
        return project
    for key in ("run_id", "task_id"):
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.startswith("run_"):
            continue
        try:
            from api_gateway.runs.routes import get_run_store

            run = get_run_store().get(raw.split(":", 1)[0])
        except Exception as exc:  # noqa: BLE001 - scoping is best-effort
            logger.info("tool_approval_project_unresolved", key=key, error=str(exc))
            continue
        found = project_of(run.request or {})
        if found:
            return found
    return None


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
    #: For a durable hold (FORGE-780) it is the approval's lifetime.
    timeout_seconds: float | None = Field(default=None, gt=0, le=MAX_HOLD_TIMEOUT_SECONDS)
    #: FORGE-780: the durable approval this hold is, keyed by caller session,
    #: tool, canonical arguments and project. A live hold with the same key
    #: is returned instead of a second one being opened.
    approval_key: str | None = Field(default=None, max_length=128)
    #: The canonical hash of the arguments, for the audit trail.
    arguments_hash: str | None = Field(default=None, max_length=128)


@router.post("", response_model=RunResponse, status_code=201)
def hold_tool_call(body: HoldToolCallRequest) -> RunResponse:
    """Create a held approval and return it.

    This exists because the approval store is process-level (an
    ``InMemoryRunStore`` in *this* process), and the MCP sidecar is a
    different process. A sidecar used to hold calls only in
    its own memory, where the dashboard — served from here — would never see
    them. So the sidecar parks them here instead, and there is exactly one
    ledger rather than one per process.

    A second store would have been the more obvious fix and the wrong one:
    two queues means a reviewer clearing one while the other fills, and no
    page that shows both.

    With an ``approval_key`` (FORGE-780) the hold is a durable approval: a
    live one with the same key is returned rather than a second opened.
    """
    return RunResponse.from_run(
        create_hold(
            tool=body.tool,
            arguments=body.arguments,
            reason=body.reason,
            caller=body.caller,
            source=body.source,
            session_id=body.session_id,
            project=body.project,
            route=body.route,
            client=body.client,
            timeout_seconds=body.timeout_seconds,
            approval_key=body.approval_key,
            arguments_hash=body.arguments_hash,
        )
    )


# -- Durable approvals (FORGE-780) --------------------------------------------
#
# A held call used to block until it was answered, and the client gave up
# first; an approval that came later was recorded and never applied. A hold
# with an ``approval_key`` is a record that outlives its call: it is decided
# from anywhere (the client's prompt, /v1/approvals, approvals.decide; the
# first decision wins), and the identical retry runs once on an approval.

#: Request fields a durable hold carries.
APPROVAL_KEY = "approval_key"
_EXPIRES_AT = "expires_at"
_CONSUMED_AT = "consumed_at"

#: Serialises using a decided hold, so one approval runs one call. Sync route
#: handlers run in a thread pool, so an event loop alone does not order them.
_consume_lock = threading.Lock()

_APPROVED = frozenset({RunStatus.RUNNING, RunStatus.COMPLETED})


def _record_outcome(outcome: str) -> None:
    try:
        _get_metrics().record_tool_approval(outcome)
    except Exception:  # noqa: BLE001, S110 - a metric must not fail an approval
        pass


def _iso_now() -> str:
    return datetime.fromtimestamp(time.time(), UTC).isoformat()


def create_hold(
    *,
    tool: str,
    arguments: dict[str, Any],
    reason: str,
    caller: str = "untrusted",
    source: str = "mcp",
    session_id: str | None = None,
    project: str | None = None,
    route: str = "dashboard",
    client: str | None = None,
    timeout_seconds: float | None = None,
    approval_key: str | None = None,
    arguments_hash: str | None = None,
) -> Run:
    """Open a held approval, or return the live durable one with the same key."""
    if approval_key:
        existing = find_live_hold(approval_key)
        if existing is not None:
            logger.info(
                "tool_approval_hold_reused",
                run_id=existing.id,
                tool=tool,
                status=existing.status.value,
            )
            return existing
    window = min(timeout_seconds or DEFAULT_HOLD_TIMEOUT_SECONDS, MAX_HOLD_TIMEOUT_SECONDS)
    request: dict[str, Any] = {
        "tool": tool,
        "arguments": arguments,
        "caller": caller,
        "source": source,
        "session_id": session_id,
        "project": project_for_hold(project, arguments),
        "route": route,
        "client": client,
    }
    if approval_key:
        request[APPROVAL_KEY] = approval_key
        request["arguments_hash"] = arguments_hash
        request[_EXPIRES_AT] = _approval_store.now() + window
    run = _approval_store.create(request)
    _approval_store.start(run.id)
    run = _approval_store.request_approval(
        run.id,
        reason=reason,
        deadline=_approval_store.now() + window + HOLD_DEADLINE_GRACE_SECONDS,
    )
    logger.info(
        "tool_approval_held",
        run_id=run.id,
        tool=tool,
        caller=caller,
        source=source,
        route=route,
        deadline=run.approval_deadline,
        durable=bool(approval_key),
        arguments_hash=arguments_hash,
    )
    if approval_key:
        _record_outcome("held")
    return run


def _is_live(run: Run, now: float) -> bool:
    """Pending, or decided, unused and inside its lifetime."""
    request = run.request
    if request.get(_CONSUMED_AT):
        return False
    if run.status is RunStatus.AWAITING_APPROVAL:
        return True
    if run.status in _APPROVED or run.status is RunStatus.REJECTED:
        expires = request.get(_EXPIRES_AT)
        return not isinstance(expires, (int, float)) or now < expires
    return False


def find_live_hold(approval_key: str) -> Run | None:
    """The newest live durable hold for ``approval_key``, if any."""
    expire_overdue_holds()
    now = _approval_store.now()
    keyed = [r for r in _approval_store.list() if r.request.get(APPROVAL_KEY) == approval_key]
    for run in keyed:
        _note_unused_expiry(run, now)
    matches = [r for r in keyed if _is_live(r, now)]
    return max(matches, key=lambda r: r.created_at) if matches else None


def _note_unused_expiry(run: Run, now: float) -> None:
    """Record, once, an approval that ran out of lifetime before it was used."""
    request = run.request
    expires = request.get(_EXPIRES_AT)
    if (
        run.status in _APPROVED
        and not request.get(_CONSUMED_AT)
        and not request.get("expired_unused_at")
        and isinstance(expires, (int, float))
        and now >= expires
    ):
        _approval_store.annotate(run.id, "expired_unused_at", _iso_now())
        logger.info(
            "tool_approval_expired",
            run_id=run.id,
            tool=request.get("tool"),
            project=request.get("project"),
            state="approved_unused",
        )
        _record_outcome("expired")


def consume_hold(run_id: str, *, retry: bool = False) -> Run:
    """Use a decided durable hold once. Refusals are ``HTTPException`` s.

    An approval runs one call; a refusal is reported once. A pending hold,
    one already used, and an approval past its lifetime are 409.
    """
    with _consume_lock:
        try:
            run = _approval_store.get(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"approval '{run_id}' not found") from exc
        request = run.request
        if request.get(_CONSUMED_AT):
            raise HTTPException(status_code=409, detail=f"approval '{run_id}' was already used")
        if run.status is RunStatus.AWAITING_APPROVAL:
            raise HTTPException(
                status_code=409, detail=f"approval '{run_id}' is still waiting for a decision"
            )
        if run.status not in _APPROVED and run.status is not RunStatus.REJECTED:
            raise HTTPException(
                status_code=409, detail=f"approval '{run_id}' is {run.status.value}"
            )
        approved = run.status in _APPROVED
        expires = request.get(_EXPIRES_AT)
        if approved and isinstance(expires, (int, float)) and _approval_store.now() >= expires:
            _note_unused_expiry(run, _approval_store.now())
            raise HTTPException(
                status_code=409,
                detail=f"approval '{run_id}' expired before the call was made again",
            )
        run = _approval_store.annotate(run_id, _CONSUMED_AT, _iso_now())
    if approved and request.get("route") != "policy":
        outcome = "approved_late_honoured" if retry else "approved"
        logger.info(
            f"tool_approval_{outcome}",
            run_id=run_id,
            tool=request.get("tool"),
            project=request.get("project"),
            approved_by=run.approved_by,
            arguments_hash=request.get("arguments_hash"),
        )
        _record_outcome(outcome)
    elif not approved:
        _record_outcome("rejected")
    return run


def approved_by_policy_in_window(project: str, tool: str, seconds: float) -> int:
    """How many ``tool`` calls the project's policy approved in the last ``seconds``."""
    since = _approval_store.now() - seconds
    return sum(
        1
        for r in _approval_store.list()
        if r.request.get("route") == "policy"
        and r.request.get("tool") == tool
        and r.request.get("project") == project
        and r.created_at >= since
    )


@router.get("/by-key/{approval_key}", response_model=RunResponse)
def get_live_hold(approval_key: str) -> RunResponse:
    """The live durable hold for a key: pending, or decided and not yet used (FORGE-780)."""
    run = find_live_hold(approval_key)
    if run is None:
        raise HTTPException(status_code=404, detail="no live approval for this call")
    return RunResponse.from_run(run)


class ConsumeHoldRequest(BaseModel):
    """Use a decided durable hold (FORGE-780)."""

    #: True when the call using it is a retry made after the original call
    #: had returned, so the approval arrived late and is honoured now.
    retry: bool = False


@router.post("/{run_id}/consume", response_model=RunResponse)
def consume_tool_approval(run_id: str, body: ConsumeHoldRequest) -> RunResponse:
    """Mark a decided durable hold used: one approval runs one call."""
    return RunResponse.from_run(consume_hold(run_id, retry=body.retry))


class PolicyApprovalRequest(BaseModel):
    """A held call the project's approval policy may already cover."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    caller: str = "untrusted"
    session_id: str | None = None
    project: str | None = None
    client: str | None = None
    #: FORGE-780: recorded on the approval so the audit names what ran.
    approval_key: str | None = Field(default=None, max_length=128)
    arguments_hash: str | None = Field(default=None, max_length=128)


class PolicyApprovalResponse(BaseModel):
    approved: bool
    #: The ledger entry the approval is recorded on, when approved.
    id: str | None = None
    approved_by: str | None = None
    #: The policy mode in force for the call's project (FORGE-780).
    mode: str | None = None
    #: Why it was or was not approved.
    reason: str | None = None
    #: The policy would have approved it, but the project's spend cap for
    #: this paid tool was reached; a person decides it.
    cap_held: bool = False


@router.post("/policy", response_model=PolicyApprovalResponse)
async def approve_by_policy(body: PolicyApprovalRequest) -> PolicyApprovalResponse:
    """Approve a held call the project's policy covers, or say it does not.

    A ``phase.claim`` on an open task of the project's own client-mode run
    under an ``auto`` policy is covered (see
    :mod:`api_gateway.approvals.claim_policy`), approved by
    ``policy:auto:<who set it>``. Any other held write is decided by
    :mod:`api_gateway.approvals.tool_write_policy` (FORGE-780), approved by
    ``policy:<mode>:<project>``. A covered call is written to the ledger like
    any other, on route ``policy``; anything else records nothing and the
    caller holds it for a person as before.
    """
    from api_gateway.approvals.claim_policy import (
        POLICY_APPROVABLE_TOOLS,
        claim_covered_by_policy,
    )
    from api_gateway.approvals.tool_write_policy import tool_write_covered_by_policy

    project = project_for_hold(body.project, body.arguments)
    mode: str | None = None
    if body.tool in POLICY_APPROVABLE_TOOLS:
        approver = await claim_covered_by_policy(body.tool, body.arguments)
        reason = "covered by the project's approval policy"
        if approver is None:
            return PolicyApprovalResponse(approved=False)
    else:
        verdict = await tool_write_covered_by_policy(
            body.tool, project, approved_in_window=approved_by_policy_in_window
        )
        mode = verdict.mode
        if not verdict.approved or verdict.approver is None:
            if verdict.cap_held:
                _record_outcome("cap_held")
            return PolicyApprovalResponse(
                approved=False, mode=mode, reason=verdict.reason, cap_held=verdict.cap_held
            )
        approver = verdict.approver
        reason = verdict.reason
    request: dict[str, Any] = {
        "tool": body.tool,
        "arguments": body.arguments,
        "caller": body.caller,
        "source": "mcp",
        "session_id": body.session_id,
        "project": project,
        "route": "policy",
        "client": body.client,
    }
    if body.approval_key:
        request[APPROVAL_KEY] = body.approval_key
        # The call runs now, on this approval; it is never a pass for a retry.
        request[_CONSUMED_AT] = _iso_now()
    if body.arguments_hash:
        request["arguments_hash"] = body.arguments_hash
    if mode is not None:
        request["policy_mode"] = mode
    run = _approval_store.create(request)
    _approval_store.start(run.id)
    _approval_store.request_approval(
        run.id,
        reason=reason,
        deadline=_approval_store.now() + DEFAULT_HOLD_TIMEOUT_SECONDS,
    )
    run = _approval_store.submit_approval(
        run.id, ApprovalDecision.APPROVE, approved_by=approver, approver_verified=False
    )
    run = _close_decided_hold(
        run.id,
        approved=True,
        approver=approver,
        verified=False,
        reason=reason,
    )
    logger.info("tool_approval_by_policy", run_id=run.id, tool=body.tool, approved_by=approver)
    if mode is not None:
        logger.info(
            "tool_approval_auto_approved",
            run_id=run.id,
            tool=body.tool,
            project=project,
            mode=mode,
            decided_by=approver,
            arguments_hash=body.arguments_hash,
        )
        _record_outcome("auto_approved")
    return PolicyApprovalResponse(
        approved=True, id=run.id, approved_by=approver, mode=mode, reason=reason
    )


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


#: The persisted decision log on a hold's request (FORGE-507); the ledger
#: stores ``request`` on every transition, so this outlives a restart, which
#: ``Run.approved_by`` does not (the ledger has no column for it).
_DECISIONS_KEY = "decisions"


def _close_decided_hold(
    run_id: str, *, approved: bool, approver: str | None, verified: bool, reason: str = ""
) -> Run:
    """Log the decision on the hold and, when approved, close it (FORGE-639).

    An approved hold used to stay RUNNING, because the decision is the whole
    of its job and nothing ever completed it. The heartbeat reaper then
    cancelled it as abandoned an hour later, so every approval read as
    "canceled", and with the approver kept only on ``Run.approved_by`` (not
    persisted) a restart erased who gave it. The log entry is written first,
    so the transition that persists the run carries it.
    """
    run = _approval_store.get(run_id)
    log = run.request.setdefault(_DECISIONS_KEY, [])
    if not log:
        log.append(
            {
                "decision": "approve" if approved else "reject",
                "reason": reason,
                "approver": approver,
                "approver_verified": verified,
                "surface": "unknown",
                "on_behalf_of": None,
                "agent": None,
                "decided_at": datetime.fromtimestamp(time.time(), UTC).isoformat(),
            }
        )
    if approved and run.status is RunStatus.RUNNING:
        run = _approval_store.complete(
            run_id, result={"approved_by": approver, "approver_verified": verified}
        )
    logger.info("tool_approval_hold_closed", run_id=run_id, status=run.status.value)
    return run


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
    run = _close_decided_hold(
        run_id,
        approved=approved,
        approver=run.approved_by,
        verified=run.approver_verified,
        reason="answered in the client's own approval prompt",
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
    # FORGE-780: an inline (elicitation) hold is decidable here too. FORGE-473
    # refused it, so a hold whose client prompt had gone (the client timed the
    # call out) could be answered nowhere. The first decision wins: the
    # client's prompt, read back after this one, finds the hold decided and
    # acts on what is on record.
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
    run = _close_decided_hold(
        run_id,
        approved=decision == ApprovalDecision.APPROVE.value,
        approver=approver.label,
        verified=approver.verified,
    )
    logger.info(
        "tool_approval_submitted",
        run_id=run_id,
        decision=decision,
        approved_by=approver.actor_id,
        approver_verified=approver.verified,
    )
    return run
