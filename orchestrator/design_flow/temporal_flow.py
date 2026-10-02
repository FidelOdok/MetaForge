"""Design flows as a Temporal workflow (FORGE-401).

Runs used to be an ``asyncio.create_task`` in the gateway process against an
``InMemoryRunStore``. A gateway restart lost every in-flight run — including
runs parked at a gate, which is the worst case, because those are precisely
the runs a human was about to answer. ADR-001 names Temporal as the engine;
``temporal_worker.py`` and a per-product ``HardwareDesignWorkflow`` existed,
and the launcher never called either.

**One workflow, not one per flow.** ``DesignFlowWorkflow`` is an interpreter:
it takes the approved, frozen flow as *input data* and walks it. No code is
generated per template, so a new flow is a data change and every run —
whatever its shape — replays against the same workflow definition.

**Gates are signals, not polling.** ``wait_condition`` suspends the workflow
with no timer of its own; Temporal persists that wait. The gate survives a
worker restart, a gateway restart and a redeploy, because the waiting lives in
the server's history rather than in a coroutine on somebody's heap.

**A gate that times out is a rejection.** Not "carry on", which would promote
work nobody looked at, and not "wait forever", which leaves a run that reads as
live to anyone scanning the list. The timer is durable for the same reason the
wait is.

Determinism rules this file obeys, because breaking them produces a workflow
that passes every test and then fails on replay in production:

* no clock but ``workflow.now()``, no ``random``, no I/O
* everything that touches the twin, the LLM or the run store is an activity
* the flow arrives as data, never a module lookup, so editing ``spec.py``
  cannot change what an in-flight run is doing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from orchestrator.design_flow.frozen import FrozenFlow, FrozenPhase
    from orchestrator.design_flow.grounding import UNGROUNDED_STATUS, phase_status

__all__ = [
    "DEFAULT_GATE_TIMEOUT",
    "DesignFlowInput",
    "DesignFlowWorkflow",
    "GateAnswer",
    "PhaseRequest",
    "PhaseResult",
    "TASK_QUEUE",
]

TASK_QUEUE = "metaforge-design-flows"

#: How long a gate waits before it is treated as refused. Long, because the
#: reviewer is a person who may be asleep; finite, because a run that waits
#: forever is indistinguishable from one that is progressing.
DEFAULT_GATE_TIMEOUT = timedelta(hours=24)

#: A phase runs an agent loop — minutes to hours, not seconds. The heartbeat
#: is what lets Temporal tell a slow phase from a dead worker; without one, a
#: crashed worker holds the activity until start_to_close expires.
_PHASE_TIMEOUT = timedelta(hours=6)
_PHASE_HEARTBEAT = timedelta(minutes=2)
_CHECK_TIMEOUT = timedelta(minutes=5)


def result_ungrounded(entry: dict[str, Any]) -> bool:
    return entry.get("status") == UNGROUNDED_STATUS


@dataclass
class PhaseRequest:
    run_id: str
    goal: str
    phase: FrozenPhase
    project_id: str | None = None
    session_id: str | None = None
    #: Which template this phase belongs to. The worker routes deterministic
    #: handlers per flow (``hardware_v1`` has eight of them), so a phase that
    #: does not know its flow silently falls back to the native brain -- the
    #: same phase behaving differently on the two engines, which is the one
    #: thing this migration must not introduce.
    flow_id: str = ""
    #: Summaries of phases already done, so the agent has the thread so far.
    prior: list[str] = field(default_factory=list)
    #: FORGE-491: the frozen flow's context block, handed to the phase brain.
    flow_context: str = ""


@dataclass
class PhaseResult:
    summary: str
    artifacts: list[str] = field(default_factory=list)
    status: str = "completed"


@dataclass
class GateCheck:
    """What the gate found, gathered before a human is asked."""

    ready: bool = True
    checked: bool = False
    missing: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    constraints_passed: bool = True
    constraints_checked: bool = False
    violations: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class GateAnswer:
    """A human's decision, signalled in from the approval ledger."""

    approved: bool
    #: Who decided. FORGE-393: this comes from the approval record, never from
    #: whatever asked for the gate.
    decided_by: str = ""
    comment: str = ""


@dataclass
class ChangeRequest:
    """A mid-run flow change, accepted only at a gate boundary."""

    flow: FrozenFlow
    requested_by: str = ""
    rationale: str = ""


@dataclass
class DesignFlowInput:
    run_id: str
    goal: str
    flow: FrozenFlow
    project_id: str | None = None
    session_id: str | None = None
    gate_timeout_seconds: float = DEFAULT_GATE_TIMEOUT.total_seconds()
    #: Phases already completed by an earlier incarnation, carried across a
    #: continue-as-new so a change request does not re-run finished work.
    completed: list[dict[str, Any]] = field(default_factory=list)


@workflow.defn(name="DesignFlow")
class DesignFlowWorkflow:
    """Walks an approved flow: phases, gates, terminal state.

    **A note on worker versioning, for whoever deploys this.** A design run
    can sit at a gate for a day. If the deployment uses Temporal's worker
    versioning, this workflow wants ``PINNED`` — a run should finish on the
    build it started on. Auto-upgrading means the second half of a run
    executes code the first half never saw, and the human who approved the
    gate agreed to one flow while a different one finished it. Moving a run
    onto new logic is a deliberate act (a change request, which
    continue-as-news with a new frozen flow), not a side effect of a deploy.

    That behaviour is set through the worker's deployment options, not here:
    declaring ``versioning_behavior`` on the definition is rejected by a
    server that has no deployment options configured, which is every
    deployment MetaForge currently ships.
    """

    def __init__(self) -> None:
        self._phase_index = 0
        self._current_phase: str | None = None
        self._status = "queued"
        self._gate_open: str | None = None
        #: Why the gate is open (the gate check's own summary), so a gateway
        #: that restarted while the run waited can show it (FORGE-485).
        self._gate_reason: str = ""
        self._answer: GateAnswer | None = None
        self._change: ChangeRequest | None = None
        self._completed: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._error: str | None = None

    # ── signals ──────────────────────────────────────────────────────────

    @workflow.signal
    def submit_gate_decision(self, answer: GateAnswer) -> None:
        """Answer the gate this run is parked at.

        Ignored when no gate is open. A decision for a gate that already
        closed is not an error worth failing a run over — the likeliest cause
        is two reviewers clicking at once — but it must not be applied to the
        *next* gate, which is what storing it unconditionally would do.
        """
        if self._gate_open is None:
            self._record("gate_decision_ignored", detail="no gate open")
            return
        self._answer = answer

    @workflow.signal
    def request_change(self, change: ChangeRequest) -> None:
        """Queue a flow change. Applied at the next gate boundary, never mid-phase."""
        self._change = change
        self._record("change_requested", detail=change.rationale)

    # ── queries ──────────────────────────────────────────────────────────

    @workflow.query
    def state(self) -> dict[str, Any]:
        """Everything the live run view needs, without touching a database."""
        return {
            "status": self._status,
            "phase_index": self._phase_index,
            "current_phase": self._current_phase,
            "awaiting_gate": self._gate_open,
            "gate_reason": self._gate_reason if self._gate_open else "",
            "completed": list(self._completed),
            "error": self._error,
        }

    @workflow.query
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    # ── the interpreter ──────────────────────────────────────────────────

    @workflow.run
    async def run(self, inp: DesignFlowInput) -> dict[str, Any]:
        # The flow a human approved is the flow that runs. Checked here rather
        # than at launch so it also holds across a continue-as-new.
        inp.flow.verify()

        self._completed = list(inp.completed)
        self._phase_index = len(self._completed)
        self._status = "running"
        self._record("run_started", detail=inp.flow.template_id)

        phases = inp.flow.phases
        while self._phase_index < len(phases):
            phase = phases[self._phase_index]
            self._current_phase = phase.id
            self._record("phase_started", phase=phase.id)

            try:
                result: PhaseResult = await workflow.execute_activity(
                    "run_phase",
                    PhaseRequest(
                        run_id=inp.run_id,
                        goal=inp.goal,
                        phase=phase,
                        project_id=inp.project_id,
                        session_id=inp.session_id,
                        flow_id=inp.flow.template_id,
                        prior=[c["summary"] for c in self._completed],
                        flow_context=inp.flow.context,
                    ),
                    start_to_close_timeout=_PHASE_TIMEOUT,
                    heartbeat_timeout=_PHASE_HEARTBEAT,
                    # A configuration error (no key, unusable model) is marked
                    # non-retryable by the activity (FORGE-475); anything else
                    # gets three attempts.
                    retry_policy=RetryPolicy(maximum_attempts=3),
                    # Without result_type the payload arrives as a bare dict
                    # and every attribute access below fails at run time --
                    # inside a workflow, where the traceback surfaces as a
                    # stuck run.
                    result_type=PhaseResult,
                )
            except ActivityError as exc:
                # The phase could not run. End the run as failed with the
                # reason rather than failing the workflow with an opaque
                # "Activity task failed" nobody can read from run status.
                cause = exc.cause
                reason = str(cause) if cause is not None else str(exc)
                return self._fail(f"Phase '{phase.id}' failed: {reason}")
            # An ungrounded reply is never "completed", whatever the worker said.
            result.status = phase_status(result.summary, result.status)
            entry = {
                "phase": phase.id,
                "summary": result.summary,
                "artifacts": result.artifacts,
                "status": result.status,
            }
            self._completed.append(entry)
            self._record("phase_finished", phase=phase.id, detail=result.status)

            gate = phase.gate
            if gate is not None and not gate.auto_approve:
                verdict = await self._run_gate(inp, phase, entry)
                if verdict is not None:
                    return verdict

                if self._change is not None:
                    # A gate boundary is the only safe place to swap the flow:
                    # no phase is in flight, so nothing half-done is orphaned.
                    return await self._apply_change(inp)

            self._phase_index += 1

        self._status = "completed"
        self._record("run_completed")
        return {
            "status": "completed",
            "flow": inp.flow.template_id,
            "version": inp.flow.version,
            "content_hash": inp.flow.content_hash,
            "phases": self._completed,
        }

    async def _run_gate(
        self, inp: DesignFlowInput, phase: FrozenPhase, entry: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Hold at ``phase``'s gate. Returns a terminal result, or ``None`` to go on."""
        gate = phase.gate
        assert gate is not None  # only called when there is one

        check: GateCheck = await workflow.execute_activity(
            "evaluate_gate",
            {
                "run_id": inp.run_id,
                "project_id": inp.project_id,
                "phase": phase,
                "gate": gate,
            },
            start_to_close_timeout=_CHECK_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
            result_type=GateCheck,
        )

        # List the required deliverables the twin holds as the phase's artifacts,
        # so the run shows what the phase produced (FORGE-484).
        if check.checked:
            found = [d for d in phase.required_deliverables if d in check.present]
            entry["artifacts"] = sorted({*entry["artifacts"], *found})

        # A gate whose own preconditions failed never reaches a human. Asking
        # somebody to approve work the system already knows is incomplete
        # trains them to click through.
        if phase.enforce_deliverables and check.checked and not check.ready:
            entry["status"] = "failed"
            self._record("phase_failed", phase=phase.id, detail=f"missing {check.missing}")
            return self._fail(
                f"Gate '{gate.name}' not ready — phase '{phase.id}' did not record "
                f"required deliverables {check.missing} (present: {check.present or 'none'})."
            )
        if phase.enforce_deliverables and result_ungrounded(entry):
            entry["status"] = "failed"
            self._record("phase_failed", phase=phase.id, detail="ungrounded reply")
            return self._fail(
                f"Gate '{gate.name}' not ready: phase '{phase.id}' reply was flagged "
                "ungrounded (no tool calls were made), so its claimed work is unverified."
            )
        if gate.enforce_constraints and check.constraints_checked and not check.constraints_passed:
            return self._fail(
                f"Gate '{gate.name}' not ready — {len(check.violations)} constraint "
                f"violation(s): {'; '.join(check.violations[:10])}"
            )

        self._gate_open = gate.name
        self._gate_reason = check.reason
        self._status = "awaiting_approval"
        self._answer = None
        self._record("gate_opened", phase=phase.id, detail=check.reason)

        await workflow.execute_activity(
            "announce_gate",
            {"run_id": inp.run_id, "gate": gate.name, "reason": check.reason},
            start_to_close_timeout=_CHECK_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # `wait_condition` raises rather than returning when the timeout
        # expires. Letting that escape would fail the workflow, which reads as
        # a crashed run rather than a gate nobody answered -- the two need
        # different words to whoever is looking at the run list.
        try:
            await workflow.wait_condition(
                lambda: self._answer is not None or self._change is not None,
                timeout=timedelta(seconds=inp.gate_timeout_seconds),
            )
        except TimeoutError:
            pass

        if self._answer is None and self._change is None:
            self._gate_open = None
            return self._reject(
                f"Gate '{gate.name}' was not answered within the approval window. "
                "An unanswered gate is a refusal: nobody looked at this work."
            )

        self._gate_open = None
        if self._answer is not None and not self._answer.approved:
            return self._reject(
                f"Gate '{gate.name}' rejected by {self._answer.decided_by or 'a reviewer'}"
                + (f": {self._answer.comment}" if self._answer.comment else "")
            )

        if self._answer is not None:
            self._status = "running"
            self._record(
                "gate_approved", phase=phase.id, detail=self._answer.decided_by or "approved"
            )
        return None

    async def _apply_change(self, inp: DesignFlowInput) -> dict[str, Any]:
        """Restart as a new incarnation carrying the new flow.

        ``continue_as_new`` rather than mutating in place: the history of a run
        whose flow changed halfway is otherwise a history that no longer
        matches any single flow, and replaying it becomes impossible.
        """
        change = self._change
        assert change is not None
        change.flow.verify()
        self._record("change_applied", detail=change.flow.content_hash[:12])
        workflow.continue_as_new(
            DesignFlowInput(
                run_id=inp.run_id,
                goal=inp.goal,
                flow=change.flow,
                project_id=inp.project_id,
                session_id=inp.session_id,
                gate_timeout_seconds=inp.gate_timeout_seconds,
                completed=list(self._completed),
            )
        )
        raise AssertionError("unreachable: continue_as_new does not return")

    # ── bookkeeping ──────────────────────────────────────────────────────

    def _fail(self, message: str) -> dict[str, Any]:
        self._status = "failed"
        self._error = message
        self._record("run_failed", detail=message)
        return {"status": "failed", "error": message, "phases": self._completed}

    def _reject(self, message: str) -> dict[str, Any]:
        self._status = "rejected"
        self._record("run_rejected", detail=message)
        return {"status": "rejected", "error": message, "phases": self._completed}

    def _record(self, event: str, *, phase: str | None = None, detail: str = "") -> None:
        self._events.append(
            {
                "event": event,
                "phase": phase,
                "detail": detail,
                # workflow.now() is the replay-safe clock; time.time() here
                # would make every replay diverge.
                "at": workflow.now().isoformat(),
            }
        )
