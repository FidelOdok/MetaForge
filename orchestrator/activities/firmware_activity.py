"""Temporal activity wrapper for the Firmware Engineering agent."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from orchestrator.activities.base_activity import AgentActivityInput, AgentActivityOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.activities.firmware")

try:
    from temporalio import activity

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False


def _activity_defn(func: Any) -> Any:
    """Apply @activity.defn when the Temporal SDK is available."""
    if HAS_TEMPORAL:
        return activity.defn(func)
    return func


@_activity_defn
async def run_firmware_agent(input: AgentActivityInput) -> AgentActivityOutput:
    """Run the Firmware Engineering agent as a Temporal activity."""
    with tracer.start_as_current_span("activity.run_firmware_agent") as span:
        span.set_attribute("agent.code", input.agent_code)
        span.set_attribute("activity.run_id", input.run_id)
        span.set_attribute("activity.step_id", input.step_id)
        span.set_attribute("activity.session_id", input.session_id)

        logger.info(
            "firmware_activity_started",
            agent_code=input.agent_code,
            run_id=input.run_id,
            step_id=input.step_id,
            session_id=input.session_id,
        )

        t0 = time.monotonic()

        try:
            from domain_agents.firmware.agent import FirmwareAgent, TaskRequest
            from skill_registry.mcp_bridge import InMemoryMcpBridge
            from twin_core.api import InMemoryTwinAPI

            twin = InMemoryTwinAPI.create()
            mcp = InMemoryMcpBridge()
            agent = FirmwareAgent(
                twin=twin,
                mcp=mcp,
                session_id=UUID(input.session_id),
            )

            task_request = TaskRequest.model_validate(input.task_request)
            result = await agent.run_task(task_request)
            elapsed_ms = (time.monotonic() - t0) * 1000

            span.set_attribute("activity.duration_ms", elapsed_ms)
            span.set_attribute("activity.success", result.success)

            logger.info(
                "firmware_activity_completed",
                run_id=input.run_id,
                step_id=input.step_id,
                success=result.success,
                duration_ms=round(elapsed_ms, 2),
            )

            return AgentActivityOutput(
                task_result=result.model_dump(mode="json"),
                agent_code=input.agent_code,
                duration_ms=elapsed_ms,
                tool_calls=[],
            )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            span.record_exception(exc)
            logger.error(
                "firmware_activity_failed",
                run_id=input.run_id,
                step_id=input.step_id,
                error=str(exc),
                duration_ms=round(elapsed_ms, 2),
            )
            raise
