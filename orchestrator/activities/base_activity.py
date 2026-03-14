"""Base models and helpers for Temporal activity wrappers.

Provides the canonical input/output Pydantic models shared by all agent
activities plus a default retry policy factory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.activities.base")

# Try to import Temporal SDK; degrade gracefully
try:
    from temporalio.common import RetryPolicy

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False


# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


class AgentActivityInput(BaseModel):
    """Canonical input for every agent Temporal activity."""

    agent_code: str = Field(description="Domain agent identifier (e.g. 'mechanical')")
    task_request: dict[str, Any] = Field(description="Serialised TaskRequest for the target agent")
    session_id: str = Field(description="Session UUID string")
    run_id: str = Field(description="Temporal workflow run ID")
    step_id: str = Field(description="Workflow step identifier")


class AgentActivityOutput(BaseModel):
    """Canonical output returned by every agent Temporal activity."""

    task_result: dict[str, Any] = Field(description="Serialised TaskResult from the agent")
    agent_code: str = Field(description="Domain agent identifier")
    duration_ms: float = Field(description="Wall-clock execution time in milliseconds")
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="MCP tool calls made during execution",
    )


# ---------------------------------------------------------------------------
# Approval models
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """Input for the approval gate activity."""

    approval_id: str = Field(description="Unique approval request identifier")
    description: str = Field(description="What needs approval")
    required_role: str = Field(default="reviewer", description="Role required to approve")
    work_product_ids: list[str] = Field(
        default_factory=list, description="WorkProduct IDs under review"
    )
    run_id: str = Field(description="Parent workflow run ID")
    step_id: str = Field(description="Parent workflow step ID")
    requested_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 timestamp of request",
    )


class ApprovalResult(BaseModel):
    """Output from the approval gate activity."""

    approved: bool = Field(description="Whether the request was approved")
    approver_id: str = Field(default="", description="Who approved/rejected")
    comment: str = Field(default="", description="Optional reviewer comment")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 timestamp of decision",
    )


# ---------------------------------------------------------------------------
# Retry policy helper
# ---------------------------------------------------------------------------


def get_default_retry_policy() -> Any:
    """Return a Temporal RetryPolicy with sensible defaults.

    Returns a plain dict when the Temporal SDK is not installed so the rest
    of the codebase can still reference this helper in tests.
    """
    if HAS_TEMPORAL:
        from datetime import timedelta

        return RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=60),
            backoff_coefficient=2.0,
        )
    return {
        "maximum_attempts": 3,
        "initial_interval_seconds": 1.0,
        "maximum_interval_seconds": 60.0,
        "backoff_coefficient": 2.0,
    }
