"""Request/response schemas for sessions REST endpoints.

Maps WorkflowRun data into a dashboard-friendly format.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionEventResponse(BaseModel):
    """A single event within a session timeline."""

    id: str
    timestamp: str
    type: str
    agent_code: str
    message: str
    data: dict[str, object] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Dashboard-friendly representation of an agent session."""

    id: str
    agent_code: str
    task_type: str
    status: str
    started_at: str
    completed_at: str | None = None
    events: list[SessionEventResponse] = Field(default_factory=list)
    run_id: str | None = None
    summary: str | None = None
    # MET-493: "workflow" for internal Temporal runs, "external" for sessions
    # recorded by MCP/CLI agents. None keeps the legacy shape for workflow
    # runs so the dashboard renders unchanged.
    source: str | None = None
    # MET-516: project this session belongs to (None for unscoped / workflow runs).
    project_id: str | None = None


class SessionListResponse(BaseModel):
    """List of sessions."""

    sessions: list[SessionResponse]
    total: int


# ── Write API (MET-493): external agents record their own sessions ──────


class SessionCreateRequest(BaseModel):
    """Open a new externally-recorded agent session."""

    agent_code: str
    task_type: str
    title: str | None = None
    project_id: str | None = None


class SessionEventCreateRequest(BaseModel):
    """Append one event to a session timeline.

    ``type`` is the normalized capture vocabulary shared by every client
    adapter (MET-497): thought / action / decision / observation / error /
    result.
    """

    type: str
    message: str
    data: dict[str, object] = Field(default_factory=dict)


class SessionEventCreatedResponse(BaseModel):
    """Ack for an appended event — carries the server-assigned sequence."""

    event_id: str
    seq: int


class SessionUpdateRequest(BaseModel):
    """Close out a session (status + optional summary)."""

    status: str
    summary: str | None = None
