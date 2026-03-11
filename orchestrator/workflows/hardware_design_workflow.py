"""Multi-agent hardware design Temporal workflow.

Implements the canonical DAG for a hardware product design cycle:

    REQ -> SYS -> [EE || FW] -> SIM -> APPROVAL_GATE -> DONE

Steps run sequentially except EE and FW which execute in parallel.
An approval gate before EVT transition blocks until a human reviewer
approves the design artifacts.
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
    ApprovalRequest,
    get_default_retry_policy,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.workflows.hardware_design")

try:
    from temporalio import workflow

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False

# Import activity functions
from orchestrator.activities.approval_activity import wait_for_approval  # noqa: E402
from orchestrator.activities.electronics_activity import run_electronics_agent  # noqa: E402
from orchestrator.activities.firmware_activity import run_firmware_agent  # noqa: E402
from orchestrator.activities.mechanical_activity import run_mechanical_agent  # noqa: E402
from orchestrator.activities.simulation_activity import run_simulation_agent  # noqa: E402

# ---------------------------------------------------------------------------
# Workflow I/O models
# ---------------------------------------------------------------------------


class HardwareDesignWorkflowInput(BaseModel):
    """Input for the HardwareDesignWorkflow."""

    session_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Session UUID",
    )
    mechanical_task: dict[str, Any] = Field(
        default_factory=dict,
        description="TaskRequest for mechanical agent (REQ/SYS phase)",
    )
    electronics_task: dict[str, Any] = Field(
        default_factory=dict,
        description="TaskRequest for electronics agent",
    )
    firmware_task: dict[str, Any] = Field(
        default_factory=dict,
        description="TaskRequest for firmware agent",
    )
    simulation_task: dict[str, Any] = Field(
        default_factory=dict,
        description="TaskRequest for simulation agent",
    )
    require_approval: bool = Field(
        default=True,
        description="Whether to require human approval before EVT gate",
    )
    timeout_seconds: int = Field(
        default=600,
        description="Per-activity timeout in seconds",
    )


class StepOutcome(BaseModel):
    """Result of a single workflow step."""

    agent_code: str
    status: str  # "completed", "failed", "skipped"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class HardwareDesignWorkflowOutput(BaseModel):
    """Output from the HardwareDesignWorkflow."""

    workflow_run_id: str
    status: str  # "completed", "failed"
    steps: list[StepOutcome] = Field(default_factory=list)
    approval: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------


def _workflow_defn(cls: Any) -> Any:
    if HAS_TEMPORAL:
        return workflow.defn(cls)
    return cls


def _workflow_run(func: Any) -> Any:
    if HAS_TEMPORAL:
        return workflow.run(func)
    return func


@_workflow_defn
class HardwareDesignWorkflow:
    """Multi-agent DAG workflow for hardware design.

    Execution order:
    1. Mechanical agent (requirements / system design)
    2. Electronics + Firmware agents (parallel)
    3. Simulation agent (validation)
    4. Approval gate (human review)
    """

    @_workflow_run
    async def run(self, input: HardwareDesignWorkflowInput) -> HardwareDesignWorkflowOutput:
        """Execute the full hardware design DAG."""
        run_id = str(uuid4())
        started_at = datetime.now(UTC).isoformat()
        steps: list[StepOutcome] = []

        logger.info(
            "hardware_design_workflow_started",
            workflow_run_id=run_id,
            session_id=input.session_id,
        )

        # ---- Step 1: Mechanical (REQ/SYS) ----
        if input.mechanical_task:
            mech_outcome = await self._run_activity(
                run_mechanical_agent,
                agent_code="mechanical",
                task_request=input.mechanical_task,
                session_id=input.session_id,
                run_id=run_id,
                step_id="mechanical_req",
                timeout_seconds=input.timeout_seconds,
            )
            steps.append(mech_outcome)

            if mech_outcome.status == "failed":
                return self._build_output(run_id, "failed", steps, {}, started_at)

        # ---- Step 2: Electronics + Firmware (parallel) ----
        parallel_tasks: list[asyncio.Task[StepOutcome]] = []

        if input.electronics_task:
            ee_coro = self._run_activity(
                run_electronics_agent,
                agent_code="electronics",
                task_request=input.electronics_task,
                session_id=input.session_id,
                run_id=run_id,
                step_id="electronics_design",
                timeout_seconds=input.timeout_seconds,
            )
            parallel_tasks.append(asyncio.ensure_future(ee_coro))

        if input.firmware_task:
            fw_coro = self._run_activity(
                run_firmware_agent,
                agent_code="firmware",
                task_request=input.firmware_task,
                session_id=input.session_id,
                run_id=run_id,
                step_id="firmware_design",
                timeout_seconds=input.timeout_seconds,
            )
            parallel_tasks.append(asyncio.ensure_future(fw_coro))

        if parallel_tasks:
            parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)
            has_failure = False
            for result in parallel_results:
                if isinstance(result, Exception):
                    steps.append(
                        StepOutcome(
                            agent_code="unknown",
                            status="failed",
                            error=str(result),
                        )
                    )
                    has_failure = True
                else:
                    steps.append(result)
                    if result.status == "failed":
                        has_failure = True

            if has_failure:
                return self._build_output(run_id, "failed", steps, {}, started_at)

        # ---- Step 3: Simulation (validation) ----
        if input.simulation_task:
            sim_outcome = await self._run_activity(
                run_simulation_agent,
                agent_code="simulation",
                task_request=input.simulation_task,
                session_id=input.session_id,
                run_id=run_id,
                step_id="simulation_validation",
                timeout_seconds=input.timeout_seconds,
            )
            steps.append(sim_outcome)

            if sim_outcome.status == "failed":
                return self._build_output(run_id, "failed", steps, {}, started_at)

        # ---- Step 4: Approval gate ----
        approval_dict: dict[str, Any] = {}
        if input.require_approval:
            approval_request = ApprovalRequest(
                approval_id=str(uuid4()),
                description="EVT gate approval for hardware design",
                required_role="reviewer",
                run_id=run_id,
                step_id="evt_approval",
            )

            try:
                if HAS_TEMPORAL:
                    approval_result = await workflow.execute_activity(
                        wait_for_approval,
                        approval_request,
                        start_to_close_timeout=timedelta(hours=24),
                    )
                else:
                    approval_result = await wait_for_approval(approval_request)

                approval_dict = (
                    approval_result.model_dump(mode="json")
                    if hasattr(approval_result, "model_dump")
                    else approval_result
                )

                if not (
                    approval_result.approved
                    if hasattr(approval_result, "approved")
                    else approval_dict.get("approved", False)
                ):
                    logger.info(
                        "hardware_design_workflow_rejected",
                        workflow_run_id=run_id,
                    )
                    return self._build_output(run_id, "failed", steps, approval_dict, started_at)

            except Exception as exc:
                logger.error(
                    "hardware_design_workflow_approval_error",
                    workflow_run_id=run_id,
                    error=str(exc),
                )
                return self._build_output(run_id, "failed", steps, {"error": str(exc)}, started_at)

        logger.info(
            "hardware_design_workflow_completed",
            workflow_run_id=run_id,
            total_steps=len(steps),
        )

        return self._build_output(run_id, "completed", steps, approval_dict, started_at)

    async def _run_activity(
        self,
        activity_fn: Any,
        agent_code: str,
        task_request: dict[str, Any],
        session_id: str,
        run_id: str,
        step_id: str,
        timeout_seconds: int,
    ) -> StepOutcome:
        """Execute a single agent activity and return a StepOutcome."""
        activity_input = AgentActivityInput(
            agent_code=agent_code,
            task_request=task_request,
            session_id=session_id,
            run_id=run_id,
            step_id=step_id,
        )

        try:
            if HAS_TEMPORAL:
                result = await workflow.execute_activity(
                    activity_fn,
                    activity_input,
                    start_to_close_timeout=timedelta(seconds=timeout_seconds),
                    retry_policy=get_default_retry_policy(),
                )
            else:
                result = await activity_fn(activity_input)

            result_dict = (
                result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            )

            return StepOutcome(
                agent_code=agent_code,
                status="completed",
                result=result_dict,
            )

        except Exception as exc:
            logger.error(
                "hardware_design_step_failed",
                agent_code=agent_code,
                step_id=step_id,
                error=str(exc),
            )
            return StepOutcome(
                agent_code=agent_code,
                status="failed",
                error=str(exc),
            )

    @staticmethod
    def _build_output(
        run_id: str,
        status: str,
        steps: list[StepOutcome],
        approval: dict[str, Any],
        started_at: str,
    ) -> HardwareDesignWorkflowOutput:
        return HardwareDesignWorkflowOutput(
            workflow_run_id=run_id,
            status=status,
            steps=steps,
            approval=approval,
            started_at=started_at,
            completed_at=datetime.now(UTC).isoformat(),
        )
