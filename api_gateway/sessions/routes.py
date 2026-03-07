"""Sessions REST endpoints for the MetaForge Gateway.

Exposes workflow runs as dashboard-friendly "sessions" by mapping
each ``WorkflowRun`` to a ``SessionResponse``.

Endpoints live under ``/api/v1/sessions``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request

from api_gateway.sessions.schemas import (
    SessionEventResponse,
    SessionListResponse,
    SessionResponse,
)
from observability.tracing import get_tracer
from orchestrator.workflow_dag import StepStatus, WorkflowRun

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.sessions")

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_to_session(run: WorkflowRun) -> SessionResponse:
    """Convert a ``WorkflowRun`` into a ``SessionResponse``.

    Extracts the agent_code from the first step and synthesises timeline
    events from step_results.
    """
    first_step = next(iter(run.step_results.values()), None)
    agent_code = first_step.agent_code if first_step else "UNKNOWN"
    task_type = first_step.task_type if first_step else "unknown"

    # Map run status to session status vocabulary
    status_map = {
        "pending": "pending",
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "failed",
    }
    status = status_map.get(run.status, run.status)

    # Build events from step results
    events: list[SessionEventResponse] = []
    for idx, (step_id, sr) in enumerate(run.step_results.items()):
        if sr.started_at:
            events.append(
                SessionEventResponse(
                    id=f"{run.id}-{step_id}-start",
                    timestamp=sr.started_at,
                    type="task_started",
                    agent_code=sr.agent_code,
                    message=f"Started {sr.task_type.replace('_', ' ')}",
                )
            )
        if sr.status == StepStatus.COMPLETED and sr.completed_at:
            events.append(
                SessionEventResponse(
                    id=f"{run.id}-{step_id}-done",
                    timestamp=sr.completed_at,
                    type="task_completed",
                    agent_code=sr.agent_code,
                    message=f"{sr.task_type.replace('_', ' ')} completed successfully",
                )
            )
        elif sr.status == StepStatus.FAILED and sr.completed_at:
            events.append(
                SessionEventResponse(
                    id=f"{run.id}-{step_id}-fail",
                    timestamp=sr.completed_at,
                    type="task_failed",
                    agent_code=sr.agent_code,
                    message=sr.error or f"{sr.task_type.replace('_', ' ')} failed",
                )
            )

    # Sort events by timestamp
    events.sort(key=lambda e: e.timestamp)

    return SessionResponse(
        id=run.id,
        agent_code=agent_code,
        task_type=task_type,
        status=status,
        started_at=run.started_at or "",
        completed_at=run.completed_at,
        events=events,
        run_id=run.id,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=SessionListResponse)
async def list_sessions(request: Request) -> SessionListResponse:
    """List all agent sessions (backed by workflow runs)."""
    with tracer.start_as_current_span("sessions.list"):
        workflow_engine = getattr(request.app.state, "workflow_engine", None)
        if workflow_engine is None:
            return SessionListResponse(sessions=[], total=0)

        runs = await workflow_engine.list_runs()
        sessions = [_run_to_session(run) for run in runs]
        # Most recent first
        sessions.sort(key=lambda s: s.started_at, reverse=True)

        logger.info("sessions_listed", count=len(sessions))
        return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, request: Request) -> SessionResponse:
    """Get a single session by ID."""
    with tracer.start_as_current_span("sessions.get") as span:
        span.set_attribute("session.id", session_id)

        workflow_engine = getattr(request.app.state, "workflow_engine", None)
        if workflow_engine is None:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        run = await workflow_engine.get_run(session_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Session not found")

        return _run_to_session(run)
