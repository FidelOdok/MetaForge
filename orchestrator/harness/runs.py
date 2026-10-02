"""Run lifecycle + approval state machine (MET-547, Phase 1).

The OpenAI-compatible gateway (``POST /v1/runs`` + ``/runs/{id}/approval``)
needs a run abstraction: a single harness execution that can pause for human
approval before a consequential step and resume or abort on the decision. This
module is the transport-free core of that -- the FastAPI surface + SSE stream
(next slice) wrap it.

A run moves through an explicit state machine::

    queued ── start ──▶ running ──┬── request_approval ──▶ awaiting_approval
                                  │                              │
                                  ├── complete ──▶ completed      ├─ approve ─▶ running
                                  ├── fail ──────▶ failed         ├─ reject ──▶ rejected
                                  └── cancel ────▶ canceled       ├─ time_out ▶ timed_out
                                                                  └─ cancel ──▶ canceled

``completed``, ``failed``, ``rejected``, ``canceled``, ``timed_out`` are
terminal. ``timed_out`` and ``canceled`` out of ``awaiting_approval`` mean the
same thing to a reviewer: nobody is waiting for this answer any more
(FORGE-466). Illegal
transitions raise :class:`InvalidTransition`, so the gateway can return a clean
409 instead of corrupting run state.
"""

from __future__ import annotations

import asyncio
import builtins
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELED = "canceled"
    #: The wait for a human ended with no answer (FORGE-466). Distinct from
    #: ``rejected``: nobody said no, nobody said anything.
    TIMED_OUT = "timed_out"


TERMINAL: frozenset[RunStatus] = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.REJECTED,
        RunStatus.CANCELED,
        RunStatus.TIMED_OUT,
    }
)

#: An approval that ended without anyone answering it. Approving one of these
#: would record an approval for a call that is no longer waiting.
UNANSWERED: frozenset[RunStatus] = frozenset({RunStatus.TIMED_OUT, RunStatus.CANCELED})

# Allowed status transitions (source -> set of legal destinations).
_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.REJECTED, RunStatus.CANCELED, RunStatus.TIMED_OUT}
    ),
}


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RunNotFoundError(KeyError):
    """No run with the given id."""


class InvalidTransition(Exception):
    """An illegal run status transition was attempted."""

    def __init__(self, run_id: str, current: RunStatus, target: RunStatus) -> None:
        self.run_id = run_id
        self.current = current
        self.target = target
        super().__init__(f"run '{run_id}': cannot transition {current.value} -> {target.value}")


@dataclass
class Run:
    """One harness execution and its lifecycle state."""

    id: str
    status: RunStatus
    request: dict[str, Any]
    created_at: float
    updated_at: float
    error: str | None = None
    approval_reason: str | None = None
    #: Wall-clock time after which nobody is waiting for this approval
    #: (FORGE-466). Set when the hold is created; ``expire_overdue`` marks a
    #: run past it ``timed_out`` so a waiter that died without saying so
    #: cannot leave it pending forever. ``None`` means no deadline.
    approval_deadline: float | None = None
    #: Who answered the approval, and whether that identity was verified
    #: (FORGE-393). Set by ``submit_approval``; never by the code that asked
    #: for the approval, and never by a tool argument.
    approved_by: str | None = None
    approver_verified: bool = False
    result: dict[str, Any] | None = None
    history: list[RunStatus] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


class InMemoryRunStore:
    """In-memory run store with a validated status state machine.

    The clock is injected so tests get deterministic timestamps; production
    passes the default wall clock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        on_transition: Callable[[Run], None] | None = None,
    ) -> None:
        self._clock = clock
        # Optional synchronous observer fired on every status change (create +
        # transition). Kept sync so this stays async-framework-free; the
        # gateway wires it to an SSE stream manager.
        self._on_transition = on_transition
        self._runs: dict[str, Run] = {}

    def set_on_transition(self, callback: Callable[[Run], None] | None) -> None:
        """Set (or clear) the status-change observer after construction."""
        self._on_transition = callback

    def now(self) -> float:
        """This store's clock, so a deadline is measured on the same one."""
        return self._clock()

    def _notify(self, run: Run) -> None:
        if self._on_transition is not None:
            self._on_transition(run)

    def create(self, request: dict[str, Any], *, run_id: str | None = None) -> Run:
        rid = run_id or f"run_{uuid.uuid4().hex[:16]}"
        if rid in self._runs:
            raise ValueError(f"run '{rid}' already exists")
        now = self._clock()
        run = Run(
            id=rid,
            status=RunStatus.QUEUED,
            request=dict(request),
            created_at=now,
            updated_at=now,
            history=[RunStatus.QUEUED],
        )
        self._runs[rid] = run
        logger.info("run_created", run_id=rid)
        self._notify(run)
        return run

    def restore(self, run: Run) -> None:
        """Reinsert a ``Run`` recovered from durable storage, bypassing
        transition validation (production-harness audit follow-up — used
        only at startup to rehydrate in-flight runs after a process restart,
        see ``orchestrator.harness.ledger.SqliteRunLedger``)."""
        self._runs[run.id] = run
        logger.info("run_restored", run_id=run.id, status=run.status.value)

    def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(run_id) from exc

    def list(self) -> list[Run]:
        return list(self._runs.values())

    def _transition(self, run_id: str, target: RunStatus) -> Run:
        run = self.get(run_id)
        allowed = _TRANSITIONS.get(run.status, frozenset())
        if target not in allowed:
            raise InvalidTransition(run_id, run.status, target)
        run.status = target
        run.updated_at = self._clock()
        run.history.append(target)
        logger.info("run_transition", run_id=run_id, status=target.value)
        self._notify(run)
        return run

    def start(self, run_id: str) -> Run:
        return self._transition(run_id, RunStatus.RUNNING)

    def request_approval(
        self,
        run_id: str,
        *,
        reason: str | None = None,
        deadline: float | None = None,
    ) -> Run:
        run = self._transition(run_id, RunStatus.AWAITING_APPROVAL)
        run.approval_reason = reason
        run.approval_deadline = deadline
        return run

    def time_out(self, run_id: str, *, reason: str = "timed out waiting for approval") -> Run:
        """Close a pending approval nobody answered (FORGE-466)."""
        run = self._transition(run_id, RunStatus.TIMED_OUT)
        run.error = reason
        return run

    def expire_overdue(self, *, now: float | None = None) -> builtins.list[Run]:
        """Mark every pending approval past its deadline ``timed_out``.

        The waiter is meant to resolve its own hold when it stops waiting.
        This is the backstop for when it cannot: a process that crashed, or
        lost the network at the wrong moment, says nothing on the way out.
        """
        current = self._clock() if now is None else now
        expired: builtins.list[Run] = []
        for run in list(self._runs.values()):
            if (
                run.status is RunStatus.AWAITING_APPROVAL
                and run.approval_deadline is not None
                and run.approval_deadline <= current
            ):
                expired.append(
                    self.time_out(run.id, reason="deadline passed with nobody waiting (expired)")
                )
        return expired

    def submit_approval(
        self,
        run_id: str,
        decision: ApprovalDecision,
        *,
        approved_by: str | None = None,
        approver_verified: bool = False,
    ) -> Run:
        """Record a human's answer, and who gave it.

        ``approved_by`` comes from the route that observed the decision — the
        verified principal where there is one, otherwise a label for the
        surface the click arrived on. It is recorded on both outcomes: knowing
        who refused a promotion matters as much as knowing who allowed one.
        """
        run = self.get(run_id)
        if run.status is not RunStatus.AWAITING_APPROVAL:
            raise InvalidTransition(run_id, run.status, RunStatus.RUNNING)
        target = RunStatus.RUNNING if decision is ApprovalDecision.APPROVE else RunStatus.REJECTED
        run = self._transition(run_id, target)
        run.approval_reason = None
        run.approved_by = approved_by
        run.approver_verified = approver_verified
        return run

    def reconcile(
        self,
        run_id: str,
        status: RunStatus,
        *,
        approval_reason: str | None = None,
        error: str | None = None,
    ) -> Run:
        """Align a run's record with what its engine reports (FORGE-485).

        Not a transition. The state machine describes what *this process*
        may do next; this records what already happened elsewhere, in a
        workflow that kept running while the gateway was down. A restored
        record is ``queued`` because that is all the ledger remembered, and
        ``queued -> awaiting_approval`` is illegal, which is precisely why a
        gate could not be answered. The engine is the authority, so its
        answer is applied without validation.

        A run already in a terminal state is never moved: the record that
        ended it is the more specific one. Nothing is notified when nothing
        changed, so polling this does not flood the SSE stream or the ledger.
        """
        run = self.get(run_id)
        if run.is_terminal:
            return run
        if (
            run.status is status
            and run.approval_reason == approval_reason
            and (error is None or run.error == error)
        ):
            return run
        run.status = status
        run.updated_at = self._clock()
        if run.history[-1:] != [status]:
            run.history.append(status)
        run.approval_reason = approval_reason if status is RunStatus.AWAITING_APPROVAL else None
        if error is not None:
            run.error = error
        logger.info("run_reconciled", run_id=run_id, status=status.value)
        self._notify(run)
        return run

    def delete(self, run_id: str) -> None:
        """Remove a run outright.

        Only for a run that was created and then could not be started at all
        (FORGE-401: the workflow engine was unreachable). A record left in
        ``queued`` that nothing will ever pick up reads as "starting" to
        everyone looking at the list, and nothing ever notices that it does
        not move. Deleting is honest; a phantom run is not.

        Not a general-purpose delete: a run that has begun belongs in the
        ledger whatever happened to it.
        """
        self._runs.pop(run_id, None)

    def complete(self, run_id: str, result: dict[str, Any] | None = None) -> Run:
        run = self._transition(run_id, RunStatus.COMPLETED)
        run.result = result
        return run

    def fail(self, run_id: str, error: str) -> Run:
        run = self._transition(run_id, RunStatus.FAILED)
        run.error = error
        return run

    def cancel(self, run_id: str, *, reason: str | None = None) -> Run:
        run = self._transition(run_id, RunStatus.CANCELED)
        if reason is not None:
            run.error = reason
        return run


# ---------------------------------------------------------------------------
# Waiting on a decision
# ---------------------------------------------------------------------------


class ApprovalWait(StrEnum):
    """How a wait for a human ended."""

    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


async def await_approval_decision(
    store: InMemoryRunStore,
    run_id: str,
    *,
    timeout_seconds: float,
    poll_interval: float,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float] = time.monotonic,
) -> ApprovalWait:
    """Block until a paused run is approved, rejected, or the window closes.

    Extracted from ``HarnessRuntime._await_approval`` (FORGE-359) so the MCP
    path can hold a call the same way the chat path does. A second copy of
    this loop would be a second copy of the race fix below, and the copy that
    loses it is the one that drops an approval a human actually gave.

    Fails closed on every path that is not an explicit approval.

    However the wait ends without an answer, the hold is closed on the way
    out (FORGE-466): ``timed_out`` when the window closes, ``canceled`` when
    the waiter itself is cancelled. Left ``awaiting_approval``, it is a
    button on the Approvals page for a call nobody is waiting for.
    """
    deadline = monotonic() + timeout_seconds
    try:
        while monotonic() < deadline:
            status = store.get(run_id).status
            if status is RunStatus.RUNNING:
                return ApprovalWait.APPROVED
            if status is RunStatus.REJECTED:
                return ApprovalWait.REJECTED
            if status in UNANSWERED:
                return ApprovalWait.TIMED_OUT
            await sleep(poll_interval)
    except asyncio.CancelledError:
        _close_unanswered(store, run_id, RunStatus.CANCELED, "waiter cancelled")
        raise

    # Timed out. Deny by default -- but a decision landing in the exact
    # instant between the last poll and here is still honoured rather than
    # clobbered by the race with submit_approval.
    _close_unanswered(store, run_id, RunStatus.TIMED_OUT, "timed out waiting for approval")
    try:
        if store.get(run_id).status is RunStatus.RUNNING:
            return ApprovalWait.APPROVED
    except RunNotFoundError:
        pass
    return ApprovalWait.TIMED_OUT


def _close_unanswered(store: InMemoryRunStore, run_id: str, target: RunStatus, reason: str) -> None:
    """Best-effort: a hold already decided or gone is left as it is."""
    try:
        if store.get(run_id).status is not RunStatus.AWAITING_APPROVAL:
            return
        if target is RunStatus.TIMED_OUT:
            store.time_out(run_id, reason=reason)
        else:
            store.cancel(run_id, reason=reason)
    except (InvalidTransition, RunNotFoundError):
        return
    logger.info("approval_hold_closed", run_id=run_id, status=target.value, reason=reason)
