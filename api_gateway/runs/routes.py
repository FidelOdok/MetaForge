"""Runs REST endpoints for the MetaForge Gateway (MET-547, Phase 1).

The OpenAI-compatible Runs API surface over the harness run lifecycle:

* ``POST   /v1/runs``               create a run (optionally start it)
* ``GET    /v1/runs``               list runs
* ``GET    /v1/runs/{id}``          fetch one run
* ``POST   /v1/runs/{id}/approval`` approve, reject or (design flows) retry or rework a paused run

The run store is process-local for now (mirrors the chat backend pattern);
persistence lands in Phase 4. Domain errors map to clean HTTP status:
:class:`RunNotFoundError` -> 404, :class:`InvalidTransition` -> 409.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api_gateway.auth.approver import approver_from_request
from api_gateway.runs import change_sets as run_change_sets
from api_gateway.runs.engine import FlowEngine, resolve_flow_engine, temporal_target
from api_gateway.runs.schemas import (
    ApprovalRequest,
    CreateRunRequest,
    RunListResponse,
    RunResponse,
    filter_by_project,
)
from api_gateway.runs.streaming import RunStreamManager, run_event_stream, run_ws_loop
from mcp_core.guardrails import Approver
from observability.metrics import MetricsCollector
from orchestrator.design_flow.executor import DesignFlowExecutor, GateCoordinator
from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.invariants import FlowInvariantError, validate_flow
from orchestrator.design_flow.launcher import (
    DesignFlowLauncher,
    DesignFlowWorkerUnavailableError,
    TemporalUnavailableError,
    WorkflowNotFoundError,
    connect_temporal,
)
from orchestrator.design_flow.rework import rework_target_error
from orchestrator.design_flow.spec import (
    DEFAULT_FLOW_ID,
    FlowDefinition,
    definition_from_frozen,
    flow_version,
    get_flow,
)
from orchestrator.design_flow.versions import VersionNotFoundError, get_version_store
from orchestrator.harness.ledger import SqliteRunLedger
from orchestrator.harness.providers.usage import ensure_usage_store, run_usage, usage_report
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    Run,
    RunNotFoundError,
    RunStatus,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/runs", tags=["runs"])

# Process-local store + SSE manager + gate coordinator (mirrors the chat backend
# pattern). The store notifies BOTH the SSE stream and the gate coordinator so a
# design-flow run paused at a gate resumes when POST /approval fires a
# transition. reset_run_store() rewires all three for tests.
_stream_manager = RunStreamManager()
_gate_coordinator = GateCoordinator()
# Production-harness audit follow-up: this module's own docstring said
# "persistence lands in Phase 4" — ledger.py was built (SQLite, described as
# restart-surviving) but never actually wired to _store. None here means the
# historical, purely process-local behavior; init_run_ledger() opts in.
_ledger: SqliteRunLedger | None = None


def _on_transition(run: Run) -> None:
    _stream_manager.publish(run)
    _gate_coordinator.on_transition(run)
    try:
        # FORGE-525: a run that ended outside a gate decision settles its drafts.
        run_change_sets.on_run_transition(run)
    except Exception as exc:  # noqa: BLE001 - drafts must never break a transition
        logger.warning("run_change_set_hook_failed", run_id=run.id, error=str(exc))
    if _ledger is not None:
        try:
            _ledger.record_run(run)
        except Exception as exc:  # noqa: BLE001 - durability must never break a transition
            logger.warning("run_ledger_write_failed", run_id=run.id, error=str(exc))


_store = InMemoryRunStore(on_transition=_on_transition)

# Background executor tasks, kept referenced so they aren't GC'd mid-run.
_flow_tasks: set[asyncio.Task[None]] = set()


_collector: MetricsCollector | None = None


def set_metrics_collector(collector: MetricsCollector | None) -> None:
    """Wire the gateway's collector in (``server.py`` at start-up)."""
    global _collector  # noqa: PLW0603
    _collector = collector


def _metrics() -> MetricsCollector:
    """The collector, or a no-op one.

    A no-op collector is the right default here and not a silent fallback:
    it is what ``MetricsCollector()`` does without an OTel SDK, and the
    gateway already logs which of the two it built at start-up.
    """
    return _collector if _collector is not None else MetricsCollector()


_launcher: DesignFlowLauncher | None = None


async def get_flow_launcher() -> DesignFlowLauncher:
    """The Temporal launcher, connected on first use.

    Connecting lazily rather than at import keeps the gateway startable with
    Temporal down — a gateway that cannot serve reads because the workflow
    engine is unreachable is a worse outage than one that cannot start runs.
    What it does *not* do is let a run start anyway: see ``_launch_flow``.
    """
    global _launcher  # noqa: PLW0603
    if _launcher is None:
        _launcher = DesignFlowLauncher(client=await connect_temporal(temporal_target()))
    return _launcher


def set_flow_launcher(launcher: DesignFlowLauncher | None) -> None:
    """Inject a launcher (tests, and the worker bootstrap)."""
    global _launcher  # noqa: PLW0603
    _launcher = launcher


def get_run_store() -> InMemoryRunStore:
    return _store


def get_gate_coordinator() -> GateCoordinator:
    return _gate_coordinator


def init_run_store(store: InMemoryRunStore) -> None:
    global _store
    store.set_on_transition(_on_transition)
    _store = store


_RESUMABLE_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.AWAITING_APPROVAL.value,
}


def init_run_ledger(ledger: SqliteRunLedger | None) -> None:
    """Wire a durable ledger so run transitions survive a process restart,
    and rehydrate any non-terminal runs it already has on record.

    ``None`` (the default) keeps every existing test's process-local-only
    behavior unchanged. A restored run's ``history`` is a single-element
    placeholder (the ledger persists run state, not the event trace) — an
    accepted degradation, since resume/gate logic keys off ``status``.
    """
    global _ledger
    _ledger = ledger
    if ledger is None:
        return
    restored = 0
    for row in ledger.list_runs(statuses=_RESUMABLE_STATUSES):
        status = RunStatus(row["status"])
        _store.restore(
            Run(
                id=row["id"],
                status=status,
                request=row["request"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                error=row["error"],
                result=row["result"],
                history=[status],
            )
        )
        restored += 1
    logger.info("run_ledger_wired", restored=restored)


#: How long a read waits for the workflow to answer a state query. A query is
#: answered by a worker, so with none running it would otherwise hang the read.
_RECONCILE_TIMEOUT_SECONDS = 5.0

#: Workflow statuses that map one to one onto a run status.
_ENGINE_STATUSES = {
    RunStatus.RUNNING.value,
    RunStatus.AWAITING_APPROVAL.value,
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.REJECTED.value,
}


def _gate_reason_text(state: dict[str, Any]) -> str:
    gate = str(state.get("awaiting_gate") or "")
    reason = str(state.get("gate_reason") or "")
    if gate and reason:
        return f"Gate '{gate}': {reason}"
    return f"Gate '{gate}'" if gate else reason


async def _reconcile_run(run: Run) -> Run:
    """Align a live Temporal run's record with its workflow (FORGE-485).

    The workflow is the authority on where a run is. The gateway's record is a
    cache of it that a restart rebuilds from the ledger, which only remembers
    ``queued``. Left alone, a run parked at a gate reads ``queued`` and its
    approval is refused with a 409, so nobody can answer it.

    Best effort and read-only toward Temporal: if the workflow cannot be asked
    (no worker, engine down) the record is left exactly as it was. Guessing a
    status from silence is how a record ends up confidently wrong.
    """
    if run.is_terminal or not _is_design_flow(run.request):
        return run
    if run.request.get("flow_engine") == FlowEngine.IN_PROCESS.value:
        return run
    if resolve_flow_engine() is not FlowEngine.TEMPORAL:
        return run
    try:
        launcher = await get_flow_launcher()
        state = await asyncio.wait_for(launcher.state(run.id), _RECONCILE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a read must not fail because the engine is away
        logger.info("run_reconcile_skipped", run_id=run.id, error=str(exc))
        return run
    status = str(state.get("status") or "")
    if status not in _ENGINE_STATUSES:
        return run
    reason = _gate_reason_text(state) if status == RunStatus.AWAITING_APPROVAL.value else None
    error = str(state["error"]) if status in {"failed", "rejected"} and state.get("error") else None
    try:
        return _store.reconcile(run.id, RunStatus(status), approval_reason=reason, error=error)
    except RunNotFoundError:
        return run


async def reconcile_live_runs() -> int:
    """Reconcile every non-terminal design-flow run; returns how many changed.

    Run once at start-up so the list and the Approvals page are right before
    anyone opens a single run. Reads reconcile too, so a run the workflow
    reached after this pass is still caught.
    """
    changed = 0
    for run in list(_store.list()):
        if run.is_terminal:
            continue
        before = (run.status, run.approval_reason)
        after = await _reconcile_run(run)
        if (after.status, after.approval_reason) != before:
            changed += 1
    logger.info("runs_reconciled", changed=changed)
    return changed


#: Seconds between background reconcile passes over live Temporal runs.
RECONCILE_INTERVAL_SECONDS = 5.0


async def run_reconcile_loop(interval: float = RECONCILE_INTERVAL_SECONDS) -> None:
    """Keep live runs' records in step with their workflows, indefinitely.

    The workflow runs in another process and cannot call back into this
    one, so the gateway polls. Reads and the approval route reconcile as
    well; this loop is what keeps the record right when nobody is looking,
    which is when a phase's own writes check it.
    """
    while True:
        try:
            await reconcile_live_runs()
        except Exception as exc:  # noqa: BLE001 - the loop must outlive one bad pass
            logger.warning("run_reconcile_pass_failed", error=str(exc))
        await asyncio.sleep(interval)


def reset_run_store() -> None:
    global _store, _stream_manager, _gate_coordinator, _ledger
    _stream_manager = RunStreamManager()
    _gate_coordinator = GateCoordinator()
    _store = InMemoryRunStore(on_transition=_on_transition)
    _ledger = None
    run_change_sets.reset()


def _is_design_flow(request: dict) -> bool:
    """A run is a design flow only when it opts in explicitly.

    Triggered by a ``flow`` id or ``kind == "design_flow"`` — never by a bare
    ``goal`` alone, so plain runs keep their existing create+start semantics.
    """
    return (
        bool(request.get("flow"))
        # FORGE-399: a run started from an approved, edited flow version names
        # the version rather than a template. Without this it would be treated
        # as a plain run: created, started, and never driven by any engine.
        or bool(request.get("flow_version_id"))
        or request.get("kind") == "design_flow"
    )


async def _ensure_run_project(run: Any, project_backend: Any) -> None:
    """Auto-create a bare project for a run that started without one (FORGE-87).

    A run started without a project_id (the CLI's --goal-only path, and
    RunLauncher.start()'s default) left the intent phase with nothing to
    attach its deliverables to -- twin.record_engineering_entity requires
    project_id as a non-null string, and the model's own recovery attempt
    (mcp_project_create) is a requires_approval tool nobody is watching to
    approve in an unattended run, so it always timed out and the flow
    failed at its very first gate. Reproduced live: every hardware_v1 run
    started this way failed within ~50s.

    Auto-creating a bare project from the goal gives the flow somewhere to
    record into -- this is flow-internal infrastructure setup the run needs
    to function, not a user-facing decision, so it bypasses the approval
    gate entirely (a direct backend call, not the gated mcp_project_create
    tool). Mutates ``run.request`` in place, which every later phase/gate
    check reads from the same stored ``Run`` object.
    """
    if run.request.get("project_id"):
        return
    goal = str(run.request.get("goal") or "Untitled design")
    project = await project_backend.create_project(
        name=goal[:80], description=goal if len(goal) > 80 else "", status="draft"
    )
    run.request["project_id"] = project.id
    logger.info("design_flow_project_autocreated", run_id=run.id, project_id=project.id)


def _make_constraints_loader(twin: Any) -> Any:
    """Loader for a project's Constraint nodes (``[]`` when the twin cannot list them)."""

    async def load(project_id: str | None) -> list[Any]:
        lister = getattr(twin, "list_constraints", None)
        if lister is None or not project_id:
            return []
        from uuid import UUID

        return list(await lister(project_id=UUID(project_id)))

    return load


def _make_cad_model_probe(project_backend: Any, twin: Any) -> Any:
    """Whether the project holds a loadable cad_model recorded since ``since_ts``."""
    from api_gateway.runs.gate_eval import ProjectGateEvaluator

    evaluator = ProjectGateEvaluator(project_backend, twin=twin)

    async def probe(project_id: str | None, since_ts: float) -> bool:
        return "cad_model" in await evaluator.present_types(project_id, since_ts)

    return probe


async def build_phase_brain(run_id: str = "worker", flow_id: str | None = None) -> Any:
    """The brain a phase runs on, for whichever engine is driving it.

    Extracted from ``_launch_flow`` (FORGE-401) rather than copied into the
    Temporal worker. Two copies of this routing would diverge, and the
    divergence would show up as "the same flow behaves differently on the
    durable engine" — the most confusing possible bug to inherit from a
    migration whose whole selling point is that nothing else changes.

    The brain is a HybridBrain: deterministic handlers drive the mechanical
    phases (requirements / design / simulation) so their deliverables reliably
    land in the twin, and the ReAct brain handles any other phase.
    """
    from api_gateway.chat.routes import get_mcp_bridge
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.runs.arch_handlers import GoalDrivenArchitectureHandler
    from api_gateway.runs.concept_handlers import GoalDrivenConceptSelectionHandler
    from api_gateway.runs.elec_handlers import GoalDrivenElectronicsHandler
    from api_gateway.runs.flow_brain import ReActPhaseBrain
    from api_gateway.runs.fw_handlers import GoalDrivenFirmwareHandler
    from api_gateway.runs.mech_handlers import (
        GoalDrivenMechanicalHandler,
        HybridBrain,
        MechanicalDesignHandler,
        NativeMechanicalDesignHandler,
        RequirementsHandler,
        SimulationHandler,
    )
    from api_gateway.runs.mfg_handlers import GoalDrivenManufacturingHandler
    from api_gateway.runs.req_handlers import GoalDrivenRequirementsHandler
    from api_gateway.runs.vv_handlers import GoalDrivenVVHandler
    from api_gateway.twin.bom_recorder import make_bom_recorder
    from api_gateway.twin.document_recorder import make_document_recorder
    from api_gateway.twin.geometry_recorder import make_geometry_recorder
    from api_gateway.twin.routes import get_twin

    # FORGE-476: the Temporal worker is a separate process; opening the shared
    # usage store here lets its phases record next to the gateway's.
    ensure_usage_store()
    bridge = get_mcp_bridge()
    project_backend = get_project_backend()
    recorder = make_geometry_recorder(get_twin(), project_backend)
    bom_recorder = make_bom_recorder(get_twin(), project_backend)
    doc_recorder = make_document_recorder(get_twin(), project_backend)
    react = ReActPhaseBrain(mcp_bridge=bridge, session_id=f"flow:{run_id}")

    # Per-flow brain routing:
    #  - design_v1: deterministic quadruped-demo handlers (reliable, hardcoded).
    #  - mech_v1:   goal-driven hybrid — the LLM specs the part, a deterministic
    #               step authors+commits it so the cad_model is always loadable.
    #  - hardware_v1: electronics uses a deterministic handler (guaranteed BOM +
    #               closed power budget); other phases stay native.
    #  - others: the native brain drives every phase.
    if flow_id == "design_v1":
        handlers: dict[str, Any] = {
            "requirements": RequirementsHandler(bridge),
            "design": MechanicalDesignHandler(bridge, recorder),
            "simulation": SimulationHandler(bridge),
        }
    elif flow_id == "mech_v1":
        # FORGE-496: the native brain designs (flow context + FreeCAD tools);
        # the goal-driven single-primitive handler is only the backstop.
        twin = get_twin()
        handlers = {
            "design": NativeMechanicalDesignHandler(
                react,
                GoalDrivenMechanicalHandler(
                    bridge,
                    recorder,
                    constraints_loader=_make_constraints_loader(twin),
                ),
                _make_cad_model_probe(project_backend, twin),
            )
        }
    elif flow_id == "hardware_v1":
        # Deterministic handlers where the native brain is flaky or dishonest:
        # mechanical design (loadable cad_model), electronics (BOM + closed power
        # budget), firmware (pinmap + firmware_source scaffold), and V&V (honest
        # verdict + test_plan, no false compliance). concept_selection is the
        # Decision Agent (FORGE-73, G5): a real trade study (alternatives +
        # rationale) instead of a one-line native-brain decision. Remaining
        # phases stay native (backstop covers their decisions).
        handlers = {
            "requirements": GoalDrivenRequirementsHandler(bridge, doc_recorder),
            "architecture": GoalDrivenArchitectureHandler(bridge, doc_recorder),
            "concept_selection": GoalDrivenConceptSelectionHandler(bridge),
            "design": GoalDrivenMechanicalHandler(bridge, recorder),
            "electronics": GoalDrivenElectronicsHandler(bridge, bom_recorder),
            "firmware": GoalDrivenFirmwareHandler(bridge, doc_recorder),
            "simulation": GoalDrivenVVHandler(bridge, doc_recorder),
            "manufacturing": GoalDrivenManufacturingHandler(bridge, doc_recorder),
        }
    else:
        handlers = {}
    return HybridBrain(handlers=handlers, fallback=react, run_id=run_id)


class _GateCheckers:
    """The three gate evaluations, in one place for both engines."""

    def __init__(self, evaluator: Any, constraints: Any, consistency: Any) -> None:
        self._evaluator = evaluator
        self._constraints = constraints
        self._consistency = consistency

    async def evaluate(self, phase: Any, project_id: str | None) -> Any:
        from orchestrator.design_flow.temporal_flow import GateCheck

        if project_id is None:
            # No project means nothing to evaluate against. Say so rather
            # than returning a clean result nobody looked for.
            return GateCheck(checked=False, constraints_checked=False, reason="run has no project")
        # Over Temporal the phase arrives as a plain dict (the activity payload is
        # untyped), where getattr found nothing: the required list read empty, the
        # check came back ``checked=False`` and an empty gate opened as ready
        # (FORGE-484). Read both shapes.
        if isinstance(phase, dict):
            required = list(phase.get("required_deliverables") or [])
        else:
            required = list(getattr(phase, "required_deliverables", []) or [])
        # ``since_ts=0`` asks "what has this project ever recorded", which is
        # the right question for a durable engine: the phase that produced the
        # deliverable may have run in a different process, hours earlier and
        # on another host, so a window anchored to this evaluation's own start
        # would miss it.
        present = await self._evaluator.present_types(project_id, 0.0)
        missing = [d for d in required if d not in present]

        constraints = await self._constraints.check(project_id)
        return GateCheck(
            ready=not missing,
            checked=bool(required),
            missing=missing,
            present=sorted(present),
            constraints_passed=constraints.passed,
            constraints_checked=constraints.checked,
            violations=list(constraints.violations),
            reason=(
                f"{len(present)} deliverable type(s) recorded; "
                f"{len(constraints.violations)} constraint violation(s)"
                + ("" if constraints.checked else " (constraints not evaluated)")
            ),
        )


async def build_gate_checkers() -> _GateCheckers | None:
    """Gate evaluators bound to the live twin, or ``None`` if unavailable."""
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.runs.gate_eval import (
        ProjectGateEvaluator,
        TwinConsistencyGateChecker,
        TwinConstraintChecker,
    )
    from api_gateway.twin.routes import get_twin

    twin = get_twin()
    if twin is None:
        return None
    backend = get_project_backend()
    return _GateCheckers(
        ProjectGateEvaluator(backend, twin=twin),
        TwinConstraintChecker(twin, backend),
        TwinConsistencyGateChecker(twin),
    )


class FlowVersionNotApprovedError(RuntimeError):
    """A run named a flow version nobody has approved yet (FORGE-399)."""

    def __init__(self, version_id: str, status: str) -> None:
        super().__init__(
            f"flow version '{version_id}' is {status}, not approved. A run cannot start "
            "on a flow a human has not agreed to."
        )


class FlowVersionUnrunnableError(RuntimeError):
    """The in-process engine cannot run this flow version exactly (FORGE-474).

    Raised instead of running something close to it. Substituting the
    template the version came from is the bug this exists to prevent: the run
    would look like the approved flow and do a different one.
    """

    def __init__(self, version_id: str, detail: str) -> None:
        super().__init__(
            f"flow version '{version_id}' cannot be run by the in-process engine: {detail}. "
            "No run was created. The default template was not substituted, because a run "
            "must do exactly the flow a human approved."
        )


def _resolve_in_process_flow(run: Run) -> FlowDefinition:
    """The exact flow an in-process run walks, recorded on the run (FORGE-474).

    A run naming ``flow_version_id`` runs that stored, approved version: its
    frozen content is hash-verified and turned back into a definition, and the
    round trip must reproduce the same hash. Anything else raises, so the
    executor never falls back to the template behind the version.

    Raises :class:`~orchestrator.design_flow.versions.VersionNotFoundError`,
    :class:`FlowVersionNotApprovedError` or :class:`FlowVersionUnrunnableError`
    for a version, and ``KeyError`` for an unknown template id.
    """
    version_id = run.request.get("flow_version_id")
    if version_id:
        version = get_version_store().get(str(version_id))
        if not version.startable:
            raise FlowVersionNotApprovedError(str(version_id), version.status.value)
        frozen = version.frozen
        try:
            frozen.verify()
        except ValueError as exc:
            raise FlowVersionUnrunnableError(str(version_id), str(exc)) from exc
        definition = definition_from_frozen(frozen)
        rebuilt = freeze_flow(
            definition, version=frozen.version, context=frozen.context
        ).content_hash
        if rebuilt != frozen.content_hash:
            raise FlowVersionUnrunnableError(
                str(version_id),
                f"rebuilding it changed its content (approved {frozen.content_hash[:12]}, "
                f"rebuilt {rebuilt[:12]})",
            )
        run.request["flow"] = version.base_template_id
        run.request["flow_template_id"] = version.base_template_id
        run.request["flow_version"] = frozen.version
        run.request["flow_content_hash"] = frozen.content_hash
        run.request["flow_context"] = frozen.context
        return definition

    flow_id = str(run.request.get("flow") or DEFAULT_FLOW_ID)
    definition = get_flow(flow_id)
    try:
        template_version = flow_version(flow_id)
    except KeyError:
        # A flow registered without a template file (test doubles only).
        template_version = "unversioned"
    run.request["flow"] = flow_id
    run.request["flow_template_id"] = flow_id
    run.request["flow_version"] = template_version
    run.request["flow_content_hash"] = freeze_flow(
        definition, version=template_version
    ).content_hash
    return definition


def _build_in_process_executor(brain: Any, project_backend: Any) -> DesignFlowExecutor:
    """The executor and its live gate checks, bound to the gateway's twin."""
    from api_gateway.runs.gate_eval import (
        ProjectGateEvaluator,
        TwinConsistencyGateChecker,
        TwinConstraintChecker,
    )
    from api_gateway.twin.routes import get_twin

    return DesignFlowExecutor(
        store=_store,
        brain=brain,
        coordinator=_gate_coordinator,
        # The twin lets the gate require a *loadable* cad_model, not a bare node.
        gate_evaluator=ProjectGateEvaluator(project_backend, twin=get_twin()),
        # MET-583: constraint state surfaces at every gate; enforce_constraints
        # gates fail-fast on ERROR-severity violations.
        constraint_checker=TwinConstraintChecker(get_twin(), project_backend),
        # FORGE-73: real G3/G4 status surfaces at the two gates that carry a
        # gate_id -- informational only, never fails a gate (no enforce_*
        # flag exists for it; see Gate.gate_id's own docstring for why).
        consistency_gate_checker=TwinConsistencyGateChecker(get_twin()),
        # FORGE-525: the phase's twin writes carry its run and phase, so they
        # are drafts in the run's change set, as on the Temporal worker.
        phase_scope=run_change_sets.phase_scope,
    )


async def _launch_flow(run_id: str) -> None:
    """Spawn the in-process executor for ``run_id`` as a tracked background task.

    The test double (FORGE-401). Durable runs go through Temporal; this exists
    so the flow logic can be exercised without a server, and so a contributor
    with no Docker is not blocked.

    The flow is resolved before anything else happens (FORGE-474), so a
    version this engine cannot run exactly raises here, before a project is
    created or a task is spawned, and the caller can delete the run record.
    """
    from api_gateway.projects.routes import get_project_backend

    run = _store.get(run_id)
    definition = _resolve_in_process_flow(run)
    run.request["flow_engine"] = FlowEngine.IN_PROCESS.value
    project_backend = get_project_backend()
    await _ensure_run_project(run, project_backend)
    hybrid = await build_phase_brain(run_id, run.request.get("flow"))
    executor = _build_in_process_executor(hybrid, project_backend)
    logger.info(
        "design_flow_started_in_process",
        run_id=run_id,
        flow=definition.id,
        version_id=run.request.get("flow_version_id"),
        version=run.request.get("flow_version"),
        content_hash=str(run.request.get("flow_content_hash") or "")[:12],
        phases=[p.id for p in definition.phases],
    )
    task = asyncio.create_task(
        executor.run(run_id, definition, flow_context=str(run.request.get("flow_context") or ""))
    )
    _flow_tasks.add(task)
    task.add_done_callback(_flow_tasks.discard)


async def _start_on_temporal(run_id: str) -> None:
    """Hand the run to the real engine (FORGE-401).

    Raises :class:`TemporalUnavailableError` if there is no engine. The
    caller deletes the run record rather than leaving one that looks started
    and never will be.
    """
    run = _store.get(run_id)

    # FORGE-399: a run may name a stored, approved flow version instead of a
    # template. An unapproved version is refused here rather than at the
    # gate: starting work on a flow nobody agreed to and asking afterwards is
    # the shape this whole epic exists to prevent.
    version_id = run.request.get("flow_version_id")
    if version_id:
        version = get_version_store().get(str(version_id))
        if not version.startable:
            raise FlowVersionNotApprovedError(str(version_id), version.status.value)
        flow_id = version.base_template_id
        frozen = version.frozen
        run.request["flow"] = flow_id
        run.request["flow_template_id"] = flow_id
        run.request["flow_version"] = frozen.version
        run.request["flow_content_hash"] = frozen.content_hash
        launcher = await get_flow_launcher()
        await launcher.require_worker()
        await launcher.start(
            run_id=run_id,
            goal=str(run.request.get("goal") or "").strip(),
            flow=frozen,
            project_id=run.request.get("project_id"),
            session_id=run.request.get("session_id"),
        )
        _metrics().record_design_flow_started(FlowEngine.TEMPORAL.value, flow_id)
        return

    flow_id = run.request.get("flow") or DEFAULT_FLOW_ID
    definition = get_flow(flow_id)

    # FORGE-397: the invariants are server-enforced, which means here -- not
    # only where a flow is authored. A tailored flow reaching this point
    # having skipped its own validation is exactly the case the rules exist
    # for, and refusing costs a 400 rather than a run that cannot pass.
    validate_flow(definition).raise_if_invalid(flow_id)
    run.request["flow_template_id"] = flow_id

    # The version travels with the run, so "which flow did this use" survives
    # the template being edited afterwards.
    frozen = freeze_flow(definition, version=flow_version(flow_id))
    run.request["flow_version"] = frozen.version
    run.request["flow_content_hash"] = frozen.content_hash
    launcher = await get_flow_launcher()
    await launcher.require_worker()
    await launcher.start(
        run_id=run_id,
        goal=str(run.request.get("goal") or "").strip(),
        flow=frozen,
        project_id=run.request.get("project_id"),
        session_id=run.request.get("session_id"),
    )
    _metrics().record_design_flow_started(FlowEngine.TEMPORAL.value, flow_id)


@router.post("", response_model=RunResponse, status_code=201)
async def create_run(body: CreateRunRequest) -> RunResponse:
    run = _store.create(body.request)
    if _is_design_flow(body.request) and body.start:
        # FORGE-471: checked before choosing an engine, not only inside the
        # Temporal path. `flow.start_run` is not held at the call because this
        # refusal is its authorisation, so it has to hold on the in-process
        # engine too, which otherwise never looks at the version.
        version_id = body.request.get("flow_version_id")
        if version_id:
            try:
                version = get_version_store().get(str(version_id))
            except VersionNotFoundError as exc:
                _store.delete(run.id)
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if not version.startable:
                refused = FlowVersionNotApprovedError(str(version_id), version.status.value)
                _store.delete(run.id)
                logger.warning(
                    "design_flow_version_not_approved", run_id=run.id, error=str(refused)
                )
                raise HTTPException(status_code=409, detail=str(refused)) from refused
        engine = resolve_flow_engine()
        # Run status says which engine is driving it (FORGE-474).
        run.request["flow_engine"] = engine.value
        if engine is FlowEngine.TEMPORAL:
            try:
                await _start_on_temporal(run.id)
                # FORGE-485: the workflow is running from here on. Leaving the
                # record `queued` made every write the run's phases attempted
                # get refused ("run is queued, not running").
                _store.start(run.id)
            except FlowVersionNotApprovedError as exc:
                _store.delete(run.id)
                logger.warning("design_flow_version_not_approved", run_id=run.id, error=str(exc))
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except VersionNotFoundError as exc:
                _store.delete(run.id)
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except FlowInvariantError as exc:
                # The flow itself is wrong. Distinct from the engine being
                # down: retrying will never help, and the message already
                # names each rule and phase.
                _store.delete(run.id)
                logger.warning("design_flow_invalid", run_id=run.id, error=str(exc))
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except DesignFlowWorkerUnavailableError as exc:
                # FORGE-475: Temporal answers but nothing polls the queue, so
                # the run would be accepted and never advance. Refuse it.
                _store.delete(run.id)
                logger.error(
                    "design_flow_worker_unavailable", queue=exc.task_queue, reason=exc.reason
                )
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except TemporalUnavailableError as exc:
                # No run is left behind. A record sitting in `queued` that
                # nothing will ever pick up is worse than no record: it reads
                # as "starting" to everyone looking at the list, and there is
                # nothing to notice that it never moves.
                _store.delete(run.id)
                _metrics().record_design_flow_engine_unavailable(temporal_target())
                logger.error("design_flow_engine_unavailable", error=str(exc))
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        else:
            # The in-process double. `resolve_flow_engine` has already warned
            # that runs started this way are not durable. It runs the exact
            # approved version or refuses (FORGE-474); it never substitutes
            # the template a version came from.
            try:
                await _launch_flow(run.id)
            except VersionNotFoundError as exc:
                _store.delete(run.id)
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except FlowVersionNotApprovedError as exc:
                _store.delete(run.id)
                logger.warning("design_flow_version_not_approved", run_id=run.id, error=str(exc))
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except FlowVersionUnrunnableError as exc:
                _store.delete(run.id)
                logger.error("design_flow_version_unrunnable", run_id=run.id, error=str(exc))
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except KeyError as exc:
                _store.delete(run.id)
                raise HTTPException(status_code=400, detail=str(exc.args[0])) from exc
        logger.info(
            "run_api_created",
            run_id=run.id,
            started=True,
            kind="design_flow",
            engine=engine.value,
        )
    elif body.start:
        run = _store.start(run.id)
        logger.info("run_api_created", run_id=run.id, started=True, kind="plain")
    else:
        logger.info("run_api_created", run_id=run.id, started=False)
    return RunResponse.from_run(_store.get(run.id))


@router.get("", response_model=RunListResponse)
async def list_runs(project_id: str | None = None) -> RunListResponse:
    """Every run, or one project's.

    Unfiltered before: `/runs` showed every project's work in one list, and
    the project a run belonged to was only inside its request blob. Passing
    ``project_id`` scopes it; the response says how many runs were left out
    for having no project, so they do not simply vanish.
    """
    for run in list(_store.list()):
        await _reconcile_run(run)
    runs, unscoped = filter_by_project(list(_store.list()), project_id)
    return RunListResponse(runs=[RunResponse.from_run(r) for r in runs], unscoped_count=unscoped)


@router.get("/usage/summary")
def get_usage_summary(window_hours: float = 24.0) -> dict[str, Any]:
    """LLM tokens and cost across all runs for the trailing window (FORGE-476)."""
    return usage_report(window_hours * 3600.0)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    try:
        response = RunResponse.from_run(await _reconcile_run(_store.get(run_id)))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    response.usage = run_usage(run_id)
    return response


class FlowPhaseState(BaseModel):
    """One phase, as the live run view draws it."""

    id: str
    title: str
    status: str
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    gate: str | None = None
    disciplines: list[str] = Field(default_factory=list)
    #: Tokens and cost this phase spent (FORGE-476); ``None`` if none recorded.
    usage: dict[str, Any] | None = None


class FlowRunState(BaseModel):
    """Live state of a design-flow run (FORGE-396).

    Read from the workflow itself rather than a cache of it. A projection that
    can be stale is a live view that is sometimes wrong, and nothing on the
    page would say which.
    """

    runId: str  # noqa: N815
    status: str
    currentPhase: str | None = None  # noqa: N815
    awaitingGate: str | None = None  # noqa: N815
    phases: list[FlowPhaseState] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    #: True when the state came from the engine. False means the engine could
    #: not be asked -- rendered as "unknown", never as "nothing is happening".
    live: bool = True
    detail: str = ""
    #: Run-wide token and cost totals, with per-phase, per-role and per-model
    #: breakdowns (FORGE-476).
    usage: dict[str, Any] | None = None
    #: The flow this run was frozen on (FORGE-475), read from the run record so
    #: it is present even when the engine cannot be queried.
    flow: str | None = None
    flowVersionId: str | None = None  # noqa: N815
    flowVersion: str | None = None  # noqa: N815
    flowContentHash: str | None = None  # noqa: N815
    #: Why the run failed, when the engine reports it did.
    error: str | None = None
    #: FORGE-495: which attempt of the current phase is running (1 = first), how
    #: many retries it has left, and whether the open gate can be approved. A
    #: gate that is not ready (``gateReady`` false) takes retry or reject only.
    attempt: int = 1
    retriesLeft: int | None = None  # noqa: N815
    gateReady: bool = True  # noqa: N815
    gateFindings: list[str] = Field(default_factory=list)  # noqa: N815
    #: FORGE-500: how many times the run has been sent back to an earlier phase,
    #: the per-run cap, and how many are left.
    reworkCycles: int = 0  # noqa: N815
    maxReworkCycles: int | None = None  # noqa: N815
    reworksLeft: int | None = None  # noqa: N815


def _frozen_identity(run: Run) -> dict[str, Any]:
    """The run's own frozen version, from its record."""
    request = run.request
    return {
        "flow": request.get("flow"),
        "flowVersionId": request.get("flow_version_id"),
        "flowVersion": request.get("flow_version"),
        "flowContentHash": request.get("flow_content_hash"),
    }


def _run_definition(run: Run) -> FlowDefinition | None:
    """The phases a run was started on, for display.

    A run on a stored version shows that version's phases (FORGE-474), not
    the template it descends from: a dropped phase must not reappear here as
    ``pending``.

    ``None`` when the run names a version that cannot be read back (FORGE-475):
    showing the base template instead reads as "the wrong flow is running".
    """
    version_id = run.request.get("flow_version_id")
    if version_id:
        try:
            return definition_from_frozen(get_version_store().get(str(version_id)).frozen)
        except VersionNotFoundError:
            return None
    flow_id = run.request.get("flow") or DEFAULT_FLOW_ID
    try:
        return get_flow(str(flow_id))
    except KeyError:
        return get_flow(DEFAULT_FLOW_ID)


@router.get("/{run_id}/flow-state", response_model=FlowRunState)
async def get_flow_state(run_id: str) -> FlowRunState:
    """Phase-by-phase state of a design-flow run.

    Queries the Temporal workflow. A workflow query is answered by a *worker*,
    so with none running there is nobody to answer -- which is reported as
    ``live: false`` with a reason rather than as an empty flow, because an
    empty flow and a flow nobody can see render identically and mean opposite
    things.
    """
    try:
        run = _store.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc

    definition = _run_definition(run)
    ordered = list(definition.phases) if definition is not None else []
    identity = _frozen_identity(run)
    usage = run_usage(run_id)
    by_phase: dict[str, Any] = (usage or {}).get("by_phase", {})

    if not _is_design_flow(run.request):
        return FlowRunState(
            runId=run_id,
            status=str(run.status),
            live=False,
            detail="this run is not a design flow, so it has no phases",
            **identity,
        )

    try:
        launcher = await get_flow_launcher()
        state = await launcher.state(run_id)
        events = await launcher.events(run_id)
    except Exception as exc:  # noqa: BLE001 — a view reports, it does not raise
        logger.info("flow_state_unavailable", run_id=run_id, error=str(exc))
        return FlowRunState(
            runId=run_id,
            status=str(run.status),
            phases=[
                FlowPhaseState(
                    id=p.id,
                    title=p.title,
                    status="unknown",
                    gate=p.gate.name if p.gate else None,
                    disciplines=list(p.disciplines),
                    usage=by_phase.get(p.id),
                )
                for p in ordered
            ],
            usage=usage,
            live=False,
            detail=(
                f"the workflow could not be queried ({exc}). A query is answered by a "
                "worker, so this usually means the design-flow worker is not running. "
                "Phase status is unknown, not idle."
                + (
                    ""
                    if definition is not None
                    else f" The run's frozen version {identity['flowVersionId']} could not "
                    "be read back, so no phases are listed."
                )
            ),
            **identity,
        )

    done = {entry["phase"]: entry for entry in state.get("completed", [])}
    current = state.get("current_phase")
    awaiting = state.get("awaiting_gate")
    run_state = str(state.get("status") or "")
    run_error = state.get("error") or None

    phases: list[FlowPhaseState] = []
    for phase in ordered:
        finished = done.get(phase.id)
        if finished is not None:
            status = "passed"
        elif phase.id == current and run_state == "failed":
            status = "failed"
        elif phase.id == current and awaiting:
            status = "awaiting_gate"
        elif phase.id == current:
            status = "running"
        else:
            status = "pending"
        phases.append(
            FlowPhaseState(
                id=phase.id,
                title=phase.title,
                status=status,
                summary=(
                    str(run_error)
                    if status == "failed" and run_error
                    else str((finished or {}).get("summary") or "")
                ),
                artifacts=list((finished or {}).get("artifacts") or []),
                gate=phase.gate.name if phase.gate else None,
                disciplines=list(phase.disciplines),
                usage=by_phase.get(phase.id),
            )
        )

    return FlowRunState(
        runId=run_id,
        status=str(state.get("status") or run.status),
        currentPhase=current,
        awaitingGate=awaiting,
        phases=phases,
        events=events,
        live=True,
        usage=usage,
        error=str(run_error) if run_error else None,
        attempt=int(state.get("attempt") or 1),
        retriesLeft=state.get("retries_left"),
        gateReady=bool(state.get("gate_ready", True)),
        gateFindings=[str(f) for f in state.get("gate_findings") or []],
        reworkCycles=int(state.get("rework_cycles") or 0),
        maxReworkCycles=state.get("max_rework_cycles"),
        reworksLeft=state.get("reworks_left"),
        **identity,
    )


@router.get("/{run_id}/events")
def stream_run_events(run_id: str) -> StreamingResponse:
    """SSE stream of a run's status transitions until it reaches a terminal state."""
    try:
        snapshot = _store.get(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    return StreamingResponse(
        run_event_stream(run_id, snapshot, _stream_manager),
        media_type="text/event-stream",
    )


@router.websocket("/{run_id}/ws")
async def stream_run_ws(websocket: WebSocket, run_id: str) -> None:
    """WebSocket stream of a run's status transitions (10 Hz-friendly, per MET-524)."""
    await websocket.accept()
    try:
        snapshot = _store.get(run_id)
    except RunNotFoundError:
        await websocket.close(code=4404)
        return
    try:
        await run_ws_loop(websocket.send_json, run_id, snapshot, _stream_manager)
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("run_ws_disconnected", run_id=run_id)


class GateOpenedRequest(BaseModel):
    gate: str = Field(min_length=1)
    reason: str = ""


@router.post("/{run_id}/gate-opened", response_model=RunResponse)
async def gate_opened(run_id: str, body: GateOpenedRequest, request: Request) -> RunResponse:
    """Record that a run's workflow has opened a gate (FORGE-489).

    Called by the design-flow worker's announcer. The workflow is the
    authority on whether a gate is open, so this first re-reads it
    (``_reconcile_run``). Only when the workflow cannot be asked does it fall
    back to the worker's word. Either way it moves the *record* to
    ``awaiting_approval``, which is what lists the run on the Approvals page
    and publishes the change on its SSE stream. It never approves: a decision
    still has to come through ``/approval``, and the workflow ignores one for
    a gate that is not open.
    """
    try:
        run = await _reconcile_run(_store.get(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    if run.status is not RunStatus.AWAITING_APPROVAL and not run.is_terminal:
        reason = f"Gate '{body.gate}': {body.reason}" if body.reason else f"Gate '{body.gate}'"
        run = _store.reconcile(run_id, RunStatus.AWAITING_APPROVAL, approval_reason=reason)
    await _note_gate_in_session(request, run, body.gate)
    logger.info("design_flow_gate_recorded", run_id=run_id, gate=body.gate, status=run.status.value)
    return RunResponse.from_run(run)


async def _note_gate_in_session(request: Request, run: Run, gate: str) -> None:
    """Tell the run's caller session that it is waiting, when it has one.

    Best effort: the Approvals page is the primary channel. There is no MCP
    ``resources/updated`` push channel in the plugin yet, so the session event
    (and ``flow.status`` ``awaitingGate``) is what a caller can see.
    """
    session_id = run.request.get("session_id")
    store = getattr(request.app.state, "agent_session_store", None)
    if not session_id or store is None:
        return
    try:
        await store.append_event(
            str(session_id),
            type="decision",
            message=f"Run {run.id} is waiting at gate '{gate}' for approval",
            data={"run_id": run.id, "gate": gate, "awaiting_approval": True},
        )
    except Exception as exc:  # noqa: BLE001 - never fail the announcement over a session note
        logger.info("design_flow_gate_session_note_skipped", run_id=run.id, error=str(exc))


WORKFLOW_MISSING_DETAIL = (
    "the run's workflow no longer exists; it cannot continue; reject it instead"
)


async def _refuse_undeliverable_decision(
    run: Run, decision: ApprovalDecision, to_phase: str = ""
) -> None:
    """409 a decision the open gate cannot take (FORGE-495).

    A gate that is not ready (missing deliverables, an ungrounded reply,
    constraint violations) takes retry or reject, never approve; and a retry
    past the per-phase cap would only end the run. Refusing here, before the
    record moves, keeps the run parked and answerable. Best effort: when the
    gate's state cannot be read, the workflow's own guard still holds.
    """
    if decision in (ApprovalDecision.RETRY, ApprovalDecision.REWORK) and not _is_design_flow(
        run.request
    ):
        raise HTTPException(
            status_code=422,
            detail=f"'{decision.value}' re-runs design-flow phases; this run is not one",
        )
    definition = _run_definition(run) if decision is ApprovalDecision.REWORK else None
    if decision is ApprovalDecision.REWORK:
        # Without the run's own phases there is nothing to validate against; the
        # engine's check still applies.
        known = [p.id for p in definition.phases] if definition is not None else [to_phase]
        error = rework_target_error(known, None, to_phase)
        if error is not None:
            raise HTTPException(status_code=422, detail=error)
    if decision is ApprovalDecision.REJECT or not _is_design_flow(run.request):
        return
    gate: dict[str, Any] | None = None
    if run.request.get("flow_engine") == FlowEngine.IN_PROCESS.value:
        gate = _gate_coordinator.gate_state(run.id)  # type: ignore[assignment]
    elif resolve_flow_engine() is FlowEngine.TEMPORAL:
        try:
            launcher = await get_flow_launcher()
            state = await asyncio.wait_for(launcher.state(run.id), _RECONCILE_TIMEOUT_SECONDS)
            gate = {
                "ready": state.get("gate_ready", True),
                "retries_left": state.get("retries_left"),
                "phase": state.get("current_phase"),
                "reworks_left": state.get("reworks_left"),
            }
        except WorkflowNotFoundError as exc:
            # FORGE-516: nothing can resume this run, so only reject is honest.
            # Refused before the store moves, or the saved decision would claim
            # a run was resumed that never will be.
            logger.info("design_flow_gate_workflow_missing", run_id=run.id, error=str(exc))
            raise HTTPException(status_code=409, detail=WORKFLOW_MISSING_DETAIL) from exc
        except Exception as exc:  # noqa: BLE001 - the workflow's own guard still applies
            logger.info("approval_gate_state_unavailable", run_id=run.id, error=str(exc))
    if gate is None:
        return
    if decision is ApprovalDecision.REWORK:
        phase_ids = [p.id for p in definition.phases] if definition is not None else []
        current = gate.get("phase")
        if phase_ids:
            error = rework_target_error(phase_ids, str(current) if current else None, to_phase)
            if error is not None:
                raise HTTPException(status_code=422, detail=error)
        reworks_left = gate.get("reworks_left")
        if isinstance(reworks_left, int) and reworks_left <= 0:
            raise HTTPException(
                status_code=409,
                detail="This run has used all its rework cycles. Approve (if the gate is ready), "
                "retry the phase or reject.",
            )
        return
    if decision is ApprovalDecision.APPROVE and gate.get("ready") is False:
        raise HTTPException(
            status_code=409,
            detail="This gate is not ready (see the findings on the run). "
            "Retry the phase or reject; it cannot be approved.",
        )
    left = gate.get("retries_left")
    if decision is ApprovalDecision.RETRY and isinstance(left, int) and left <= 0:
        raise HTTPException(
            status_code=409,
            detail="This phase has used all its retries. Approve (if the gate is ready) or reject.",
        )


async def gate_snapshot(run: Run) -> dict[str, Any] | None:
    """What the gate a design-flow run is parked at reports about itself (FORGE-507).

    ``ready``, ``retries_left``, ``reworks_left``, ``phase`` and ``findings``
    (structured text the workflow built; empty on the in-process engine, whose
    findings only exist inside the approval reason). ``None`` when the run is
    not a design flow or the gate's state cannot be read: the caller then falls
    back to the reason text and the decision routes' own guards.
    """
    if not _is_design_flow(run.request):
        return None
    if run.request.get("flow_engine") == FlowEngine.IN_PROCESS.value:
        state = _gate_coordinator.gate_state(run.id)
        return None if state is None else {**state, "findings": []}
    if resolve_flow_engine() is not FlowEngine.TEMPORAL:
        return None
    try:
        launcher = await get_flow_launcher()
        state = await asyncio.wait_for(launcher.state(run.id), _RECONCILE_TIMEOUT_SECONDS)
    except WorkflowNotFoundError:
        return {"workflow_missing": True, "findings": []}
    except Exception as exc:  # noqa: BLE001 - a read must not fail because the engine is away
        logger.info("approval_gate_snapshot_unavailable", run_id=run.id, error=str(exc))
        return None
    return {
        "ready": state.get("gate_ready", True),
        "retries_left": state.get("retries_left"),
        "phase": state.get("current_phase"),
        "reworks_left": state.get("reworks_left"),
        "attempt": state.get("attempt"),
        "findings": [str(f) for f in state.get("gate_findings") or []],
    }


def run_phase_ids(run: Run) -> list[str]:
    """The ids of the phases of the flow ``run`` is on, in order (empty if unreadable)."""
    definition = _run_definition(run)
    return [p.id for p in definition.phases] if definition is not None else []


def is_design_flow_run(run: Run) -> bool:
    return _is_design_flow(run.request)


@router.post("/{run_id}/approval", response_model=RunResponse)
async def submit_approval(run_id: str, body: ApprovalRequest, request: Request) -> RunResponse:
    """Answer the gate this run is parked at.

    Async so the store transition — which resolves the in-process executor's
    gate future via the coordinator — runs on the event-loop thread (future
    resolution is not thread-safe from FastAPI's sync worker pool).

    On Temporal the decision is also *signalled* into the workflow, which is
    what actually resumes it (FORGE-401). The store transition stays, because
    the run list, the SSE stream and the ledger all read from it.

    The deciding human comes from the request, never the body (FORGE-393).
    """
    run = await decide_run_gate(
        run_id,
        ApprovalDecision(body.decision),
        approver_from_request(request),
        reason=body.reason,
        to_phase=body.to_phase,
    )
    return RunResponse.from_run(run)


async def _open_gate_name(run: Run) -> str | None:
    """The name of the gate ``run`` is parked at, from its own flow definition."""
    snapshot = await gate_snapshot(run)
    phase_id = (snapshot or {}).get("phase")
    definition = _run_definition(run)
    if not phase_id or definition is None:
        return None
    for phase in definition.phases:
        if phase.id == phase_id and phase.gate is not None:
            gate_id = getattr(phase.gate, "gate_id", None)
            return f"{phase.gate.name} ({gate_id})" if gate_id else phase.gate.name
    return None


async def _settle_change_set(
    run: Run, decision: ApprovalDecision, approver: Approver, reason: str
) -> None:
    """Commit (approve) or close (reject, retry, rework) the run's drafts (FORGE-525).

    An approval whose drafts were based on revisions that moved since is
    refused here with a 409 and the rebase message, and the run stays parked.
    """
    from twin_core.items import ChangeSetCommitError, ChangeSetConflictError

    if decision is not ApprovalDecision.APPROVE:
        await run_change_sets.close_for_decision(run, decision, reason)
        return
    gate = await _open_gate_name(run)
    try:
        committed = await run_change_sets.commit_for_approval(
            run, gate=gate, decided_by=approver.label, reason=reason
        )
    except ChangeSetConflictError as exc:
        logger.warning("design_flow_approval_refused_conflict", run_id=run.id, gate=gate)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ChangeSetCommitError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if committed and committed.get("items"):
        logger.info(
            "design_flow_change_set_committed", run_id=run.id, gate=gate, items=committed["items"]
        )


async def decide_run_gate(
    run_id: str,
    decision: ApprovalDecision,
    approver: Approver,
    *,
    reason: str = "",
    to_phase: str = "",
) -> Run:
    """Answer a design-flow gate. Shared by ``/v1/runs`` and ``/v1/approvals``.

    Every refusal (404, 409, 422, 503) is raised as an ``HTTPException`` so the
    two surfaces enforce identical rules from one body of code (FORGE-507).
    """
    # FORGE-485: the workflow decides whether a gate is open, not the local
    # record. After a restart that record says `queued` while the workflow
    # waits, and refusing on it leaves a gate nobody can answer.
    try:
        reconciled = await _reconcile_run(_store.get(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    await _refuse_undeliverable_decision(reconciled, decision, to_phase)
    if _is_design_flow(reconciled.request):
        # FORGE-525: the run's drafts follow the decision, before the run moves
        # on (a resumed phase must not write into a change set being closed).
        await _settle_change_set(reconciled, decision, approver, reason)
    if decision is ApprovalDecision.RETRY:
        # A retry after a refused approval tells the phase what to rebase on.
        reason = run_change_sets.retry_note(run_id, reason)
        _gate_coordinator.note_retry(run_id, reason)
    elif decision is ApprovalDecision.REWORK:
        _gate_coordinator.note_rework(run_id, to_phase, reason)
    run_change_sets.begin_decision(run_id)
    try:
        run = _store.submit_approval(
            run_id,
            decision,
            approved_by=approver.label,
            approver_verified=approver.verified,
        )
    except RunNotFoundError as exc:
        _gate_coordinator.take_retry_reason(run_id)
        _gate_coordinator.take_rework(run_id)
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    except InvalidTransition as exc:
        _gate_coordinator.take_retry_reason(run_id)
        _gate_coordinator.take_rework(run_id)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        run_change_sets.end_decision(run_id)

    approved = decision is ApprovalDecision.APPROVE
    if _is_design_flow(run.request) and resolve_flow_engine() is FlowEngine.TEMPORAL:
        try:
            launcher = await get_flow_launcher()
            extra: dict[str, Any] = (
                {"rework_to": to_phase} if decision is ApprovalDecision.REWORK else {}
            )
            await launcher.answer_gate(
                run_id,
                approved=approved,
                decided_by=approver.label,
                comment=reason,
                retry=decision is ApprovalDecision.RETRY,
                **extra,
            )
        except WorkflowNotFoundError as exc:
            # FORGE-516: only reject reaches here (the others were refused
            # above). The decision is saved and the run is closed; there is no
            # workflow left to tell, which is not a failure of the request.
            logger.warning(
                "design_flow_gate_workflow_missing",
                run_id=run_id,
                decision=decision.value,
                error=str(exc),
            )
            if decision is not ApprovalDecision.REJECT:
                raise HTTPException(status_code=409, detail=WORKFLOW_MISSING_DETAIL) from exc
        except TemporalUnavailableError as exc:
            # The store already moved, but the run itself did not hear the
            # decision. Saying so is the only honest answer: reporting 200
            # would leave a reviewer believing they had unblocked a run that
            # is still sitting at its gate.
            _metrics().record_design_flow_engine_unavailable(temporal_target())
            logger.error("design_flow_approval_not_delivered", run_id=run_id, error=str(exc))
            raise HTTPException(
                status_code=503,
                detail=(
                    f"The decision was recorded but could not be delivered to the run: "
                    f"{exc}. The run is still waiting at its gate."
                ),
            ) from exc

    _metrics().record_design_flow_gate(
        str(run.request.get("flow") or DEFAULT_FLOW_ID),
        {"approve": "approved", "reject": "rejected", "retry": "retried", "rework": "reworked"}[
            decision.value
        ],
    )
    logger.info(
        "run_api_approval",
        run_id=run_id,
        decision=decision.value,
        decided_by=approver.actor_id,
        approver_verified=approver.verified,
    )
    return run
