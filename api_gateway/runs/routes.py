"""Runs REST endpoints for the MetaForge Gateway (MET-547, Phase 1).

The OpenAI-compatible Runs API surface over the harness run lifecycle:

* ``POST   /v1/runs``               create a run (optionally start it)
* ``GET    /v1/runs``               list runs
* ``GET    /v1/runs/{id}``          fetch one run
* ``POST   /v1/runs/{id}/approval`` approve or reject a paused run

The run store is process-local for now (mirrors the chat backend pattern);
persistence lands in Phase 4. Domain errors map to clean HTTP status:
:class:`RunNotFoundError` -> 404, :class:`InvalidTransition` -> 409.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from api_gateway.runs.schemas import (
    ApprovalRequest,
    CreateRunRequest,
    RunListResponse,
    RunResponse,
)
from api_gateway.runs.streaming import RunStreamManager, run_event_stream, run_ws_loop
from orchestrator.design_flow.executor import DesignFlowExecutor, GateCoordinator
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    Run,
    RunNotFoundError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/runs", tags=["runs"])

# Process-local store + SSE manager + gate coordinator (mirrors the chat backend
# pattern). The store notifies BOTH the SSE stream and the gate coordinator so a
# design-flow run paused at a gate resumes when POST /approval fires a
# transition. reset_run_store() rewires all three for tests.
_stream_manager = RunStreamManager()
_gate_coordinator = GateCoordinator()


def _on_transition(run: Run) -> None:
    _stream_manager.publish(run)
    _gate_coordinator.on_transition(run)


_store = InMemoryRunStore(on_transition=_on_transition)

# Background executor tasks, kept referenced so they aren't GC'd mid-run.
_flow_tasks: set[asyncio.Task[None]] = set()


def get_run_store() -> InMemoryRunStore:
    return _store


def get_gate_coordinator() -> GateCoordinator:
    return _gate_coordinator


def init_run_store(store: InMemoryRunStore) -> None:
    global _store
    store.set_on_transition(_on_transition)
    _store = store


def reset_run_store() -> None:
    global _store, _stream_manager, _gate_coordinator
    _stream_manager = RunStreamManager()
    _gate_coordinator = GateCoordinator()
    _store = InMemoryRunStore(on_transition=_on_transition)


def _is_design_flow(request: dict) -> bool:
    """A run is a design flow only when it opts in explicitly.

    Triggered by a ``flow`` id or ``kind == "design_flow"`` — never by a bare
    ``goal`` alone, so plain runs keep their existing create+start semantics.
    """
    return bool(request.get("flow")) or request.get("kind") == "design_flow"


def _launch_flow(run_id: str) -> None:
    """Spawn the design-flow executor for ``run_id`` as a tracked background task.

    The brain is a HybridBrain: deterministic handlers drive the mechanical
    phases (requirements / design / simulation) so their deliverables reliably
    land in the twin, and the ReAct brain handles any other phase.
    """
    from api_gateway.chat.routes import get_mcp_bridge
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.runs.flow_brain import ReActPhaseBrain
    from api_gateway.runs.gate_eval import ProjectGateEvaluator
    from api_gateway.runs.mech_handlers import (
        HybridBrain,
        MechanicalDesignHandler,
        RequirementsHandler,
        SimulationHandler,
    )
    from api_gateway.twin.geometry_recorder import make_geometry_recorder
    from api_gateway.twin.routes import get_twin

    bridge = get_mcp_bridge()
    project_backend = get_project_backend()
    recorder = make_geometry_recorder(get_twin(), project_backend)
    hybrid = HybridBrain(
        handlers={
            "requirements": RequirementsHandler(bridge),
            "design": MechanicalDesignHandler(bridge, recorder),
            "simulation": SimulationHandler(bridge),
        },
        fallback=ReActPhaseBrain(mcp_bridge=bridge, session_id=f"flow:{run_id}"),
    )
    executor = DesignFlowExecutor(
        store=_store,
        brain=hybrid,
        coordinator=_gate_coordinator,
        gate_evaluator=ProjectGateEvaluator(project_backend),
    )
    task = asyncio.create_task(executor.run(run_id))
    _flow_tasks.add(task)
    task.add_done_callback(_flow_tasks.discard)


@router.post("", response_model=RunResponse, status_code=201)
async def create_run(body: CreateRunRequest) -> RunResponse:
    run = _store.create(body.request)
    if _is_design_flow(body.request) and body.start:
        # The executor owns the lifecycle (start -> phases -> gates -> terminal).
        _launch_flow(run.id)
        logger.info("run_api_created", run_id=run.id, started=True, kind="design_flow")
    elif body.start:
        run = _store.start(run.id)
        logger.info("run_api_created", run_id=run.id, started=True, kind="plain")
    else:
        logger.info("run_api_created", run_id=run.id, started=False)
    return RunResponse.from_run(_store.get(run.id))


@router.get("", response_model=RunListResponse)
def list_runs() -> RunListResponse:
    return RunListResponse(runs=[RunResponse.from_run(r) for r in _store.list()])


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> RunResponse:
    try:
        return RunResponse.from_run(_store.get(run_id))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc


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


@router.post("/{run_id}/approval", response_model=RunResponse)
async def submit_approval(run_id: str, body: ApprovalRequest) -> RunResponse:
    # Async so the store transition — which resolves the design-flow gate's
    # asyncio.Future via the coordinator — runs on the event-loop thread (future
    # resolution is not thread-safe from FastAPI's sync worker pool).
    try:
        run = _store.submit_approval(run_id, ApprovalDecision(body.decision))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("run_api_approval", run_id=run_id, decision=body.decision)
    return RunResponse.from_run(run)
