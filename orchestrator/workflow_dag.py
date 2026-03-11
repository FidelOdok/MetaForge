"""Workflow DAG engine — workflow definitions, runs, and step tracking.

Defines the data models for multi-step agent workflows and provides an
in-memory engine that can be swapped for Temporal later (ADR-001).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.workflow")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    WAITING = "waiting"  # has unresolved dependencies
    READY = "ready"  # all deps met, awaiting scheduler
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WorkflowStep(BaseModel):
    """A single step in a workflow definition."""

    step_id: str
    agent_code: str
    task_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300
    retry_max: int = 0


class WorkflowDefinition(BaseModel):
    """Reusable workflow template."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class StepResult(BaseModel):
    """Runtime result for a single workflow step."""

    step_id: str
    status: StepStatus = StepStatus.PENDING
    agent_code: str = ""
    task_type: str = ""
    task_result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    retry_count: int = 0
    started_at: str | None = None
    completed_at: str | None = None


class WorkflowRun(BaseModel):
    """A single execution of a workflow definition."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    definition_id: str
    definition_name: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    branch: str = "main"
    step_results: dict[str, StepResult] = Field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract engine
# ---------------------------------------------------------------------------


class WorkflowEngine(ABC):
    """Abstract workflow engine — swap for Temporal in production."""

    @abstractmethod
    async def register_workflow(self, definition: WorkflowDefinition) -> WorkflowDefinition: ...

    @abstractmethod
    async def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None: ...

    @abstractmethod
    async def start_run(
        self,
        workflow_id: str,
        branch: str = "main",
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun: ...

    @abstractmethod
    async def get_run(self, run_id: str) -> WorkflowRun | None: ...

    @abstractmethod
    async def update_step(
        self,
        run_id: str,
        step_id: str,
        status: StepStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> StepResult | None: ...

    @abstractmethod
    async def cancel_run(self, run_id: str) -> WorkflowRun | None: ...

    @abstractmethod
    async def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowStatus | None = None,
    ) -> list[WorkflowRun]: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryWorkflowEngine(WorkflowEngine):
    """Dict-backed workflow engine for development and testing."""

    def __init__(self) -> None:
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._runs: dict[str, WorkflowRun] = {}

    @classmethod
    def create(cls) -> InMemoryWorkflowEngine:
        return cls()

    async def register_workflow(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        self._definitions[definition.id] = definition
        logger.info(
            "workflow_registered",
            workflow_id=definition.id,
            name=definition.name,
            step_count=len(definition.steps),
        )
        return definition

    async def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        return self._definitions.get(workflow_id)

    async def start_run(
        self,
        workflow_id: str,
        branch: str = "main",
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        with tracer.start_as_current_span("workflow.start_run") as span:
            defn = self._definitions.get(workflow_id)
            if defn is None:
                raise ValueError(f"Unknown workflow: {workflow_id}")

            now = datetime.now(UTC).isoformat()
            run = WorkflowRun(
                definition_id=workflow_id,
                definition_name=defn.name,
                status=WorkflowStatus.RUNNING,
                branch=branch,
                started_at=now,
                metadata=metadata or {},
            )

            # Initialise step results
            for step in defn.steps:
                initial_status = StepStatus.WAITING if step.depends_on else StepStatus.READY
                run.step_results[step.step_id] = StepResult(
                    step_id=step.step_id,
                    status=initial_status,
                    agent_code=step.agent_code,
                    task_type=step.task_type,
                )

            self._runs[run.id] = run
            span.set_attribute("workflow.run_id", run.id)
            span.set_attribute("workflow.definition_id", workflow_id)
            span.set_attribute("workflow.step_count", len(defn.steps))

            logger.info(
                "workflow_run_started",
                run_id=run.id,
                workflow_id=workflow_id,
                branch=branch,
                step_count=len(defn.steps),
            )
            return run

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    async def update_step(
        self,
        run_id: str,
        step_id: str,
        status: StepStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> StepResult | None:
        with tracer.start_as_current_span("workflow.update_step") as span:
            run = self._runs.get(run_id)
            if run is None:
                return None

            step_result = run.step_results.get(step_id)
            if step_result is None:
                return None

            now = datetime.now(UTC).isoformat()
            step_result.status = status
            if result is not None:
                step_result.task_result = result
            if error is not None:
                step_result.error = error
            if status == StepStatus.RUNNING and step_result.started_at is None:
                step_result.started_at = now
            if status in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED}:
                step_result.completed_at = now

            self._recompute_run_status(run)

            span.set_attribute("workflow.run_id", run_id)
            span.set_attribute("workflow.step_id", step_id)
            span.set_attribute("workflow.step_status", str(status))

            logger.info(
                "workflow_step_updated",
                run_id=run_id,
                step_id=step_id,
                status=str(status),
                run_status=str(run.status),
            )
            return step_result

    async def cancel_run(self, run_id: str) -> WorkflowRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None

        run.status = WorkflowStatus.CANCELLED
        run.completed_at = datetime.now(UTC).isoformat()

        for sr in run.step_results.values():
            if sr.status in {StepStatus.PENDING, StepStatus.WAITING, StepStatus.READY}:
                sr.status = StepStatus.SKIPPED
                sr.completed_at = run.completed_at

        logger.info("workflow_run_cancelled", run_id=run_id)
        return run

    async def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowStatus | None = None,
    ) -> list[WorkflowRun]:
        runs = list(self._runs.values())
        if workflow_id is not None:
            runs = [r for r in runs if r.definition_id == workflow_id]
        if status is not None:
            runs = [r for r in runs if r.status == status]
        return runs

    # -- helpers --

    @staticmethod
    def _recompute_run_status(run: WorkflowRun) -> None:
        """Derive the run status from its step results."""
        if run.status in {WorkflowStatus.CANCELLED}:
            return

        statuses = {sr.status for sr in run.step_results.values()}

        if StepStatus.FAILED in statuses:
            run.status = WorkflowStatus.FAILED
            run.completed_at = datetime.now(UTC).isoformat()
        elif all(s == StepStatus.COMPLETED for s in statuses):
            run.status = WorkflowStatus.COMPLETED
            run.completed_at = datetime.now(UTC).isoformat()
        elif any(s in {StepStatus.RUNNING, StepStatus.READY} for s in statuses):
            run.status = WorkflowStatus.RUNNING
        else:
            run.status = WorkflowStatus.RUNNING
