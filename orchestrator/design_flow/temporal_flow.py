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

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from orchestrator.design_flow.failures import classify_failure
    from orchestrator.design_flow.frozen import FrozenFlow, FrozenPhase
    from orchestrator.design_flow.graph import (
        FlowGraph,
        build_graph,
        evaluate_condition,
        is_linear,
        rework_candidates,
    )
    from orchestrator.design_flow.grounding import UNGROUNDED_STATUS, phase_status
    from orchestrator.design_flow.retry import DEFAULT_MAX_PHASE_RETRIES, build_retry_feedback
    from orchestrator.design_flow.rework import (
        DEFAULT_MAX_REWORK_CYCLES,
        DEFAULT_STALL_STOP,
        build_rework_feedback,
        findings_streak,
        rework_target_error,
        stall_note,
    )
    from orchestrator.design_flow.rework_context import RevisionNote, notes_from_dicts

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

#: ``workflow.patched`` id for FORGE-495. A gate that is not ready used to fail
#: the run; it now parks for a decision. Runs whose history already holds the
#: old failure replay it unchanged.
RETRY_PATCH_ID = "forge-495-gate-retry"

#: ``workflow.patched`` id for FORGE-500. A rework decision at a gate jumps the
#: run back to an earlier phase. It is checked only once a rework answer has
#: arrived, which is after any history recorded by older code, so a run parked
#: at a gate (its last one included) before this change replays unchanged and
#: then accepts a rework as new history.
REWORK_PATCH_ID = "forge-500-rework"

#: ``workflow.patched`` id for FORGE-530. A retry or rework now runs one
#: ``collect_revision_notes`` activity (the drafts the gate just closed) before
#: building its feedback. Histories recorded before it replay without the
#: activity and get the feedback they had.
REVISION_NOTES_PATCH_ID = "forge-530-revision-notes"

#: ``workflow.patched`` id for FORGE-539. A flow whose phases declare
#: dependencies or conditions runs as a graph: independent phases together,
#: false conditions skipped, rework re-running only what depends on the
#: target. Checked only for such flows, so a straight-line flow records no
#: marker and replays exactly as before.
GRAPH_PATCH_ID = "forge-539-graph"

#: ``workflow.patched`` id for FORGE-539's failure taxonomy: a phase that
#: could not run ends the run with its failure class and the response that
#: class calls for. Histories that already hold a phase failure replay with
#: the result they had.
FAILURE_CLASS_PATCH_ID = "forge-539-failure-class"

#: ``workflow.patched`` id for FORGE-573. A not-ready gate whose findings
#: repeat the previous verdict is marked stalled, and the run stops when they
#: repeat ``DEFAULT_STALL_STOP`` times. Checked only at a not-ready gate, so
#: histories recorded before it replay with the decisions they had.
STALL_PATCH_ID = "forge-573-stall"

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
    #: FORGE-495: on a retry, the gate's findings and the reviewer's reason,
    #: which the phase brain reads first. Empty on a first attempt.
    retry_feedback: str = ""
    #: 1 for the first attempt, 2 for the first retry, and so on.
    attempt: int = 1
    #: FORGE-581: ``"server"`` runs a phase brain; ``"client"`` posts the
    #: phase as a task for the connected client and waits for its submission.
    #: Payload only: the activity is the same, so no replay patch is needed.
    intelligence: str = "server"


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
    #: FORGE-495: re-run the phase instead of ending the run. ``approved`` is
    #: False for a retry; ``comment`` is the reviewer's reason.
    retry: bool = False
    #: FORGE-500: send the run back to this earlier phase (``comment`` is the
    #: reviewer's reason). ``approved`` is False for a rework.
    rework_to: str = ""


@dataclass
class ChangeRequest:
    """A mid-run flow change, accepted only at a gate boundary."""

    flow: FrozenFlow
    requested_by: str = ""
    rationale: str = ""
    #: FORGE-539: the phases an approved patch must re-run. Their results
    #: (and only theirs) are dropped; every other completed phase keeps its
    #: result and approval. Empty for a plain flow swap, which keeps all.
    rerun: list[str] = field(default_factory=list)


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
    #: FORGE-495: how many times one phase may be re-run from its gate.
    max_phase_retries: int = DEFAULT_MAX_PHASE_RETRIES
    #: FORGE-500: how many times the run may be sent back to an earlier phase,
    #: and how many it already has (carried across a continue-as-new).
    max_rework_cycles: int = DEFAULT_MAX_REWORK_CYCLES
    rework_cycles: int = 0
    #: FORGE-539: phases a graph run skipped, carried across a continue-as-new.
    skipped: list[str] = field(default_factory=list)
    #: FORGE-581: who does the phase work, fixed for the life of the run.
    intelligence: str = "server"


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
        #: FORGE-495: attempt number of the phase in flight, and the cap.
        self._attempt = 1
        self._max_retries = DEFAULT_MAX_PHASE_RETRIES
        #: False while parked at a gate that is not ready; approve is refused.
        self._gate_ready = True
        self._gate_findings: list[str] = []
        #: FORGE-573: each phase's not-ready findings across retries and
        #: reworks, and whether the open gate repeats the last verdict.
        self._gate_history: dict[str, list[tuple[str, ...]]] = {}
        self._stalled = False
        #: Set by a retry decision; consumed by the phase loop.
        self._retry_reason: str | None = None
        #: FORGE-500: rework cycles used / allowed, and the pending jump set by a
        #: rework decision (target phase id + the feedback for its brain).
        self._rework_cycles = 0
        self._max_rework_cycles = DEFAULT_MAX_REWORK_CYCLES
        self._rework_to: str | None = None
        self._rework_feedback = ""
        #: FORGE-539: "graph" for a flow with dependencies or conditions;
        #: the phases running right now, and the ones skipped.
        self._mode = "linear"
        self._running: list[str] = []
        self._skipped: list[str] = []

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
            "attempt": self._attempt,
            "max_retries": self._max_retries,
            "retries_left": max(self._max_retries - (self._attempt - 1), 0),
            "gate_ready": self._gate_ready,
            "gate_findings": list(self._gate_findings) if self._gate_open else [],
            "stalled": self._stalled and bool(self._gate_open),
            "rework_cycles": self._rework_cycles,
            "max_rework_cycles": self._max_rework_cycles,
            "reworks_left": max(self._max_rework_cycles - self._rework_cycles, 0),
            "mode": self._mode,
            "running": list(self._running),
            "skipped": list(self._skipped),
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

        self._max_retries = inp.max_phase_retries
        self._max_rework_cycles = inp.max_rework_cycles
        self._rework_cycles = inp.rework_cycles
        phases = inp.flow.phases
        graph = build_graph(phases)
        if not is_linear(graph) and workflow.patched(GRAPH_PATCH_ID):
            return await self._run_graph(inp, graph)
        while self._phase_index < len(phases):
            phase = phases[self._phase_index]
            self._current_phase = phase.id
            self._attempt = 1
            # A rework hands its feedback to the phase it jumped back to; any
            # other phase starts clean.
            retry_feedback = self._rework_feedback
            self._rework_feedback = ""
            jumped = False

            while True:
                self._record(
                    "phase_started",
                    phase=phase.id,
                    detail=f"attempt {self._attempt}" if self._attempt > 1 else "",
                )
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
                            retry_feedback=retry_feedback,
                            attempt=self._attempt,
                            intelligence=inp.intelligence,
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
                    return self._fail_phase(phase.id, exc)
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
                if gate is None or gate.auto_approve:
                    break

                self._retry_reason = None
                verdict = await self._run_gate(inp, phase, entry)
                if verdict is not None:
                    return verdict

                if self._rework_to is not None:
                    # FORGE-500: drop this phase's entry and every one from the
                    # target on; earlier phases keep their entries and approvals.
                    target = self._rework_to
                    self._rework_to = None
                    index = [p.id for p in phases].index(target)
                    del self._completed[index:]
                    self._phase_index = index
                    jumped = True
                    break

                if self._retry_reason is not None:
                    # FORGE-495: keep every earlier approved phase, drop only this
                    # attempt's entry, and run the same phase again.
                    self._completed.pop()
                    retry_feedback = build_retry_feedback(
                        findings=self._gate_findings,
                        reason=self._retry_reason,
                        attempt=self._attempt + 1,
                        revisions=await self._turned_down(inp, phase, self._retry_reason),
                    )
                    self._attempt += 1
                    continue

                if self._change is not None:
                    # A gate boundary is the only safe place to swap the flow:
                    # no phase is in flight, so nothing half-done is orphaned.
                    return await self._apply_change(inp)
                break

            if jumped:
                continue
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

    async def _run_graph(self, inp: DesignFlowInput, graph: FlowGraph) -> dict[str, Any]:
        """Walk a graph flow (FORGE-539).

        Repeatedly: take every phase whose dependencies have settled, skip
        the ones whose condition is false, run the rest as parallel
        activities, then hold their gates one at a time in flow order (so a
        reviewer answers one gate at a time, and every gate behaves exactly as
        in a straight-line run). A rework re-runs the target and what depends
        on it; every other phase keeps its result and its approval.
        """
        self._mode = "graph"
        by_id = {p.id: p for p in inp.flow.phases}
        self._skipped = list(inp.skipped)
        done = [c["phase"] for c in self._completed if c.get("status") != "failed"]
        feedback: dict[str, str] = {}
        attempts: dict[str, int] = {}
        while True:
            ready = graph.ready(done=done, skipped=self._skipped)
            if not ready:
                break
            runnable: list[FrozenPhase] = []
            for phase_id in ready:
                phase = by_id[phase_id]
                if evaluate_condition(phase.condition, inp.flow.facts):
                    runnable.append(phase)
                else:
                    self._skipped.append(phase_id)
                    self._record("phase_skipped", phase=phase_id, detail=phase.condition or "")
            if not runnable:
                continue

            self._running = [p.id for p in runnable]
            self._current_phase = runnable[0].id
            for phase in runnable:
                attempts.setdefault(phase.id, 1)
                self._record("phase_started", phase=phase.id)
            outcomes = await asyncio.gather(
                *(
                    self._execute_phase(inp, phase, feedback.pop(phase.id, ""), attempts[phase.id])
                    for phase in runnable
                ),
                return_exceptions=True,
            )
            self._running = []
            entries: dict[str, dict[str, Any]] = {}
            for phase, outcome in zip(runnable, outcomes, strict=True):
                if isinstance(outcome, ActivityError):
                    return self._fail_phase(phase.id, outcome)
                if isinstance(outcome, BaseException):
                    raise outcome
                entries[phase.id] = self._finish_phase(phase, outcome)

            rerun: set[str] = set()
            for phase in runnable:
                if phase.id in rerun:
                    continue  # a rework in this wave sent it back; it runs again
                entry = entries[phase.id]
                while True:
                    gate = phase.gate
                    if gate is None or gate.auto_approve:
                        done.append(phase.id)
                        break
                    self._current_phase = phase.id
                    self._attempt = attempts[phase.id]
                    self._retry_reason = None
                    verdict = await self._run_gate(
                        inp, phase, entry, rework_ids=rework_candidates(graph, phase.id)
                    )
                    if verdict is not None:
                        return verdict
                    if self._rework_to is not None:
                        target = self._rework_to
                        self._rework_to = None
                        rerun = set(graph.downstream(target)) | {phase.id}
                        self._completed = [c for c in self._completed if c["phase"] not in rerun]
                        done = [d for d in done if d not in rerun]
                        self._skipped = [s for s in self._skipped if s not in rerun]
                        feedback[target] = self._rework_feedback
                        self._rework_feedback = ""
                        for phase_id in rerun:
                            attempts.pop(phase_id, None)
                        self._record(
                            "selective_rework",
                            phase=phase.id,
                            detail=f"re-running {', '.join(sorted(rerun))}",
                            rerun=sorted(rerun),
                        )
                        break
                    if self._retry_reason is not None:
                        self._completed.remove(entry)
                        retry_feedback = build_retry_feedback(
                            findings=self._gate_findings,
                            reason=self._retry_reason,
                            attempt=attempts[phase.id] + 1,
                            revisions=await self._turned_down(inp, phase, self._retry_reason),
                        )
                        attempts[phase.id] += 1
                        self._record(
                            "phase_started", phase=phase.id, detail=f"attempt {attempts[phase.id]}"
                        )
                        try:
                            result = await self._execute_phase(
                                inp, phase, retry_feedback, attempts[phase.id]
                            )
                        except ActivityError as exc:
                            return self._fail_phase(phase.id, exc)
                        entry = self._finish_phase(phase, result)
                        continue
                    if self._change is not None:
                        return await self._apply_change(inp)
                    done.append(phase.id)
                    break
            self._phase_index = len(done)

        self._status = "completed"
        self._record("run_completed")
        return {
            "status": "completed",
            "flow": inp.flow.template_id,
            "version": inp.flow.version,
            "content_hash": inp.flow.content_hash,
            "phases": self._completed,
            "skipped": list(self._skipped),
        }

    async def _execute_phase(
        self, inp: DesignFlowInput, phase: FrozenPhase, retry_feedback: str, attempt: int
    ) -> PhaseResult:
        """One ``run_phase`` activity for ``phase``, with the run's usual policy."""
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
                retry_feedback=retry_feedback,
                attempt=attempt,
                intelligence=inp.intelligence,
            ),
            start_to_close_timeout=_PHASE_TIMEOUT,
            heartbeat_timeout=_PHASE_HEARTBEAT,
            retry_policy=RetryPolicy(maximum_attempts=3),
            result_type=PhaseResult,
        )
        return result

    def _finish_phase(self, phase: FrozenPhase, result: PhaseResult) -> dict[str, Any]:
        result.status = phase_status(result.summary, result.status)
        entry = {
            "phase": phase.id,
            "summary": result.summary,
            "artifacts": result.artifacts,
            "status": result.status,
        }
        self._completed.append(entry)
        self._record("phase_finished", phase=phase.id, detail=result.status)
        return entry

    async def _run_gate(
        self,
        inp: DesignFlowInput,
        phase: FrozenPhase,
        entry: dict[str, Any],
        *,
        rework_ids: list[str] | None = None,
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

        findings, blocking = self._gate_findings_for(phase, gate, check, entry)
        self._gate_findings = findings
        self._gate_ready = not blocking
        if blocking:
            # A gate whose own preconditions failed never reaches an approver as
            # an approvable gate: asking somebody to approve work the system
            # already knows is incomplete trains them to click through.
            if not workflow.patched(RETRY_PATCH_ID):
                return self._legacy_not_ready(phase, gate, check, entry)
            entry["status"] = "failed"
            self._record("gate_not_ready", phase=phase.id, detail="; ".join(findings))
            if workflow.patched(STALL_PATCH_ID):
                history = self._gate_history.setdefault(phase.id, [])
                history.append(tuple(findings))
                streak = findings_streak(history)
                if streak >= DEFAULT_STALL_STOP:
                    self._gate_ready = True
                    self._record("repair_stalled", phase=phase.id, detail=f"{streak} in a row")
                    return self._fail(
                        f"{stall_note(phase.id, streak, DEFAULT_STALL_STOP)} "
                        "[failure class: design]"
                    )
                self._stalled = streak >= 2
        else:
            self._gate_history.pop(phase.id, None)
            self._stalled = False

        reason = check.reason
        if blocking:
            reason = f"NOT READY (retry the phase or reject): {'; '.join(findings)}" + (
                f" | {check.reason}" if check.reason else ""
            )
            if self._stalled:
                streak = findings_streak(self._gate_history.get(phase.id, []))
                reason = f"{stall_note(phase.id, streak, DEFAULT_STALL_STOP)} | {reason}"
        self._gate_open = gate.name
        self._gate_reason = reason
        self._status = "awaiting_approval"
        self._answer = None
        self._record("gate_opened", phase=phase.id, detail=reason)

        await workflow.execute_activity(
            "announce_gate",
            {"run_id": inp.run_id, "gate": gate.name, "reason": reason},
            start_to_close_timeout=_CHECK_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # `wait_condition` raises rather than returning when the timeout
        # expires. Letting that escape would fail the workflow, which reads as
        # a crashed run rather than a gate nobody answered -- the two need
        # different words to whoever is looking at the run list.
        deadline = workflow.now() + timedelta(seconds=inp.gate_timeout_seconds)
        while True:
            remaining = max((deadline - workflow.now()).total_seconds(), 0.0)
            try:
                await workflow.wait_condition(
                    # A parked, not-ready gate takes a decision only; a queued
                    # flow change waits for a gate that is ready.
                    lambda: (
                        self._answer is not None or (self._gate_ready and self._change is not None)
                    ),
                    timeout=timedelta(seconds=remaining),
                )
            except TimeoutError:
                break
            answer = self._answer
            if answer is not None and answer.rework_to:
                error = rework_target_error(
                    rework_ids if rework_ids is not None else [p.id for p in inp.flow.phases],
                    phase.id,
                    answer.rework_to,
                )
                if error is not None:
                    # Refused, not applied: the gate stays open and answerable.
                    self._answer = None
                    self._record("gate_decision_ignored", phase=phase.id, detail=error)
                    continue
                break
            if answer is not None and not answer.approved and not answer.retry:
                break
            if answer is not None and answer.approved and not self._gate_ready:
                # Approving a gate the system knows is incomplete is refused.
                self._answer = None
                self._record(
                    "gate_decision_ignored",
                    phase=phase.id,
                    detail="gate not ready: retry the phase or reject",
                )
                continue
            break

        if self._answer is None and self._change is None:
            self._gate_open = None
            self._gate_ready = True
            return self._reject(
                f"Gate '{gate.name}' was not answered within the approval window. "
                "An unanswered gate is a refusal: nobody looked at this work."
            )

        self._gate_open = None
        answer = self._answer
        if answer is not None and answer.rework_to and workflow.patched(REWORK_PATCH_ID):
            if self._rework_cycles >= self._max_rework_cycles:
                self._gate_ready = True
                return self._fail(
                    f"Gate '{gate.name}' sent the run back to '{answer.rework_to}' but the run "
                    f"already used its {self._max_rework_cycles} rework "
                    f"cycle{'' if self._max_rework_cycles == 1 else 's'} (the per-run cap)."
                )
            self._rework_cycles += 1
            self._rework_to = answer.rework_to
            self._rework_feedback = build_rework_feedback(
                from_phase=phase.id,
                to_phase=answer.rework_to,
                findings=self._gate_findings,
                reason=answer.comment,
                from_summary=str(entry.get("summary") or ""),
                cycle=self._rework_cycles,
                revisions=await self._turned_down(inp, phase, answer.comment),
            )
            self._status = "running"
            self._gate_ready = True
            self._record(
                "phase_rework_requested",
                phase=phase.id,
                detail=(
                    f"cycle {self._rework_cycles} of {self._max_rework_cycles} by "
                    f"{answer.decided_by or 'a reviewer'}: back to '{answer.rework_to}'"
                    + (f": {answer.comment}" if answer.comment else "")
                ),
                **{"from": phase.id, "to": answer.rework_to, "cycle": self._rework_cycles},
            )
            return None
        if answer is not None and answer.retry:
            retries_used = self._attempt - 1
            if retries_used >= self._max_retries:
                self._gate_ready = True
                return self._fail(
                    f"Phase '{phase.id}' did not pass gate '{gate.name}' after "
                    f"{retries_used} retr{'y' if retries_used == 1 else 'ies'} "
                    "(the per-phase cap)."
                )
            self._retry_reason = answer.comment or "retry requested"
            self._status = "running"
            self._gate_ready = True
            self._record(
                "phase_retry_requested",
                phase=phase.id,
                detail=(
                    f"attempt {self._attempt + 1} of {self._max_retries + 1} by "
                    f"{answer.decided_by or 'a reviewer'}: {self._retry_reason}"
                ),
            )
            return None

        self._gate_ready = True
        if answer is not None and not answer.approved:
            return self._reject(
                f"Gate '{gate.name}' rejected by {answer.decided_by or 'a reviewer'}"
                + (f": {answer.comment}" if answer.comment else "")
            )

        if answer is not None:
            self._status = "running"
            self._record("gate_approved", phase=phase.id, detail=answer.decided_by or "approved")
        return None

    async def _turned_down(
        self, inp: DesignFlowInput, phase: FrozenPhase, reviewer: str
    ) -> list[RevisionNote]:
        """The drafts ``phase``'s gate just closed, read in an activity (FORGE-530).

        The gateway closed them before the decision signal arrived, so the
        activity sees them. Patched for replay; a failed read is no notes.
        """
        if not workflow.patched(REVISION_NOTES_PATCH_ID):
            return []
        try:
            rows = await workflow.execute_activity(
                "collect_revision_notes",
                {
                    "run_id": inp.run_id,
                    "phase_id": phase.id,
                    "project_id": inp.project_id,
                    "reason": "; ".join(self._gate_findings) or reviewer,
                },
                start_to_close_timeout=_CHECK_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=2),
                result_type=list,
            )
        except ActivityError:
            self._record("revision_notes_unavailable", phase=phase.id)
            return []
        return notes_from_dicts(rows)

    @staticmethod
    def _gate_findings_for(
        phase: FrozenPhase, gate: Any, check: GateCheck, entry: dict[str, Any]
    ) -> tuple[list[str], bool]:
        """What the gate found, and whether any of it blocks approval."""
        findings: list[str] = []
        blocking = False
        if phase.enforce_deliverables and check.checked and not check.ready:
            blocking = True
            findings.append(
                f"phase '{phase.id}' did not record required deliverables {check.missing} "
                f"(present: {check.present or 'none'})"
            )
        if phase.enforce_deliverables and result_ungrounded(entry):
            blocking = True
            findings.append(
                f"phase '{phase.id}' reply was flagged ungrounded (no tool calls were made), "
                "so its claimed work is unverified"
            )
        if check.constraints_checked and not check.constraints_passed:
            if gate.enforce_constraints:
                blocking = True
            findings.append(
                f"{len(check.violations)} constraint violation(s): "
                f"{'; '.join(check.violations[:10])}"
            )
        return findings, blocking

    def _legacy_not_ready(
        self, phase: FrozenPhase, gate: Any, check: GateCheck, entry: dict[str, Any]
    ) -> dict[str, Any]:
        """The pre-FORGE-495 behaviour: a gate that is not ready fails the run.

        Kept only so a run whose history already holds this failure replays
        deterministically (``workflow.patched``).
        """
        if phase.enforce_deliverables and check.checked and not check.ready:
            entry["status"] = "failed"
            self._record("phase_failed", phase=phase.id, detail=f"missing {check.missing}")
            return self._fail(
                f"Gate '{gate.name}' not ready: phase '{phase.id}' did not record "
                f"required deliverables {check.missing} (present: {check.present or 'none'})."
            )
        if phase.enforce_deliverables and result_ungrounded(entry):
            entry["status"] = "failed"
            self._record("phase_failed", phase=phase.id, detail="ungrounded reply")
            return self._fail(
                f"Gate '{gate.name}' not ready: phase '{phase.id}' reply was flagged "
                "ungrounded (no tool calls were made), so its claimed work is unverified."
            )
        return self._fail(
            f"Gate '{gate.name}' not ready: {len(check.violations)} constraint "
            f"violation(s): {'; '.join(check.violations[:10])}"
        )

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
        completed = list(self._completed)
        skipped = list(self._skipped)
        if change.rerun:
            completed, skipped = _keep_for_patch(change.flow, completed, skipped, change.rerun)
            self._record(
                "patch_applied",
                detail=f"re-running {', '.join(change.rerun)}",
                rerun=list(change.rerun),
                kept=[c["phase"] for c in completed],
            )
        workflow.continue_as_new(
            DesignFlowInput(
                run_id=inp.run_id,
                goal=inp.goal,
                flow=change.flow,
                project_id=inp.project_id,
                session_id=inp.session_id,
                gate_timeout_seconds=inp.gate_timeout_seconds,
                completed=completed,
                max_phase_retries=inp.max_phase_retries,
                max_rework_cycles=inp.max_rework_cycles,
                rework_cycles=self._rework_cycles,
                skipped=skipped,
                intelligence=inp.intelligence,
            )
        )
        raise AssertionError("unreachable: continue_as_new does not return")

    # ── bookkeeping ──────────────────────────────────────────────────────

    def _fail_phase(self, phase_id: str, exc: ActivityError) -> dict[str, Any]:
        """End the run because ``phase_id`` could not run, with its failure class."""
        cause = exc.cause
        reason = str(cause) if cause is not None else str(exc)
        result = self._fail(f"Phase '{phase_id}' failed: {reason}")
        if workflow.patched(FAILURE_CLASS_PATCH_ID):
            verdict = classify_failure(reason, error_type=str(getattr(cause, "type", "") or ""))
            result["failure"] = {"phase": phase_id, **verdict.as_dict()}
            self._record(
                "phase_failure_classified",
                phase=phase_id,
                detail=f"{verdict.failure_class.value}: {verdict.guidance}",
            )
        return result

    def _fail(self, message: str) -> dict[str, Any]:
        self._status = "failed"
        self._error = message
        self._record("run_failed", detail=message)
        return {"status": "failed", "error": message, "phases": self._completed}

    def _reject(self, message: str) -> dict[str, Any]:
        self._status = "rejected"
        self._record("run_rejected", detail=message)
        return {"status": "rejected", "error": message, "phases": self._completed}

    def _record(
        self, event: str, *, phase: str | None = None, detail: str = "", **extra: Any
    ) -> None:
        self._events.append(
            {
                "event": event,
                "phase": phase,
                "detail": detail,
                **extra,
                # workflow.now() is the replay-safe clock; time.time() here
                # would make every replay diverge.
                "at": workflow.now().isoformat(),
            }
        )


def _keep_for_patch(
    flow: FrozenFlow,
    completed: list[dict[str, Any]],
    skipped: list[str],
    rerun: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """The completed entries and skips an approved patch keeps (FORGE-539).

    A graph flow keeps every completed phase that is still in the flow and
    not re-run. A straight-line flow resumes by position, so it keeps the
    longest prefix of the new phase order that is completed and not re-run;
    everything after the first gap runs again, which for a line is exactly
    the downstream of that gap.
    """
    drop = set(rerun)
    ids = [p.id for p in flow.phases]
    by_phase = {c["phase"]: c for c in completed}
    if is_linear(build_graph(flow.phases)):
        kept: list[dict[str, Any]] = []
        for phase_id in ids:
            entry = by_phase.get(phase_id)
            if entry is None or phase_id in drop:
                break
            kept.append(entry)
        return kept, []
    kept_graph = [c for c in completed if c["phase"] in ids and c["phase"] not in drop]
    return kept_graph, [s for s in skipped if s in ids and s not in drop]
