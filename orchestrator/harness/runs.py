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
                                  ├── fail ──────▶ failed         └─ reject ──▶ rejected
                                  └── cancel ────▶ canceled

``completed``, ``failed``, ``rejected``, ``canceled`` are terminal. Illegal
transitions raise :class:`InvalidTransition`, so the gateway can return a clean
409 instead of corrupting run state.
"""

from __future__ import annotations

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


TERMINAL: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.REJECTED, RunStatus.CANCELED}
)

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
        {RunStatus.RUNNING, RunStatus.REJECTED, RunStatus.CANCELED}
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

    def request_approval(self, run_id: str, *, reason: str | None = None) -> Run:
        run = self._transition(run_id, RunStatus.AWAITING_APPROVAL)
        run.approval_reason = reason
        return run

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

    def cancel(self, run_id: str) -> Run:
        return self._transition(run_id, RunStatus.CANCELED)


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
    """
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        status = store.get(run_id).status
        if status is RunStatus.RUNNING:
            return ApprovalWait.APPROVED
        if status is RunStatus.REJECTED:
            return ApprovalWait.REJECTED
        await sleep(poll_interval)

    # Timed out. Deny by default -- but a decision landing in the exact
    # instant between the last poll and here is still honoured rather than
    # clobbered by the race with submit_approval.
    try:
        store.submit_approval(run_id, ApprovalDecision.REJECT)
    except (InvalidTransition, RunNotFoundError):
        pass
    if store.get(run_id).status is RunStatus.RUNNING:
        return ApprovalWait.APPROVED
    return ApprovalWait.TIMED_OUT
