"""Assistant API routes for IDE integration.

Provides REST and WebSocket endpoints that IDE assistants use to
submit agent requests, manage design-change proposals (approve/reject),
and receive real-time events via SSE or WebSocket.

Endpoints live under ``/v1/assistant``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.schemas import (
    ApprovalDecision,
    AssistantRequest,
    AssistantResponse,
    DesignChangeProposal,
    ProposalListResponse,
    RunStatusResponse,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level workflow & router
# ---------------------------------------------------------------------------

workflow = ApprovalWorkflow()

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])


# ---------------------------------------------------------------------------
# Request submission
# ---------------------------------------------------------------------------


@router.post("/request", response_model=AssistantResponse)
async def submit_request(body: AssistantRequest, request: Request) -> AssistantResponse:
    """Submit a request to an agent via the orchestrator.

    Looks up the workflow definition for ``body.action``, creates a
    WorkflowRun, and dispatches it through the Scheduler.
    """
    logger.info(
        "assistant_request",
        action=body.action,
        target_id=str(body.target_id),
        session_id=str(body.session_id),
    )

    # Resolve orchestrator components from app.state
    action_workflows: dict = getattr(request.app.state, "action_workflows", {})
    workflow_engine = getattr(request.app.state, "workflow_engine", None)
    scheduler = getattr(request.app.state, "scheduler", None)

    if workflow_engine is None or scheduler is None:
        # Orchestrator not wired — fall back to placeholder
        return AssistantResponse(
            request_id=uuid4(),
            status="accepted",
            result={
                "action": body.action,
                "target_id": str(body.target_id),
                "session_id": str(body.session_id),
            },
        )

    # Look up workflow definition by action name
    defn = action_workflows.get(body.action)
    if defn is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: '{body.action}'. "
            f"Available: {', '.join(sorted(action_workflows.keys()))}",
        )

    # Inject work_product_id and parameters into each step
    for step in defn.steps:
        step.parameters = {
            **step.parameters,
            "work_product_id": str(body.target_id),
            **body.parameters,
        }

    # Build dependency graph for this specific workflow
    from orchestrator.dependency_engine import DependencyGraph

    dep_graph = DependencyGraph(defn)
    dep_graph.validate()
    scheduler._dep_graph = dep_graph

    # Start the workflow run
    run = await workflow_engine.start_run(
        workflow_id=defn.id,
        branch=body.parameters.get("branch", "main"),
        metadata={
            "action": body.action,
            "target_id": str(body.target_id),
            "session_id": str(body.session_id),
        },
    )

    # Schedule all initially-ready steps
    await scheduler.execute_run(run)

    return AssistantResponse(
        request_id=uuid4(),
        status="running",
        result={
            "run_id": run.id,
            "action": body.action,
            "target_id": str(body.target_id),
            "session_id": str(body.session_id),
        },
    )


@router.get("/request/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str, request: Request) -> RunStatusResponse:
    """Poll the status of a workflow run."""
    workflow_engine = getattr(request.app.state, "workflow_engine", None)
    if workflow_engine is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    run = await workflow_engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    steps = {}
    for step_id, sr in run.step_results.items():
        steps[step_id] = {
            "status": sr.status,
            "agent_code": sr.agent_code,
            "task_type": sr.task_type,
            "result": sr.task_result,
            "error": sr.error,
            "started_at": sr.started_at,
            "completed_at": sr.completed_at,
        }

    return RunStatusResponse(
        run_id=run.id,
        status=run.status,
        steps=steps,
        completed_at=run.completed_at,
    )


# ---------------------------------------------------------------------------
# Proposal endpoints
# ---------------------------------------------------------------------------


@router.get("/proposals", response_model=ProposalListResponse)
async def list_proposals(
    session_id: UUID | None = None,
) -> ProposalListResponse:
    """List pending design-change proposals, optionally filtered by session."""
    proposals = workflow.get_pending_proposals(session_id=session_id)
    return ProposalListResponse(proposals=proposals, total=len(proposals))


@router.get("/proposals/{change_id}", response_model=DesignChangeProposal)
async def get_proposal(change_id: UUID) -> DesignChangeProposal:
    """Return a single design-change proposal."""
    proposal = workflow.get_proposal(change_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


@router.post("/proposals/{change_id}/decide", response_model=DesignChangeProposal)
async def decide_proposal(change_id: UUID, body: ApprovalDecision) -> DesignChangeProposal:
    """Approve or reject a pending design-change proposal."""
    proposal = await workflow.decide(
        change_id=change_id,
        decision=body.decision,
        reason=body.reason,
        reviewer=body.reviewer,
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


# ---------------------------------------------------------------------------
# Server-Sent Events (SSE) endpoint
# ---------------------------------------------------------------------------


async def _event_generator(
    session_id: UUID,
    wf: ApprovalWorkflow,
) -> asyncio.AsyncIterator[str]:  # type: ignore[override]
    """Yield SSE-formatted events for a given session."""
    queue = wf.subscribe(session_id)
    try:
        while True:
            event = await queue.get()
            data = event.model_dump_json()
            yield f"data: {data}\n\n"
    except asyncio.CancelledError:
        wf.unsubscribe(session_id, queue)
        raise


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: UUID) -> StreamingResponse:
    """SSE endpoint — streams real-time events for *session_id*."""
    return StreamingResponse(
        _event_generator(session_id, workflow),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: UUID) -> None:
    """Bidirectional WebSocket for IDE assistants.

    Currently pushes events to the client.  Client messages are logged
    but not yet acted upon (future: inline approval from IDE).
    """
    await websocket.accept()
    queue = workflow.subscribe(session_id)
    logger.info("ws_connected", session_id=str(session_id))

    try:
        # Run two tasks concurrently: sending events and receiving messages
        send_task = asyncio.create_task(_ws_sender(websocket, queue))
        recv_task = asyncio.create_task(_ws_receiver(websocket, session_id))
        done, pending = await asyncio.wait(
            {send_task, recv_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        logger.info("ws_disconnected", session_id=str(session_id))
    finally:
        workflow.unsubscribe(session_id, queue)


async def _ws_sender(
    websocket: WebSocket,
    queue: asyncio.Queue,
) -> None:
    """Forward events from the queue to the WebSocket client."""
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(event.model_dump_json())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass


async def _ws_receiver(
    websocket: WebSocket,
    session_id: UUID,
) -> None:
    """Receive messages from the WebSocket client (logging only for now)."""
    try:
        while True:
            data = await websocket.receive_text()
            logger.info(
                "ws_message_received",
                session_id=str(session_id),
                data=data[:200],
            )
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
