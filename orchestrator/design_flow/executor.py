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
from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FlowDefinition, Phase, get_flow
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    RunStatus,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.design_flow.executor")


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
            fut.set_result(ApprovalDecision.APPROVE)
        elif status is RunStatus.REJECTED:
            fut.set_result(ApprovalDecision.REJECT)
        elif status is RunStatus.CANCELED:
            fut.set_exception(FlowCanceled(run_id))


def _gate_reason(phase: Phase, outcome: PhaseOutcome, readiness: ReadinessReport) -> str:
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
    ) -> None:
        self._store = store
        self._brain = brain
        self._coordinator = coordinator
        self._evaluator = gate_evaluator

    async def run(self, run_id: str) -> None:
        """Drive ``run_id`` through its flow to a terminal state.

        Best-effort: swallows :class:`InvalidTransition` (the run was canceled
        or completed out from under us) and records unexpected errors via
        ``store.fail`` so the run never dangles in a non-terminal state.
        """
        with tracer.start_as_current_span("design_flow.run") as span:
            span.set_attribute("run.id", run_id)
            try:
                run = self._store.get(run_id)
                flow = get_flow(run.request.get("flow") or DEFAULT_FLOW_ID)
                goal = str(run.request.get("goal") or "").strip()
                span.set_attribute("flow.id", flow.id)
                ctx = FlowContext(
                    goal=goal,
                    project_id=run.request.get("project_id"),
                    session_id=run.request.get("session_id"),
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
        for phase in flow.phases:
            phase_start = time.time()
            logger.info("design_flow_phase_start", run_id=run_id, phase=phase.id)
            outcome = await self._brain.run_phase(goal=ctx.goal, phase=phase, context=ctx)
            ctx.completed.append((phase, outcome))
            logger.info(
                "design_flow_phase_done",
                run_id=run_id,
                phase=phase.id,
                status=outcome.status,
                artifacts=len(outcome.artifacts),
            )

            gate = phase.gate
            if gate is None or gate.auto_approve:
                continue

            # Readiness: did the phase record its required deliverables?
            readiness = await self._readiness(phase, ctx, since_ts=phase_start)
            if phase.enforce_deliverables and readiness.checked and not readiness.ready:
                msg = (
                    f"Gate '{gate.name}' not ready — phase '{phase.id}' did not record "
                    f"required deliverables {readiness.missing} into the twin "
                    f"(present: {readiness.present or 'none'})."
                )
                logger.warning(
                    "design_flow_gate_not_ready", run_id=run_id, missing=readiness.missing
                )
                self._store.fail(run_id, msg)
                return

            # Register the waiter BEFORE moving to awaiting_approval so a fast
            # approval can't race ahead of the future.
            self._coordinator.register(run_id)
            self._store.request_approval(run_id, reason=_gate_reason(phase, outcome, readiness))
            logger.info("design_flow_gate_wait", run_id=run_id, gate=gate.name)
            decision = await self._coordinator.wait(run_id)
            if decision is ApprovalDecision.REJECT:
                # submit_approval already moved the run to REJECTED (terminal).
                logger.info("design_flow_gate_rejected", run_id=run_id, gate=gate.name)
                return
            logger.info("design_flow_gate_approved", run_id=run_id, gate=gate.name)

        self._store.complete(run_id, result=self._summarize(flow, ctx))

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
