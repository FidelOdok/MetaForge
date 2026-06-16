"""Sessions REST endpoints for the MetaForge Gateway.

Exposes workflow runs as dashboard-friendly "sessions" by mapping
each ``WorkflowRun`` to a ``SessionResponse``.

Endpoints live under ``/v1/sessions``.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request

from api_gateway.sessions.backend import (
    AgentSessionStore,
    SessionClosedError,
    SessionNotFoundError,
)
from api_gateway.sessions.schemas import (
    SessionCreateRequest,
    SessionEventCreatedResponse,
    SessionEventCreateRequest,
    SessionEventResponse,
    SessionListResponse,
    SessionResponse,
    SessionUpdateRequest,
)
from observability.tracing import get_tracer
from orchestrator.workflow_dag import StepStatus, WorkflowRun

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.sessions")

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _store(request: Request) -> AgentSessionStore | None:
    """The externally-recorded agent-session store (MET-493), if wired."""
    return getattr(request.app.state, "agent_session_store", None)


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
async def list_sessions(request: Request, project_id: str | None = None) -> SessionListResponse:
    """List all agent sessions.

    Merges internal Temporal ``WorkflowRun``s with externally-recorded
    agent sessions (MET-493) so MCP/CLI-driven work shows up alongside
    autonomous runs. Most-recent-first.

    ``project_id`` scopes to one project (MET-516). Internal Temporal runs
    carry no project, so they're excluded when a project filter is set.
    """
    with tracer.start_as_current_span("sessions.list") as span:
        sessions: list[SessionResponse] = []

        # Workflow runs have no project — only include them in the unscoped view.
        workflow_engine = getattr(request.app.state, "workflow_engine", None)
        if workflow_engine is not None and not project_id:
            runs = await workflow_engine.list_runs()
            sessions.extend(_run_to_session(run) for run in runs)

        store = _store(request)
        external = 0
        if store is not None:
            ext = await store.list_sessions(project_id)
            sessions.extend(ext)
            external = len(ext)
        if project_id:
            span.set_attribute("sessions.project_id", project_id)

        # Most recent first (ISO timestamps sort lexically)
        sessions.sort(key=lambda s: s.started_at, reverse=True)

        logger.info("sessions_listed", count=len(sessions), external=external)
        return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, request: Request) -> SessionResponse:
    """Get a single session by ID (store first, then workflow engine)."""
    with tracer.start_as_current_span("sessions.get") as span:
        span.set_attribute("session.id", session_id)

        store = _store(request)
        if store is not None:
            session = await store.get_session(session_id)
            if session is not None:
                return session

        workflow_engine = getattr(request.app.state, "workflow_engine", None)
        if workflow_engine is not None:
            run = await workflow_engine.get_run(session_id)
            if run is not None:
                return _run_to_session(run)

        raise HTTPException(status_code=404, detail="Session not found")


# ---------------------------------------------------------------------------
# Write API (MET-493) — external agents record their own sessions
# ---------------------------------------------------------------------------


def _require_store(request: Request) -> AgentSessionStore:
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Session store not initialized")
    return store


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(body: SessionCreateRequest, request: Request) -> SessionResponse:
    """Open a new externally-recorded agent session."""
    with tracer.start_as_current_span("sessions.create") as span:
        store = _require_store(request)
        if body.project_id:
            try:
                UUID(body.project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
        span.set_attribute("session.agent_code", body.agent_code)
        return await store.create_session(
            agent_code=body.agent_code,
            task_type=body.task_type,
            title=body.title,
            project_id=body.project_id,
        )


@router.post(
    "/{session_id}/events",
    response_model=SessionEventCreatedResponse,
    status_code=201,
)
async def append_session_event(
    session_id: str, body: SessionEventCreateRequest, request: Request
) -> SessionEventCreatedResponse:
    """Append one event (thought / action / decision / …) to a session."""
    with tracer.start_as_current_span("sessions.append_event") as span:
        span.set_attribute("session.id", session_id)
        span.set_attribute("session.event_type", body.type)
        store = _require_store(request)
        try:
            event_id, seq = await store.append_event(
                session_id, type=body.type, message=body.message, data=body.data
            )
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")
        except SessionClosedError:
            raise HTTPException(status_code=409, detail="Session already completed")
        return SessionEventCreatedResponse(event_id=event_id, seq=seq)


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str, body: SessionUpdateRequest, request: Request
) -> SessionResponse:
    """Complete a session (set terminal status + optional summary)."""
    with tracer.start_as_current_span("sessions.update") as span:
        span.set_attribute("session.id", session_id)
        store = _require_store(request)
        try:
            return await store.complete_session(
                session_id, status=body.status, summary=body.summary
            )
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="Session not found")
        except SessionClosedError:
            raise HTTPException(status_code=409, detail="Session already completed")
