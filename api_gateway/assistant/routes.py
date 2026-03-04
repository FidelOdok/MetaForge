"""Assistant API routes for IDE integration.

Provides REST and WebSocket endpoints that IDE assistants use to
submit agent requests, manage design-change proposals (approve/reject),
and receive real-time events via SSE or WebSocket.

Endpoints live under ``/api/v1/assistant``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.schemas import (
    ApprovalDecision,
    AssistantRequest,
    AssistantResponse,
    DesignChangeProposal,
    ProposalListResponse,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level workflow & router
# ---------------------------------------------------------------------------

workflow = ApprovalWorkflow()

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


# ---------------------------------------------------------------------------
# Request submission
# ---------------------------------------------------------------------------


@router.post("/request", response_model=AssistantResponse)
async def submit_request(body: AssistantRequest) -> AssistantResponse:
    """Submit a request to an agent via the orchestrator.

    This is a placeholder that acknowledges the request.  The real
    implementation will forward to the Orchestrator / Temporal workflow.
    """
    logger.info(
        "assistant_request",
        action=body.action,
        target_id=str(body.target_id),
        session_id=str(body.session_id),
    )
    return AssistantResponse(
        request_id=uuid4(),
        status="accepted",
        result={
            "action": body.action,
            "target_id": str(body.target_id),
            "session_id": str(body.session_id),
        },
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
