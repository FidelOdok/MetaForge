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


class SessionListResponse(BaseModel):
    """List of sessions."""

    sessions: list[SessionResponse]
    total: int
