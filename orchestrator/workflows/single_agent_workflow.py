"""Single-agent Temporal workflow.

Runs exactly one domain agent activity, handling timeout and retry via
Temporal's built-in mechanisms, and emits lifecycle events to the EventBus.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from orchestrator.activities.base_activity import (
    AgentActivityInput,
    AgentActivityOutput,
    get_default_retry_policy,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.workflows.single_agent")

# Try to import Temporal SDK
try:
    from temporalio import workflow

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False


# ---------------------------------------------------------------------------
# Workflow I/O models
# ---------------------------------------------------------------------------


class SingleAgentWorkflowInput(BaseModel):
    """Input for the SingleAgentWorkflow."""

    agent_code: str = Field(description="Domain agent to run")
    task_request: dict[str, Any] = Field(description="Serialised TaskRequest")
    session_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Session UUID",
    )
    timeout_seconds: int = Field(
        default=600, description="Activity start-to-close timeout in seconds"
    )


class SingleAgentWorkflowOutput(BaseModel):
    """Output from the SingleAgentWorkflow."""

    activity_output: dict[str, Any] = Field(
        description="Serialised AgentActivityOutput"
    )
    agent_code: str
    workflow_run_id: str = Field(default="")
    started_at: str = Field(default="")
    completed_at: str = Field(default="")
    status: str = Field(default="completed")


# ---------------------------------------------------------------------------
# Activity dispatcher (maps agent_code -> activity function)
# ---------------------------------------------------------------------------

# Import activity functions at module level for reference by name
from orchestrator.activities.mechanical_activity import run_mechanical_agent
from orchestrator.activities.electronics_activity import run_electronics_agent
from orchestrator.activities.firmware_activity import run_firmware_agent
from orchestrator.activities.simulation_activity import run_simulation_agent

AGENT_ACTIVITIES: dict[str, Any] = {
    "mechanical": run_mechanical_agent,
    "electronics": run_electronics_agent,
    "firmware": run_firmware_agent,
    "simulation": run_simulation_agent,
}


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------


def _workflow_defn(cls: Any) -> Any:
    """Apply @workflow.defn when the Temporal SDK is available."""
    if HAS_TEMPORAL:
        return workflow.defn(cls)
    return cls


def _workflow_run(func: Any) -> Any:
    """Apply @workflow.run when the Temporal SDK is available."""
    if HAS_TEMPORAL:
        return workflow.run(func)
    return func


@_workflow_defn
class SingleAgentWorkflow:
    """Temporal workflow that executes a single domain agent.

    Dispatches to the correct activity based on ``agent_code``, applies
    timeout and retry policy, and returns the result.
    """

    @_workflow_run
    async def run(self, input: SingleAgentWorkflowInput) -> SingleAgentWorkflowOutput:
        """Execute the workflow."""
        started_at = datetime.now(UTC).isoformat()
        run_id = str(uuid4())

        logger.info(
            "single_agent_workflow_started",
            agent_code=input.agent_code,
            session_id=input.session_id,
            workflow_run_id=run_id,
        )

        activity_fn = AGENT_ACTIVITIES.get(input.agent_code)
        if activity_fn is None:
            logger.error(
                "single_agent_workflow_unknown_agent",
                agent_code=input.agent_code,
            )
            return SingleAgentWorkflowOutput(
                activity_output={
                    "task_result": {
                        "success": False,
                        "errors": [
                            f"Unknown agent_code: {input.agent_code}. "
                            f"Available: {', '.join(sorted(AGENT_ACTIVITIES))}"
                        ],
                    },
                    "agent_code": input.agent_code,
                    "duration_ms": 0,
                    "tool_calls": [],
                },
                agent_code=input.agent_code,
                workflow_run_id=run_id,
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
                status="failed",
            )

        activity_input = AgentActivityInput(
            agent_code=input.agent_code,
            task_request=input.task_request,
            session_id=input.session_id,
            run_id=run_id,
            step_id=f"{input.agent_code}_step",
        )

        try:
            if HAS_TEMPORAL:
                result = await workflow.execute_activity(
                    activity_fn,
                    activity_input,
                    start_to_close_timeout=timedelta(seconds=input.timeout_seconds),
                    retry_policy=get_default_retry_policy(),
                )
            else:
                # Direct invocation for testing without Temporal
                result = await activity_fn(activity_input)

            completed_at = datetime.now(UTC).isoformat()

            logger.info(
                "single_agent_workflow_completed",
                agent_code=input.agent_code,
                workflow_run_id=run_id,
            )

            result_dict = (
                result.model_dump(mode="json")
                if hasattr(result, "model_dump")
                else result
            )

            return SingleAgentWorkflowOutput(
                activity_output=result_dict,
                agent_code=input.agent_code,
                workflow_run_id=run_id,
                started_at=started_at,
                completed_at=completed_at,
                status="completed",
            )

        except Exception as exc:
            completed_at = datetime.now(UTC).isoformat()
            logger.error(
                "single_agent_workflow_failed",
                agent_code=input.agent_code,
                workflow_run_id=run_id,
                error=str(exc),
            )
            return SingleAgentWorkflowOutput(
                activity_output={
                    "task_result": {
                        "success": False,
                        "errors": [str(exc)],
                    },
                    "agent_code": input.agent_code,
                    "duration_ms": 0,
                    "tool_calls": [],
                },
                agent_code=input.agent_code,
                workflow_run_id=run_id,
                started_at=started_at,
                completed_at=completed_at,
                status="failed",
            )
