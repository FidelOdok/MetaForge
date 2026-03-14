"""Scheduler — priority-based step dispatch with concurrency control and retry.

The ``InMemoryScheduler`` manages an ``asyncio.PriorityQueue`` of steps and
dispatches them to registered agents, respecting a concurrency semaphore.
Failed steps are retried with exponential backoff.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from observability.metrics import MetricsCollector
from observability.tracing import get_tracer
from orchestrator.dependency_engine import DependencyGraph
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventBus
from orchestrator.workflow_dag import (
    StepStatus,
    WorkflowEngine,
    WorkflowRun,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.scheduler")


# ---------------------------------------------------------------------------
# Agent protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentProtocol(Protocol):
    """Any object that can run a task request."""

    async def run_task(self, request: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SchedulerPriority(IntEnum):
    URGENT = 0
    NORMAL = 5
    LOW = 10


class RetryPolicy(BaseModel):
    max_retries: int = 0
    backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0


class ScheduledStep(BaseModel):
    """A step queued for execution."""

    run_id: str
    step_id: str
    agent_code: str
    task_type: str
    work_product_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    branch: str = "main"
    priority: SchedulerPriority = SchedulerPriority.NORMAL
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Abstract scheduler
# ---------------------------------------------------------------------------


class Scheduler(ABC):
    @abstractmethod
    async def schedule_step(self, step: ScheduledStep) -> None: ...

    @abstractmethod
    async def cancel_step(self, run_id: str, step_id: str) -> bool: ...

    @abstractmethod
    def get_queue_size(self) -> int: ...

    @abstractmethod
    def get_active_count(self) -> int: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryScheduler(Scheduler):
    """Asyncio-based scheduler with priority queue and concurrency limit."""

    def __init__(
        self,
        workflow_engine: WorkflowEngine,
        event_bus: EventBus | None = None,
        dependency_graph: DependencyGraph | None = None,
        max_concurrency: int = 4,
        collector: MetricsCollector | None = None,
    ) -> None:
        self._engine = workflow_engine
        self._bus = event_bus
        self._dep_graph = dependency_graph
        self._collector = collector
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._queue: asyncio.PriorityQueue[tuple[int, float, ScheduledStep]] = (
            asyncio.PriorityQueue()
        )
        self._agents: dict[str, AgentProtocol] = {}
        self._active: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None

    def register_agent(self, agent_code: str, agent: AgentProtocol) -> None:
        self._agents[agent_code] = agent

    async def schedule_step(self, step: ScheduledStep) -> None:
        await self._queue.put((step.priority, time.monotonic(), step))
        logger.info(
            "step_scheduled",
            run_id=step.run_id,
            step_id=step.step_id,
            agent_code=step.agent_code,
            priority=step.priority.name,
        )

    async def cancel_step(self, run_id: str, step_id: str) -> bool:
        key = f"{run_id}:{step_id}"
        task = self._active.get(key)
        if task is not None and not task.done():
            task.cancel()
            self._cancelled.add(key)
            logger.info("step_cancelled", run_id=run_id, step_id=step_id)
            return True
        self._cancelled.add(key)
        return False

    def get_queue_size(self) -> int:
        return self._queue.qsize()

    def get_active_count(self) -> int:
        return sum(1 for t in self._active.values() if not t.done())

    async def start(self) -> None:
        """Start the dispatch loop."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the dispatch loop gracefully."""
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while self._running:
            try:
                priority, ts, step = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            key = f"{step.run_id}:{step.step_id}"
            if key in self._cancelled:
                self._cancelled.discard(key)
                continue

            await self._semaphore.acquire()
            task = asyncio.create_task(self._execute_step(step))
            self._active[key] = task
            task.add_done_callback(lambda _t, _k=key: self._on_step_done(_k))

    def _on_step_done(self, key: str) -> None:
        self._semaphore.release()
        self._active.pop(key, None)

    async def _execute_step(self, step: ScheduledStep) -> None:
        with tracer.start_as_current_span("scheduler.execute_step") as span:
            span.set_attribute("scheduler.run_id", step.run_id)
            span.set_attribute("scheduler.step_id", step.step_id)
            span.set_attribute("scheduler.agent_code", step.agent_code)
            span.set_attribute("scheduler.retry_count", step.retry_count)

            agent = self._agents.get(step.agent_code)
            if agent is None:
                logger.error(
                    "agent_not_found",
                    agent_code=step.agent_code,
                    step_id=step.step_id,
                )
                await self._report_step_failure(
                    step, f"No agent registered for '{step.agent_code}'"
                )
                return

            # Mark step as running
            await self._engine.update_step(step.run_id, step.step_id, StepStatus.RUNNING)
            if self._bus:
                await self._bus.publish(
                    Event(
                        id=str(time.monotonic()),
                        type=EventType.AGENT_TASK_STARTED,
                        timestamp=datetime.now(UTC).isoformat(),
                        source="scheduler",
                        data={
                            "run_id": step.run_id,
                            "step_id": step.step_id,
                            "agent_code": step.agent_code,
                        },
                    )
                )

            t0 = time.monotonic()
            try:
                # Build a TaskRequest-compatible dict
                request = _build_task_request(step)
                result = await asyncio.wait_for(
                    agent.run_task(request),
                    timeout=float(step.parameters.get("_timeout", 300)),
                )
                elapsed = time.monotonic() - t0
                span.set_attribute("scheduler.duration_s", round(elapsed, 3))
                if self._collector:
                    self._collector.record_agent_execution(step.agent_code, "success", elapsed)
                await self._report_step_success(step, result)

            except TimeoutError:
                elapsed = time.monotonic() - t0
                span.set_attribute("scheduler.duration_s", round(elapsed, 3))
                if self._collector:
                    self._collector.record_agent_execution(step.agent_code, "timeout", elapsed)
                logger.warning(
                    "step_timeout",
                    run_id=step.run_id,
                    step_id=step.step_id,
                    elapsed=round(elapsed, 2),
                )
                await self._handle_failure(step, "Step timed out")

            except asyncio.CancelledError:
                await self._engine.update_step(step.run_id, step.step_id, StepStatus.SKIPPED)

            except Exception as exc:
                elapsed = time.monotonic() - t0
                span.set_attribute("scheduler.duration_s", round(elapsed, 3))
                span.record_exception(exc)
                if self._collector:
                    self._collector.record_agent_execution(step.agent_code, "error", elapsed)
                logger.error(
                    "step_execution_error",
                    run_id=step.run_id,
                    step_id=step.step_id,
                    error=str(exc),
                )
                await self._handle_failure(step, str(exc))

    async def _report_step_success(self, step: ScheduledStep, result: Any) -> None:
        result_dict: dict[str, Any] = {}
        if isinstance(result, dict):
            result_dict = result
        elif hasattr(result, "model_dump"):
            result_dict = result.model_dump()

        await self._engine.update_step(
            step.run_id,
            step.step_id,
            StepStatus.COMPLETED,
            result=result_dict,
        )

        if self._bus:
            await self._bus.publish(
                Event(
                    id=str(time.monotonic()),
                    type=EventType.AGENT_TASK_COMPLETED,
                    timestamp=datetime.now(UTC).isoformat(),
                    source="scheduler",
                    data={
                        "run_id": step.run_id,
                        "step_id": step.step_id,
                        "agent_code": step.agent_code,
                        "result": result_dict,
                    },
                )
            )

        logger.info(
            "step_completed",
            run_id=step.run_id,
            step_id=step.step_id,
            agent_code=step.agent_code,
        )

        # Schedule newly ready dependents
        await self._schedule_ready_dependents(step)

    async def _report_step_failure(self, step: ScheduledStep, error: str) -> None:
        await self._engine.update_step(
            step.run_id,
            step.step_id,
            StepStatus.FAILED,
            error=error,
        )
        if self._bus:
            await self._bus.publish(
                Event(
                    id=str(time.monotonic()),
                    type=EventType.AGENT_TASK_FAILED,
                    timestamp=datetime.now(UTC).isoformat(),
                    source="scheduler",
                    data={
                        "run_id": step.run_id,
                        "step_id": step.step_id,
                        "agent_code": step.agent_code,
                        "error": error,
                    },
                )
            )

    async def _handle_failure(self, step: ScheduledStep, error: str) -> None:
        if step.retry_count < step.retry_policy.max_retries:
            await self._retry_step(step)
        else:
            await self._report_step_failure(step, error)

    async def _retry_step(self, step: ScheduledStep) -> None:
        delay = step.retry_policy.backoff_seconds * (
            step.retry_policy.backoff_multiplier**step.retry_count
        )
        step.retry_count += 1
        logger.info(
            "step_retry",
            run_id=step.run_id,
            step_id=step.step_id,
            retry=step.retry_count,
            delay=round(delay, 2),
        )
        await asyncio.sleep(delay)
        await self.schedule_step(step)

    async def _schedule_ready_dependents(self, step: ScheduledStep) -> None:
        if self._dep_graph is None:
            return

        run = await self._engine.get_run(step.run_id)
        if run is None:
            return

        ready = self._dep_graph.get_ready_steps(run)
        for step_id in ready:
            sr = run.step_results.get(step_id)
            if sr is None or sr.status != StepStatus.READY:
                # Already past READY state (e.g. RUNNING)
                ws = self._dep_graph.get_step(step_id)
                if ws is None:
                    continue
                if sr is not None and sr.status not in {
                    StepStatus.PENDING,
                    StepStatus.WAITING,
                    StepStatus.READY,
                }:
                    continue

            ws = self._dep_graph.get_step(step_id)
            if ws is None:
                continue

            # Mark as READY in engine
            await self._engine.update_step(step.run_id, step_id, StepStatus.READY)

            # Resolve $ref parameters
            completed_results = {
                sid: sr_item.task_result
                for sid, sr_item in run.step_results.items()
                if sr_item.status == StepStatus.COMPLETED
            }
            resolved_params = self._dep_graph.resolve_step_inputs(ws, completed_results)

            await self.schedule_step(
                ScheduledStep(
                    run_id=step.run_id,
                    step_id=step_id,
                    agent_code=ws.agent_code,
                    task_type=ws.task_type,
                    parameters=resolved_params,
                    branch=run.branch,
                    priority=SchedulerPriority.NORMAL,
                    retry_policy=RetryPolicy(max_retries=ws.retry_max),
                )
            )

    async def execute_run(self, run: WorkflowRun) -> None:
        """Schedule all initially-ready steps for *run*."""
        if self._dep_graph is None:
            return

        ready = self._dep_graph.get_ready_steps(run)
        for step_id in ready:
            ws = self._dep_graph.get_step(step_id)
            if ws is None:
                continue
            await self.schedule_step(
                ScheduledStep(
                    run_id=run.id,
                    step_id=step_id,
                    agent_code=ws.agent_code,
                    task_type=ws.task_type,
                    parameters=dict(ws.parameters),
                    branch=run.branch,
                    priority=SchedulerPriority.NORMAL,
                    retry_policy=RetryPolicy(max_retries=ws.retry_max),
                )
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_task_request(step: ScheduledStep) -> Any:
    """Build a TaskRequest-like object for the agent.

    Returns a plain dict so it works with any agent implementation.
    """
    from domain_agents.mechanical.agent import TaskRequest

    work_product_id = step.work_product_id or step.parameters.get("work_product_id")
    if work_product_id is None:
        # Return raw dict if no work_product_id — agent decides how to handle
        return {
            "task_type": step.task_type,
            "parameters": step.parameters,
            "branch": step.branch,
        }
    return TaskRequest(
        task_type=step.task_type,
        work_product_id=UUID(work_product_id)
        if isinstance(work_product_id, str)
        else work_product_id,
        parameters=step.parameters,
        branch=step.branch,
    )
