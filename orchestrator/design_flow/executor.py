"""Design-flow executor + gate coordinator (MET-10, Phase 1).

The executor is the spine the terrain map found missing: it binds a run to a
phase-sequencing loop and pauses at gates. For each phase it asks a
:class:`PhaseBrain` to produce work (recorded to the twin by the brain's tools),
then — if the phase has a gate — moves the run to ``awaiting_approval`` and
waits for a decision routed in through :class:`GateCoordinator`. Approve
resumes to the next phase; reject ends the run.

Nothing here talks HTTP or LLMs directly: the brain is injected (a scripted
double in tests, the ReAct harness in production) and the run store is the same
:class:`~orchestrator.harness.runs.InMemoryRunStore` the ``/v1/runs`` surface
already wraps.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import structlog

from observability.tracing import get_tracer
from orchestrator.design_flow.grounding import UNGROUNDED_STATUS, phase_status
from orchestrator.design_flow.retry import build_retry_feedback, max_phase_retries
from orchestrator.design_flow.rework import (
    build_rework_feedback,
    max_rework_cycles,
    rework_target_error,
)
from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FlowDefinition, Phase, get_flow
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    RunStatus,
)
from twin_core.consistency import GateEvaluation

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.design_flow.executor")


@dataclass
class ReworkJump:
    """A reviewer sent the run back to an earlier phase (FORGE-500)."""

    to_index: int
    feedback: str


class FlowCanceled(Exception):
    """Raised inside the executor when its run is canceled while at a gate."""


@dataclass
class PhaseOutcome:
    """What a phase produced.

    ``summary`` is the brain's narrative (surfaced in the gate reason / run
    result). ``artifacts`` are work-product ids/names the brain reports having
    recorded. ``status`` is "completed" or "exhausted" (brain ran out of steps).
    """

    summary: str
    artifacts: list[str] = field(default_factory=list)
    status: str = "completed"


@dataclass
class FlowContext:
    """Accumulating context threaded across phases."""

    goal: str
    project_id: str | None = None
    session_id: str | None = None
    #: FORGE-491: the approved flow's context block, shown to every phase.
    flow_context: str = ""
    #: FORGE-495: on a retry, the gate's findings and the reviewer's reason.
    #: The phase brain puts this first in its prompt. Empty on a first attempt.
    retry_feedback: str = ""
    attempt: int = 1
    completed: list[tuple[Phase, PhaseOutcome]] = field(default_factory=list)


@dataclass
class ReadinessReport:
    """Whether a phase's required deliverables are present in the twin."""

    ready: bool
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    checked: bool = True  # False when no evaluator was available to check


@runtime_checkable
class PhaseBrain(Protocol):
    """Produces the work for one phase.

    Implementations own *how* the work is done (ReAct loop + MCP tools in
    production). They are expected to record artifacts into the twin via tools;
    the returned :class:`PhaseOutcome` is the executor-facing summary.
    """

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome: ...


@runtime_checkable
class GateEvaluator(Protocol):
    """Reports which work-product *types* a project has recorded since ``since_ts``.

    Backed in production by the project store the dashboard reads, so gate
    readiness matches what a human can actually see in the twin viewer.
    """

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]: ...


@dataclass
class ConstraintReport:
    """Constraint-engine state at a gate (MET-583).

    ``checked`` is False when no checker was wired or evaluation failed —
    constraint state must never crash a run, so an unchecked report always
    reads as passing.
    """

    checked: bool = False
    passed: bool = True
    evaluated_count: int = 0
    violations: list[str] = field(default_factory=list)  # ERROR severity
    warnings: list[str] = field(default_factory=list)
    # FORGE-498: what the gate could and could not compare. ``satisfied`` and
    # ``not_evaluated`` are shown to the reviewer, never counted as violations;
    # ``assumptions`` are the analysis modelling assumptions to review.
    satisfied: list[str] = field(default_factory=list)
    not_evaluated: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@runtime_checkable
class ConstraintChecker(Protocol):
    """Evaluates a project's recorded constraints for gate review (MET-583).

    Backed in production by the twin's constraint engine
    (``TwinAPI.evaluate_constraints``); ``project_id`` scopes the violations
    to the run's project where the constraint data allows it.
    """

    async def check(self, project_id: str | None) -> ConstraintReport: ...


@dataclass
class ConsistencyGateReport:
    """A gate's real ``twin_core.consistency.gates`` evaluation (FORGE-73).

    ``checked`` is False when the gate has no ``gate_id`` (most gates today
    -- see :class:`~orchestrator.design_flow.spec.Gate`'s own docstring for
    why), no checker was wired, or ``project_id`` is missing/unparseable --
    same fail-open contract as :class:`ConstraintReport`. Purely
    informational: surfaced in the gate reason, never blocks a transition.
    """

    checked: bool = False
    evaluation: GateEvaluation | None = None


@runtime_checkable
class ConsistencyGateChecker(Protocol):
    """Evaluates a gate's real G-number status (FORGE-73), when it has one.

    Backed in production by ``twin_core.consistency.gates.evaluate_gN_*``,
    dispatched on ``gate.gate_id``. Unlike :class:`ConstraintChecker`, this
    never fails a gate automatically -- there is no ``enforce_*`` flag for
    it (yet); see :class:`~orchestrator.design_flow.spec.Gate`'s docstring.
    """

    async def check(self, gate_id: str, project_id: str | None) -> ConsistencyGateReport: ...


class GateCoordinator:
    """Bridges async gate waits to synchronous run-store transitions.

    The executor ``register()``s a run before pausing it, then ``await wait()``.
    The run store's ``on_transition`` observer (wired in the gateway) calls
    :meth:`on_transition`; when the paused run leaves ``awaiting_approval`` we
    resolve the waiter: RUNNING -> approve, REJECTED -> reject, CANCELED ->
    :class:`FlowCanceled`.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[ApprovalDecision]] = {}
        #: FORGE-495: a retry also moves the run back to ``running``, which the
        #: transition observer cannot tell from an approval. The route notes
        #: the retry here first, with the reviewer's reason.
        self._retries: dict[str, str] = {}
        #: FORGE-500: the same for a rework: ``(to_phase, reason)`` per run.
        self._reworks: dict[str, tuple[str, str]] = {}
        self._gate_state: dict[str, dict[str, object]] = {}

    def note_retry(self, run_id: str, reason: str) -> None:
        self._retries[run_id] = reason

    def take_retry_reason(self, run_id: str) -> str:
        return self._retries.pop(run_id, "")

    def note_rework(self, run_id: str, to_phase: str, reason: str) -> None:
        self._reworks[run_id] = (to_phase, reason)

    def take_rework(self, run_id: str) -> tuple[str, str] | None:
        return self._reworks.pop(run_id, None)

    def set_gate_state(
        self,
        run_id: str,
        *,
        ready: bool,
        retries_left: int,
        phase: str | None = None,
        reworks_left: int | None = None,
    ) -> None:
        self._gate_state[run_id] = {
            "ready": ready,
            "retries_left": retries_left,
            "phase": phase,
            "reworks_left": reworks_left,
        }

    def gate_state(self, run_id: str) -> dict[str, object] | None:
        """What the in-process executor reports about the gate ``run_id`` is at."""
        return self._gate_state.get(run_id)

    def register(self, run_id: str) -> asyncio.Future[ApprovalDecision]:
        """Create (and store) a waiter future for ``run_id`` on the running loop."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._waiters[run_id] = fut
        return fut

    async def wait(self, run_id: str) -> ApprovalDecision:
        fut = self._waiters.get(run_id)
        if fut is None:
            raise RuntimeError(f"no gate waiter registered for run '{run_id}'")
        try:
            return await fut
        finally:
            self._waiters.pop(run_id, None)

    def on_transition(self, run: object) -> None:
        """Run-store observer: resolve a pending waiter on the paused run."""
        run_id = getattr(run, "id", None)
        status = getattr(run, "status", None)
        if run_id is None:
            return
        fut = self._waiters.get(run_id)
        if fut is None or fut.done():
            return
        if status is RunStatus.RUNNING:
            if run_id in self._reworks:
                fut.set_result(ApprovalDecision.REWORK)
            elif run_id in self._retries:
                fut.set_result(ApprovalDecision.RETRY)
            else:
                fut.set_result(ApprovalDecision.APPROVE)
        elif status is RunStatus.REJECTED:
            fut.set_result(ApprovalDecision.REJECT)
        elif status is RunStatus.CANCELED:
            fut.set_exception(FlowCanceled(run_id))


def _consistency_summary(evaluation: GateEvaluation) -> str:
    passed = sum(1 for c in evaluation.checks if c.status.value == "pass")
    failed = sum(1 for c in evaluation.checks if c.status.value == "fail")
    not_evaluated = sum(1 for c in evaluation.checks if c.status.value == "not_evaluated")
    parts = f"{passed} pass, {failed} fail, {not_evaluated} not evaluated"
    return f"{evaluation.gate_id}: {evaluation.status.value} ({parts})"


def _constraint_details(constraints: ConstraintReport) -> str:
    """Passed / not-evaluated findings and modelling assumptions for the reviewer (FORGE-498)."""
    out = ""
    if constraints.satisfied:
        out += f" | Passed ({len(constraints.satisfied)}): " + "; ".join(constraints.satisfied[:5])
    if constraints.not_evaluated:
        out += f" | Not evaluated ({len(constraints.not_evaluated)}): " + "; ".join(
            constraints.not_evaluated[:5]
        )
    if constraints.assumptions:
        out += " | Modelling assumptions: " + "; ".join(constraints.assumptions[:3])
    return out


def _gate_reason(
    phase: Phase,
    outcome: PhaseOutcome,
    readiness: ReadinessReport,
    constraints: ConstraintReport | None = None,
    consistency: ConsistencyGateReport | None = None,
) -> str:
    """Human-facing reason shown while a run waits at a gate."""
    gate = phase.gate
    label = gate.name if gate else phase.title
    head = f"[{label}] {phase.title} complete. {outcome.summary}".strip()
    if gate and gate.criteria:
        head += " | Review criteria: " + "; ".join(gate.criteria)
    if readiness.checked and (readiness.present or readiness.missing):
        head += (
            f" | Deliverables present: {readiness.present or '—'};"
            f" missing: {readiness.missing or 'none'}"
        )
    # MET-583: surface real constraint state to the approver at every gate,
    # whether or not this gate enforces it.
    if constraints is not None and constraints.checked:
        if constraints.violations or constraints.warnings:
            parts = []
            if constraints.violations:
                parts.append(
                    f"{len(constraints.violations)} violation(s): "
                    + "; ".join(constraints.violations[:5])
                )
            if constraints.warnings:
                parts.append(f"{len(constraints.warnings)} warning(s)")
            head += " | Constraints: " + " — ".join(parts)
        else:
            head += f" | Constraints: OK ({constraints.evaluated_count} evaluated)"
        head += _constraint_details(constraints)
    # FORGE-73: real G-number status, purely informational -- never changes
    # whether this gate blocks (see Gate.gate_id's own docstring).
    if consistency is not None and consistency.checked and consistency.evaluation is not None:
        head += " | " + _consistency_summary(consistency.evaluation)
    return head[:2000]


class DesignFlowExecutor:
    """Walks a :class:`FlowDefinition`, gating between phases.

    ``gate_evaluator`` (optional) lets a gate check that a phase actually
    recorded its ``required_deliverables`` into the twin; without one, gates
    proceed on the brain summary alone (readiness "unchecked").
    """

    def __init__(
        self,
        *,
        store: InMemoryRunStore,
        brain: PhaseBrain,
        coordinator: GateCoordinator,
        gate_evaluator: GateEvaluator | None = None,
        constraint_checker: ConstraintChecker | None = None,
        consistency_gate_checker: ConsistencyGateChecker | None = None,
    ) -> None:
        self._store = store
        self._brain = brain
        self._coordinator = coordinator
        self._evaluator = gate_evaluator
        self._constraint_checker = constraint_checker
        self._consistency_gate_checker = consistency_gate_checker

    async def run(
        self, run_id: str, flow: FlowDefinition | None = None, *, flow_context: str = ""
    ) -> None:
        """Drive ``run_id`` through its flow to a terminal state.

        ``flow`` is the exact definition to walk (FORGE-474). The gateway
        passes the approved, frozen version a run was started on, so a
        tailored flow runs as approved rather than as the template it came
        from. Without one, the run's ``flow`` id is looked up in the built-in
        catalogue, which is only correct for a run that names a template.

        Best-effort: swallows :class:`InvalidTransition` (the run was canceled
        or completed out from under us) and records unexpected errors via
        ``store.fail`` so the run never dangles in a non-terminal state.
        """
        with tracer.start_as_current_span("design_flow.run") as span:
            span.set_attribute("run.id", run_id)
            try:
                run = self._store.get(run_id)
                if flow is None:
                    flow = get_flow(run.request.get("flow") or DEFAULT_FLOW_ID)
                goal = str(run.request.get("goal") or "").strip()
                span.set_attribute("flow.id", flow.id)
                span.set_attribute("flow.phase_count", len(flow.phases))
                ctx = FlowContext(
                    goal=goal,
                    project_id=run.request.get("project_id"),
                    session_id=run.request.get("session_id"),
                    flow_context=flow_context,
                )
                if run.status is RunStatus.QUEUED:
                    self._store.start(run_id)

                await self._walk(run_id, flow, ctx)
            except FlowCanceled:
                logger.info("design_flow_canceled", run_id=run_id)
            except InvalidTransition as exc:
                # Run reached a terminal/illegal state externally; stop quietly.
                logger.info("design_flow_transition_stop", run_id=run_id, detail=str(exc))
            except Exception as exc:  # noqa: BLE001 - surface any failure onto the run
                span.record_exception(exc)
                logger.error("design_flow_failed", run_id=run_id, error=str(exc))
                try:
                    self._store.fail(run_id, str(exc))
                except InvalidTransition:
                    pass

    async def _walk(self, run_id: str, flow: FlowDefinition, ctx: FlowContext) -> None:
        max_retries = max_phase_retries()
        max_rework = max_rework_cycles()
        cycles = 0
        index = 0
        pending_feedback = ""
        while index < len(flow.phases):
            phase = flow.phases[index]
            attempt = 1
            # A rework hands its feedback to the phase it jumped back to.
            ctx.retry_feedback = pending_feedback
            pending_feedback = ""
            ctx.attempt = 1
            jump: ReworkJump | None = None
            while True:
                result = await self._attempt_phase(
                    run_id, phase, ctx, attempt, max_retries, flow, cycles, max_rework
                )
                if isinstance(result, ReworkJump):
                    jump = result
                    break
                if result:
                    break
                # Retry: drop this attempt's outcome, keep every earlier phase.
                attempt += 1
                ctx.attempt = attempt
            if jump is not None:
                # Drop this phase's outcome and every one from the target on;
                # earlier phases keep their outcomes and approvals.
                cycles += 1
                del ctx.completed[jump.to_index :]
                pending_feedback = jump.feedback
                index = jump.to_index
                continue
            if self._store.get(run_id).is_terminal:
                return
            index += 1
        ctx.retry_feedback = ""
        self._store.complete(run_id, result=self._summarize(flow, ctx))

    async def _attempt_phase(
        self,
        run_id: str,
        phase: Phase,
        ctx: FlowContext,
        attempt: int,
        max_retries: int,
        flow: FlowDefinition,
        rework_cycles: int = 0,
        max_rework: int = 0,
    ) -> bool | ReworkJump:
        """Run one attempt of ``phase`` and its gate.

        Returns True when the phase is done (approved, or ended the run), False
        when a reviewer asked for a retry, or a :class:`ReworkJump` when they
        sent the run back to an earlier phase (FORGE-500). Each attempt is logged.
        """
        phase_start = time.time()
        logger.info("design_flow_phase_start", run_id=run_id, phase=phase.id, attempt=attempt)
        outcome = await self._brain.run_phase(goal=ctx.goal, phase=phase, context=ctx)
        outcome.status = phase_status(outcome.summary, outcome.status)
        ctx.completed.append((phase, outcome))
        logger.info(
            "design_flow_phase_done",
            run_id=run_id,
            phase=phase.id,
            attempt=attempt,
            status=outcome.status,
            artifacts=len(outcome.artifacts),
        )

        gate = phase.gate
        if gate is None or gate.auto_approve:
            return True

        # Readiness: did the phase record its required deliverables?
        readiness = await self._readiness(phase, ctx, since_ts=phase_start)
        findings: list[str] = []
        if phase.enforce_deliverables and readiness.checked and not readiness.ready:
            findings.append(
                f"phase '{phase.id}' did not record required deliverables "
                f"{readiness.missing} into the twin (present: {readiness.present or 'none'})"
            )
        if phase.enforce_deliverables and outcome.status == UNGROUNDED_STATUS:
            findings.append(
                f"phase '{phase.id}' reply was flagged ungrounded (no tool calls were "
                "made), so its claimed work is unverified"
            )

        # MET-583 constraint-as-gate-criteria: evaluate the project's
        # recorded constraints. Surfaced in the gate reason at every gate;
        # gates with enforce_constraints block on ERROR violations, same
        # contract as missing deliverables.
        constraints = await self._constraints(ctx)
        if constraints.checked and not constraints.passed:
            findings_c = (
                f"{len(constraints.violations)} constraint violation(s): "
                f"{'; '.join(constraints.violations[:10])}"
            )
            if gate.enforce_constraints:
                findings.append(findings_c)
                logger.warning(
                    "design_flow_gate_constraints_failed",
                    run_id=run_id,
                    gate=gate.name,
                    violations=len(constraints.violations),
                )
        blocking = bool(findings)

        # FORGE-73/91: real G-number status (G3-G8), purely informational
        # (see Gate.gate_id's docstring for why this never fails a gate).
        consistency = await self._consistency(gate.gate_id, ctx)

        reason = _gate_reason(phase, outcome, readiness, constraints, consistency)
        if blocking:
            # FORGE-495: park with the findings instead of failing the run, so
            # the reviewer can retry the phase (or reject). Approve is refused.
            logger.warning("design_flow_gate_not_ready", run_id=run_id, findings=findings)
            reason = f"[{gate.name}] NOT READY (retry the phase or reject): " + "; ".join(findings)
            if constraints.checked:
                reason += _constraint_details(constraints)
        retries_left = max(max_retries - (attempt - 1), 0)
        reworks_left = max(max_rework - rework_cycles, 0)
        self._coordinator.set_gate_state(
            run_id,
            ready=not blocking,
            retries_left=retries_left,
            phase=phase.id,
            reworks_left=reworks_left,
        )

        # Register the waiter BEFORE moving to awaiting_approval so a fast
        # approval can't race ahead of the future.
        self._coordinator.register(run_id)
        self._store.request_approval(run_id, reason=reason[:2000])
        logger.info("design_flow_gate_wait", run_id=run_id, gate=gate.name, ready=not blocking)
        decision = await self._coordinator.wait(run_id)
        self._coordinator.set_gate_state(
            run_id,
            ready=True,
            retries_left=retries_left,
            phase=phase.id,
            reworks_left=reworks_left,
        )
        if decision is ApprovalDecision.REWORK:
            noted = self._coordinator.take_rework(run_id)
            to_phase, reviewer = noted if noted is not None else ("", "")
            phase_ids = [p.id for p in flow.phases]
            error = rework_target_error(phase_ids, phase.id, to_phase)
            if error is None and rework_cycles >= max_rework:
                error = f"the run already used its {max_rework} rework cycle(s) (the per-run cap)"
            if error is not None:
                logger.warning("design_flow_rework_refused", run_id=run_id, reason=error)
                self._store.fail(run_id, f"Rework from gate '{gate.name}' refused: {error}.")
                return True
            cycle = rework_cycles + 1
            logger.info(
                "design_flow_phase_rework_requested",
                run_id=run_id,
                **{"from": phase.id, "to": to_phase, "cycle": cycle},
            )
            return ReworkJump(
                to_index=phase_ids.index(to_phase),
                feedback=build_rework_feedback(
                    from_phase=phase.id,
                    to_phase=to_phase,
                    findings=findings,
                    reason=reviewer,
                    from_summary=outcome.summary,
                    cycle=cycle,
                ),
            )
        if decision is ApprovalDecision.REJECT:
            # submit_approval already moved the run to REJECTED (terminal).
            logger.info("design_flow_gate_rejected", run_id=run_id, gate=gate.name)
            return True
        if decision is ApprovalDecision.RETRY:
            reviewer = self._coordinator.take_retry_reason(run_id)
            if retries_left <= 0:
                msg = (
                    f"Phase '{phase.id}' did not pass gate '{gate.name}' after "
                    f"{attempt - 1} retries (the per-phase cap)."
                )
                logger.warning("design_flow_retry_cap", run_id=run_id, phase=phase.id)
                self._store.fail(run_id, msg)
                return True
            ctx.completed.pop()
            ctx.retry_feedback = build_retry_feedback(
                findings=findings, reason=reviewer, attempt=attempt + 1
            )
            logger.info(
                "design_flow_phase_retry",
                run_id=run_id,
                phase=phase.id,
                attempt=attempt + 1,
                reason=reviewer,
            )
            return False
        logger.info("design_flow_gate_approved", run_id=run_id, gate=gate.name)
        return True

    async def _readiness(
        self, phase: Phase, ctx: FlowContext, *, since_ts: float
    ) -> ReadinessReport:
        """Check the twin for the phase's required deliverables."""
        required = set(phase.required_deliverables)
        if not required:
            return ReadinessReport(ready=True, checked=True)
        if self._evaluator is None:
            return ReadinessReport(ready=True, checked=False)
        try:
            present = await self._evaluator.present_types(ctx.project_id, since_ts)
        except Exception as exc:  # noqa: BLE001 - readiness must not crash the run
            logger.warning("design_flow_readiness_error", phase=phase.id, error=str(exc))
            return ReadinessReport(ready=True, checked=False)
        missing = sorted(required - present)
        return ReadinessReport(
            ready=not missing,
            present=sorted(required & present),
            missing=missing,
            checked=True,
        )

    async def _constraints(self, ctx: FlowContext) -> ConstraintReport:
        """Evaluate the project's constraints for gate review (best-effort)."""
        if self._constraint_checker is None:
            return ConstraintReport(checked=False)
        try:
            return await self._constraint_checker.check(ctx.project_id)
        except Exception as exc:  # noqa: BLE001 - constraint state must not crash the run
            logger.warning("design_flow_constraint_check_error", error=str(exc))
            return ConstraintReport(checked=False)

    async def _consistency(self, gate_id: str | None, ctx: FlowContext) -> ConsistencyGateReport:
        """Evaluate the gate's real G-number status, when it has one
        (FORGE-73, best-effort -- same fail-open contract as _constraints)."""
        if gate_id is None or self._consistency_gate_checker is None:
            return ConsistencyGateReport(checked=False)
        try:
            return await self._consistency_gate_checker.check(gate_id, ctx.project_id)
        except Exception as exc:  # noqa: BLE001 - consistency state must not crash the run
            logger.warning("design_flow_consistency_check_error", gate_id=gate_id, error=str(exc))
            return ConsistencyGateReport(checked=False)

    @staticmethod
    def _summarize(flow: FlowDefinition, ctx: FlowContext) -> dict[str, object]:
        return {
            "flow": flow.id,
            "goal": ctx.goal,
            "project_id": ctx.project_id,
            "phases": [
                {
                    "id": phase.id,
                    "title": phase.title,
                    "status": outcome.status,
                    "summary": outcome.summary,
                    "artifacts": outcome.artifacts,
                }
                for phase, outcome in ctx.completed
            ],
        }
