"""Iteration controller — propose-validate-refine loop with gate checks.

Implements the core MetaForge design loop:

1. **Propose** — agent produces a design change on an isolated branch
2. **Validate** — Twin evaluates constraints on that branch
3. **Refine** — if constraints fail, enrich parameters and loop
4. **Gate check** — if constraints pass, auto-approve or delegate
5. **Commit** — merge the approved branch back to source

The gate fails closed (FORGE-470): with ``auto_approve`` off and no approval
workflow configured there is nobody to ask, so the loop ends ``BLOCKED`` and
nothing is merged. It used to approve in that case. An explicit
``auto_approve`` still merges, but under the non-human approver
``auto:iteration_controller`` with a warning log and a metric, so automatic
approvals are visible.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from observability.metrics import MetricsCollector, collector_for
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.iteration")

#: Approver identity recorded when a gate is passed by ``auto_approve``
#: rather than by a human (FORGE-470).
AUTO_APPROVER_ID = "auto:iteration_controller"

_metrics: MetricsCollector | None = None


def _get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = collector_for("metaforge-orchestrator")
    return _metrics


def set_iteration_controller_metrics(metrics: MetricsCollector | None) -> None:
    """Inject a collector (tests), or ``None`` to resolve it lazily again."""
    global _metrics
    _metrics = metrics


# ---------------------------------------------------------------------------
# Enums & config
# ---------------------------------------------------------------------------


class IterationStatus(StrEnum):
    RUNNING = "running"
    CONVERGED = "converged"
    FAILED = "failed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    # Constraints passed but the gate cannot be answered: no approval
    # workflow is configured and auto_approve is off (FORGE-470).
    BLOCKED = "blocked"


class IterationConfig(BaseModel):
    max_iterations: int = 5
    auto_approve: bool = False
    timeout_seconds: int = 600


# ---------------------------------------------------------------------------
# Audit models
# ---------------------------------------------------------------------------


class IterationRecord(BaseModel):
    """Audit trail for a single iteration."""

    iteration: int
    proposed_at: str
    validated_at: str | None = None
    constraints_passed: bool = False
    constraint_errors: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class IterationResult(BaseModel):
    """Overall result of the iteration loop."""

    loop_id: str
    status: IterationStatus
    total_iterations: int = 0
    branch: str = ""
    source_branch: str = ""
    records: list[IterationRecord] = Field(default_factory=list)
    final_result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    approver_id: str | None = None
    started_at: str = ""
    completed_at: str = ""


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class IterationController:
    """Drives the propose-validate-refine loop for a single agent task."""

    def __init__(
        self,
        twin: Any,  # TwinAPI
        config: IterationConfig | None = None,
        approval_workflow: Any | None = None,  # ApprovalWorkflow
    ) -> None:
        self._twin = twin
        self._config = config or IterationConfig()
        self._approval = approval_workflow

    async def run_iteration_loop(
        self,
        agent: Any,  # AgentProtocol
        agent_code: str,
        task_type: str,
        work_product_id: str,
        parameters: dict[str, Any],
        source_branch: str = "main",
    ) -> IterationResult:
        """Execute the propose-validate-refine loop.

        Returns an ``IterationResult`` with the final status and audit trail.
        """
        loop_id = str(uuid4())
        branch = f"iterate/{loop_id}"
        now = datetime.now(UTC).isoformat()

        result = IterationResult(
            loop_id=loop_id,
            status=IterationStatus.RUNNING,
            branch=branch,
            source_branch=source_branch,
            started_at=now,
        )

        with tracer.start_as_current_span("iteration.loop") as span:
            span.set_attribute("iteration.loop_id", loop_id)
            span.set_attribute("iteration.agent_code", agent_code)
            span.set_attribute("iteration.task_type", task_type)
            span.set_attribute("iteration.max_iterations", self._config.max_iterations)

            try:
                await self._twin.create_branch(branch, from_branch=source_branch)
            except Exception as exc:
                result.status = IterationStatus.FAILED
                result.error = f"Branch creation failed: {exc}"
                result.completed_at = datetime.now(UTC).isoformat()
                span.record_exception(exc)
                logger.error(
                    "iteration_branch_failed",
                    loop_id=loop_id,
                    error=str(exc),
                )
                return result

            current_params = dict(parameters)

            for iteration in range(1, self._config.max_iterations + 1):
                with tracer.start_as_current_span("iteration.cycle") as cycle_span:
                    cycle_span.set_attribute("iteration.number", iteration)
                    t0 = time.monotonic()
                    proposed_at = datetime.now(UTC).isoformat()

                    record = IterationRecord(
                        iteration=iteration,
                        proposed_at=proposed_at,
                    )

                    # --- PROPOSE ---
                    logger.info(
                        "iteration_propose",
                        loop_id=loop_id,
                        iteration=iteration,
                        agent_code=agent_code,
                    )
                    try:
                        from uuid import UUID as _UUID

                        from domain_agents.mechanical.agent import TaskRequest

                        aid = (
                            _UUID(work_product_id)
                            if isinstance(work_product_id, str)
                            else work_product_id
                        )
                        request = TaskRequest(
                            task_type=task_type,
                            work_product_id=aid,
                            parameters=current_params,
                            branch=branch,
                        )
                        task_result = await agent.run_task(request)
                    except Exception as exc:
                        record.duration_seconds = time.monotonic() - t0
                        result.records.append(record)
                        result.status = IterationStatus.FAILED
                        result.error = f"Agent failed on iteration {iteration}: {exc}"
                        result.total_iterations = iteration
                        result.completed_at = datetime.now(UTC).isoformat()
                        span.record_exception(exc)
                        return result

                    # --- VALIDATE ---
                    logger.info(
                        "iteration_validate",
                        loop_id=loop_id,
                        iteration=iteration,
                    )
                    try:
                        eval_result = await self._twin.evaluate_constraints(branch)
                        constraints_passed = eval_result.passed
                        constraint_errors = [
                            v.message for v in getattr(eval_result, "violations", [])
                        ]
                    except Exception as exc:
                        constraints_passed = False
                        constraint_errors = [str(exc)]

                    record.validated_at = datetime.now(UTC).isoformat()
                    record.constraints_passed = constraints_passed
                    record.constraint_errors = constraint_errors
                    record.duration_seconds = round(time.monotonic() - t0, 3)
                    result.records.append(record)
                    result.total_iterations = iteration

                    cycle_span.set_attribute("iteration.constraints_passed", constraints_passed)

                    if constraints_passed:
                        # --- GATE CHECK ---
                        gate_status = await self._gate_check(loop_id, agent_code, branch)
                        if gate_status == IterationStatus.APPROVED:
                            # --- COMMIT ---
                            try:
                                await self._twin.commit(
                                    branch,
                                    f"iteration/{loop_id}: converged after {iteration} iterations",
                                    f"agent:{agent_code}",
                                )
                                await self._twin.merge(
                                    branch,
                                    source_branch,
                                    f"merge iteration/{loop_id}",
                                    f"agent:{agent_code}",
                                )
                            except Exception as exc:
                                result.status = IterationStatus.FAILED
                                result.error = f"Commit/merge failed: {exc}"
                                result.completed_at = datetime.now(UTC).isoformat()
                                span.record_exception(exc)
                                return result

                            result.status = IterationStatus.APPROVED
                            if self._config.auto_approve:
                                result.approver_id = AUTO_APPROVER_ID
                            result.final_result = (
                                task_result.model_dump()
                                if hasattr(task_result, "model_dump")
                                else {"raw": str(task_result)}
                            )
                            result.completed_at = datetime.now(UTC).isoformat()
                            span.set_attribute("iteration.final_status", "approved")
                            logger.info(
                                "iteration_approved",
                                loop_id=loop_id,
                                iterations=iteration,
                            )
                            return result

                        elif gate_status == IterationStatus.CONVERGED:
                            result.status = IterationStatus.CONVERGED
                            result.final_result = (
                                task_result.model_dump()
                                if hasattr(task_result, "model_dump")
                                else {"raw": str(task_result)}
                            )
                            result.completed_at = datetime.now(UTC).isoformat()
                            logger.info(
                                "iteration_converged",
                                loop_id=loop_id,
                                iterations=iteration,
                            )
                            return result

                        elif gate_status == IterationStatus.BLOCKED:
                            result.status = IterationStatus.BLOCKED
                            result.error = (
                                "Constraints passed but no approval workflow is "
                                "configured and auto_approve is off; refusing to "
                                f"merge branch {branch!r} without approval"
                            )
                            result.final_result = (
                                task_result.model_dump()
                                if hasattr(task_result, "model_dump")
                                else {"raw": str(task_result)}
                            )
                            result.completed_at = datetime.now(UTC).isoformat()
                            span.set_attribute("iteration.final_status", "blocked")
                            return result

                        elif gate_status == IterationStatus.REJECTED:
                            result.status = IterationStatus.REJECTED
                            result.completed_at = datetime.now(UTC).isoformat()
                            return result

                    # --- REFINE ---
                    current_params = self._refine_parameters(
                        current_params, iteration, constraint_errors
                    )

            # Exhausted max iterations
            result.status = IterationStatus.FAILED
            result.error = f"Max iterations ({self._config.max_iterations}) exhausted"
            result.completed_at = datetime.now(UTC).isoformat()
            span.set_attribute("iteration.final_status", "max_exhausted")
            logger.warning(
                "iteration_max_exhausted",
                loop_id=loop_id,
                max_iterations=self._config.max_iterations,
            )
            return result

    async def _gate_check(self, loop_id: str, agent_code: str, branch: str) -> IterationStatus:
        """Decide the gate for a converged result.

        Fails closed (FORGE-470): only an explicit ``auto_approve`` approves
        without a human, and never silently.
        """
        if self._config.auto_approve:
            logger.warning(
                "iteration_gate_auto_approved",
                loop_id=loop_id,
                agent_code=agent_code,
                branch=branch,
                approver_id=AUTO_APPROVER_ID,
            )
            _get_metrics().record_iteration_gate_unattended("auto_approved", agent_code)
            return IterationStatus.APPROVED

        if self._approval is None:
            # Nobody can be asked, so the gate cannot pass. It used to
            # approve here, which merged unreviewed work (FORGE-470).
            logger.warning(
                "iteration_gate_no_approval_workflow",
                loop_id=loop_id,
                agent_code=agent_code,
                branch=branch,
                outcome="blocked",
            )
            _get_metrics().record_iteration_gate_unattended("blocked", agent_code)
            return IterationStatus.BLOCKED

        # Delegate to approval workflow — returns CONVERGED (awaiting human)
        logger.info(
            "iteration_gate_pending",
            loop_id=loop_id,
            agent_code=agent_code,
            branch=branch,
        )
        return IterationStatus.CONVERGED

    @staticmethod
    def _refine_parameters(
        params: dict[str, Any],
        iteration: int,
        errors: list[str],
    ) -> dict[str, Any]:
        """Enrich parameters with iteration metadata for the next cycle."""
        refined = dict(params)
        refined["_iteration"] = iteration + 1
        refined["_previous_errors"] = errors
        return refined
