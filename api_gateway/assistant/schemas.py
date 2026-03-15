"""Pydantic v2 request/response schemas for the assistant API layer.

Defines the HTTP and WebSocket contract for IDE assistants interacting
with the MetaForge Orchestrator and Digital Twin.  These schemas cover
agent requests, design-change proposals, approval decisions, and
real-time event streaming.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EventType(StrEnum):
    """WebSocket / SSE event types emitted by the assistant layer."""

    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    CHANGE_PROPOSED = "change_proposed"
    CHANGE_APPROVED = "change_approved"
    CHANGE_REJECTED = "change_rejected"
    SKILL_STARTED = "skill_started"
    SKILL_COMPLETED = "skill_completed"
    TWIN_UPDATED = "twin_updated"


class ChangeStatus(StrEnum):
    """Lifecycle states for a design-change proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    EXPIRED = "expired"


class ApprovalDecisionType(StrEnum):
    """The two possible human verdicts on a change proposal."""

    APPROVE = "approve"
    REJECT = "reject"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class AssistantRequest(BaseModel):
    """Body for ``POST /api/v1/assistant/request``.

    Every request must belong to a project. An IDE assistant submits a
    request to invoke an agent on a specific design work_product.
    """

    action: str = Field(
        min_length=1,
        description="Agent action to perform (e.g. 'validate_stress', 'run_drc')",
    )
    target_id: UUID = Field(description="UUID of the target work_product in the Digital Twin")
    project_id: str = Field(
        min_length=1,
        description="Project this request belongs to",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters",
    )
    session_id: UUID = Field(
        default_factory=uuid4,
        description="Session ID for grouping related requests",
    )


class ApprovalDecision(BaseModel):
    """Body for ``POST /api/v1/assistant/proposals/{change_id}/decide``."""

    change_id: UUID = Field(description="UUID of the proposal to decide on")
    decision: ApprovalDecisionType = Field(description="Approve or reject")
    reason: str = Field(
        min_length=1,
        description="Human-written justification for the decision",
    )
    reviewer: str = Field(
        min_length=1,
        description="Identifier of the human reviewer",
    )


# ---------------------------------------------------------------------------
# Response / event schemas
# ---------------------------------------------------------------------------


class AssistantResponse(BaseModel):
    """Response returned by ``POST /api/v1/assistant/request``."""

    request_id: UUID = Field(default_factory=uuid4, description="Unique request identifier")
    status: str = Field(description="Request status (accepted, completed, failed)")
    result: dict[str, Any] = Field(
        default_factory=dict,
        description="Action result payload",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Error messages, if any",
    )


class DesignChangeProposal(BaseModel):
    """A proposed design change awaiting human review.

    Created by agents when they want to modify the Digital Twin.
    Must be approved before the change is applied.
    """

    change_id: UUID = Field(default_factory=uuid4, description="Unique proposal identifier")
    agent_code: str = Field(
        min_length=1,
        description="Code of the agent proposing the change",
    )
    description: str = Field(
        min_length=1,
        description="Human-readable description of what the change does",
    )
    diff: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured diff of the proposed changes",
    )
    work_products_affected: list[UUID] = Field(
        default_factory=list,
        description="UUIDs of Digital Twin work_products affected by this change",
    )
    requires_approval: bool = Field(
        default=True,
        description="Whether this change requires explicit human approval",
    )
    status: ChangeStatus = Field(
        default=ChangeStatus.PENDING,
        description="Current lifecycle status of the proposal",
    )
    session_id: UUID = Field(
        default_factory=uuid4,
        description="Session under which this proposal was created",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the proposal was created",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="When the proposal was approved or rejected",
    )
    decision_reason: str | None = Field(
        default=None,
        description="Reviewer's reason for the decision",
    )
    reviewer: str | None = Field(
        default=None,
        description="Who approved or rejected the proposal",
    )


class WebSocketEvent(BaseModel):
    """A real-time event pushed to connected IDE assistants."""

    event_type: EventType = Field(description="Type of event")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific data",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the event was emitted",
    )
    session_id: UUID = Field(description="Session this event belongs to")


# ---------------------------------------------------------------------------
# List response wrappers
# ---------------------------------------------------------------------------


class RunStatusResponse(BaseModel):
    """Response from ``GET /api/v1/assistant/request/{run_id}``."""

    run_id: str = Field(description="Workflow run identifier")
    status: str = Field(description="Run status: pending, running, completed, failed")
    steps: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-step status and results",
    )
    completed_at: str | None = Field(
        default=None,
        description="ISO timestamp when the run completed",
    )


class ProposalListResponse(BaseModel):
    """Paginated list of design-change proposals."""

    proposals: list[DesignChangeProposal]
    total: int
