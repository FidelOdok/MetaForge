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
from collections.abc import Callable
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

    def submit_approval(self, run_id: str, decision: ApprovalDecision) -> Run:
        run = self.get(run_id)
        if run.status is not RunStatus.AWAITING_APPROVAL:
            raise InvalidTransition(run_id, run.status, RunStatus.RUNNING)
        target = RunStatus.RUNNING if decision is ApprovalDecision.APPROVE else RunStatus.REJECTED
        run = self._transition(run_id, target)
        run.approval_reason = None
        return run

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
