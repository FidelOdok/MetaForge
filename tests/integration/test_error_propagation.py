"""Integration tests: Error propagation and retry behavior.

Tests failure cascades through the system: MCP errors -> skill errors ->
agent failures -> step failures -> workflow failures, plus retry logic
with exponential backoff.
"""

from __future__ import annotations

import asyncio
from typing import Any

from orchestrator.dependency_engine import DependencyGraph
from orchestrator.event_bus.events import EventType
from orchestrator.event_bus.subscribers import EventBus
from orchestrator.scheduler import (
    InMemoryScheduler,
    RetryPolicy,
    ScheduledStep,
)
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepStatus,
    WorkflowDefinition,
    WorkflowStep,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge, McpTimeoutError
from tests.conftest import SpySubscriber
from tests.integration.conftest import FlakeyAgent, MockAgent
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_step_and_wait(
    scheduler: InMemoryScheduler,
    engine: InMemoryWorkflowEngine,
    step: ScheduledStep,
    timeout: float = 5.0,
) -> None:
    """Schedule a single step, start scheduler, wait for completion/failure."""
    defn = WorkflowDefinition(
        name="err-test",
        steps=[
            WorkflowStep(
                step_id=step.step_id,
                agent_code=step.agent_code,
                task_type=step.task_type,
                retry_max=step.retry_policy.max_retries,
            )
        ],
    )
    defn = await engine.register_workflow(defn)
    run = await engine.start_run(defn.id)

    # Update step with correct run_id
    step = step.model_copy(update={"run_id": run.id})

    await scheduler.start()
    await scheduler.schedule_step(step)

    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        updated = await engine.get_run(run.id)
        if updated and updated.step_results.get(step.step_id):
            sr = updated.step_results[step.step_id]
            if sr.status in {StepStatus.COMPLETED, StepStatus.FAILED}:
                break
    await scheduler.stop()


# ---------------------------------------------------------------------------
# MCP error propagation
# ---------------------------------------------------------------------------


class TestMcpErrorPropagation:
    """MCP tool errors propagate through agent to scheduler."""

    async def test_mcp_tool_not_registered_causes_agent_error_result(
        self,
        twin: InMemoryTwinAPI,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
        mech_work_product,
    ):
        """MCP tool not registered -> agent catches the error and returns
        TaskResult(success=False) rather than raising an exception.

        The scheduler marks the step as COMPLETED (the agent returned a result),
        but the result indicates failure. This tests the error containment
        pattern: MCP errors are caught by the agent, not propagated as exceptions.
        """
        from domain_agents.mechanical.agent import MechanicalAgent

        # MCP bridge with NO tools registered
        bare_mcp = InMemoryMcpBridge()
        agent = MechanicalAgent(twin=twin, mcp=bare_mcp)

        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", agent)

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
            work_product_id=str(mech_work_product.id),
            parameters={"mesh_file_path": "cad/bracket.inp"},
        )
        await _run_step_and_wait(scheduler, workflow_engine, step)

        runs = await workflow_engine.list_runs()
        assert len(runs) >= 1
        run = runs[-1]
        sr = run.step_results.get("s1")
        assert sr is not None
        # Agent caught the MCP error and returned TaskResult — scheduler sees success
        assert sr.status == StepStatus.COMPLETED
        # But the result itself indicates failure
        assert sr.task_result.get("success") is False
        assert any(
            "failed" in e.lower() or "not registered" in e.lower()
            for e in sr.task_result.get("errors", [])
        )

    async def test_mcp_timeout_propagates(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        class TimeoutAgent:
            async def run_task(self, request: Any) -> dict:
                raise McpTimeoutError("calculix.run_fea", 30.0)

        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", TimeoutAgent())

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
        )
        await _run_step_and_wait(scheduler, workflow_engine, step)

        runs = await workflow_engine.list_runs()
        run = runs[-1]
        sr = run.step_results.get("s1")
        assert sr is not None
        assert sr.status == StepStatus.FAILED


# ---------------------------------------------------------------------------
# Agent exception propagation
# ---------------------------------------------------------------------------


class TestAgentExceptionPropagation:
    """Agent exceptions cascade to step -> workflow failure."""

    async def test_agent_exception_marks_step_failed(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        mock = MockAgent(error=RuntimeError("Agent crashed"))
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", mock)

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
        )
        await _run_step_and_wait(scheduler, workflow_engine, step)

        runs = await workflow_engine.list_runs()
        run = runs[-1]
        sr = run.step_results.get("s1")
        assert sr is not None
        assert sr.status == StepStatus.FAILED
        assert "crashed" in (sr.error or "").lower()

    async def test_failed_event_published(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        mock = MockAgent(error=ValueError("bad input"))
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", mock)

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
        )
        await _run_step_and_wait(scheduler, workflow_engine, step)

        failed_events = spy.events_of_type(EventType.AGENT_TASK_FAILED)
        assert len(failed_events) >= 1
        assert "bad input" in failed_events[0].data.get("error", "")

    async def test_error_message_preserved_in_step_result(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        mock = MockAgent(error=RuntimeError("Specific error: code 42"))
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", mock)

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
        )
        await _run_step_and_wait(scheduler, workflow_engine, step)

        runs = await workflow_engine.list_runs()
        run = runs[-1]
        assert "Specific error: code 42" in (run.step_results["s1"].error or "")


# ---------------------------------------------------------------------------
# Multi-step failure blocking
# ---------------------------------------------------------------------------


class TestMultiStepFailure:
    """Step A failure prevents step B from executing."""

    async def test_failure_blocks_dependent(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        call_log: list[str] = []

        class FailFirst:
            async def run_task(self, request: Any) -> dict:
                task_type = (
                    request.task_type
                    if hasattr(request, "task_type")
                    else request.get("task_type", "?")
                )
                call_log.append(task_type)
                if task_type == "validate_stress":
                    raise RuntimeError("Step A failed")
                return {"status": "ok"}

        defn = WorkflowDefinition(
            name="fail-chain",
            steps=[
                WorkflowStep(step_id="a", agent_code="MECH", task_type="validate_stress"),
                WorkflowStep(
                    step_id="b", agent_code="MECH", task_type="generate_mesh", depends_on=["a"]
                ),
            ],
        )
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", FailFirst())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)

        # Wait for step A to fail
        for _ in range(50):
            await asyncio.sleep(0.05)
            updated = await workflow_engine.get_run(run.id)
            if updated and updated.step_results.get("a"):
                if updated.step_results["a"].status == StepStatus.FAILED:
                    break
        await asyncio.sleep(0.3)  # Extra time to verify B doesn't start
        await scheduler.stop()

        # Only step A should have been called
        assert call_log == ["validate_stress"]


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    """Retry-then-succeed and retry-exhausted patterns."""

    async def test_flakey_agent_retry_then_succeed(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        agent = FlakeyAgent(fail_count=1, result={"status": "recovered"})
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", agent)

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
            retry_policy=RetryPolicy(max_retries=1, backoff_seconds=0.01),
        )
        await _run_step_and_wait(scheduler, workflow_engine, step, timeout=10.0)

        runs = await workflow_engine.list_runs()
        run = runs[-1]
        sr = run.step_results.get("s1")
        assert sr is not None
        assert sr.status == StepStatus.COMPLETED
        assert agent.call_count == 2  # Failed once, succeeded on retry

    async def test_retry_exhausted_marks_failed(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        agent = FlakeyAgent(fail_count=5)  # Always fails within retry budget
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", agent)

        step = ScheduledStep(
            run_id="placeholder",
            step_id="s1",
            agent_code="MECH",
            task_type="validate_stress",
            retry_policy=RetryPolicy(max_retries=2, backoff_seconds=0.01),
        )
        await _run_step_and_wait(scheduler, workflow_engine, step, timeout=10.0)

        runs = await workflow_engine.list_runs()
        run = runs[-1]
        sr = run.step_results.get("s1")
        assert sr is not None
        assert sr.status == StepStatus.FAILED
        # Should have been called 3 times total (1 initial + 2 retries)
        assert agent.call_count == 3
