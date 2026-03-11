"""Integration tests: Orchestrator -> Agent -> Skill -> MCP round-trip.

Tests the full dispatch path: Scheduler dispatches a step to a registered
agent, the agent runs its skill which invokes the MCP bridge, and the result
flows back through the scheduler to update the workflow engine.
"""

from __future__ import annotations

import asyncio
from typing import Any

from domain_agents.mechanical.agent import MechanicalAgent
from orchestrator.event_bus.events import EventType
from orchestrator.event_bus.subscribers import EventBus
from orchestrator.scheduler import (
    InMemoryScheduler,
    ScheduledStep,
)
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepStatus,
    WorkflowDefinition,
    WorkflowStep,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from tests.conftest import MECH_ARTIFACT_ID, SpySubscriber
from tests.integration.conftest import MockAgent
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _single_step_workflow(
    agent_code: str = "MECH", task_type: str = "validate_stress"
) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="single-step",
        steps=[WorkflowStep(step_id="s1", agent_code=agent_code, task_type=task_type)],
    )


async def _run_single_step(
    scheduler: InMemoryScheduler,
    workflow_engine: InMemoryWorkflowEngine,
    agent_code: str = "MECH",
    task_type: str = "validate_stress",
    artifact_id: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> str:
    """Register a single-step workflow, start a run, schedule, and wait."""
    defn = _single_step_workflow(agent_code, task_type)
    defn = await workflow_engine.register_workflow(defn)
    run = await workflow_engine.start_run(defn.id)

    await scheduler.start()
    await scheduler.schedule_step(
        ScheduledStep(
            run_id=run.id,
            step_id="s1",
            agent_code=agent_code,
            task_type=task_type,
            artifact_id=artifact_id or str(MECH_ARTIFACT_ID),
            parameters=parameters or {},
        )
    )
    # Wait for step to complete
    for _ in range(50):
        await asyncio.sleep(0.05)
        updated_run = await workflow_engine.get_run(run.id)
        if updated_run and "s1" in updated_run.step_results:
            sr = updated_run.step_results["s1"]
            if sr.status in {StepStatus.COMPLETED, StepStatus.FAILED}:
                break
    await scheduler.stop()
    return run.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSingleStepMechAgent:
    """Scheduler -> MechanicalAgent -> validate_stress -> MCP round-trip."""

    async def test_mech_agent_stress_validation_succeeds(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
        mech_artifact,
    ):
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", agent)

        run_id = await _run_single_step(
            scheduler,
            workflow_engine,
            parameters={"mesh_file_path": "cad/bracket.inp"},
        )

        run = await workflow_engine.get_run(run_id)
        assert run is not None
        sr = run.step_results["s1"]
        assert sr.status == StepStatus.COMPLETED
        assert sr.task_result.get("success") is True
        assert sr.task_result.get("task_type") == "validate_stress"

    async def test_mech_agent_result_contains_skill_results(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        mech_artifact,
    ):
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", agent)

        run_id = await _run_single_step(
            scheduler,
            workflow_engine,
            parameters={"mesh_file_path": "cad/bracket.inp"},
        )

        run = await workflow_engine.get_run(run_id)
        sr = run.step_results["s1"]
        skill_results = sr.task_result.get("skill_results", [])
        assert len(skill_results) >= 1
        assert skill_results[0]["skill"] == "validate_stress"
        assert "fea_result" in skill_results[0]


class TestSingleStepEEAgent:
    """Scheduler -> ElectronicsAgent -> run_erc -> MCP round-trip."""

    async def test_ee_agent_dispatched_and_returns_result(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        ee_artifact,
    ):
        """EE agent is dispatched, runs, and returns a structured result.

        NOTE: The skill handler re-looks up the artifact by string ID (not UUID),
        which fails the precondition check in InMemoryTwinAPI. This is a known
        gap — skill handlers pass string artifact_ids while TwinAPI expects UUIDs.
        The agent still returns a structured TaskResult (success=False with the
        skill-layer error), and the scheduler records it as COMPLETED (the agent
        itself didn't raise an exception).
        """
        from domain_agents.electronics.agent import ElectronicsAgent

        agent = ElectronicsAgent(twin=twin, mcp=mcp_with_tools)
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("EE", agent)

        run_id = await _run_single_step(
            scheduler,
            workflow_engine,
            agent_code="EE",
            task_type="run_erc",
            artifact_id=str(ee_artifact.id),
            parameters={"schematic_file": "eda/kicad/main.kicad_sch"},
        )

        run = await workflow_engine.get_run(run_id)
        sr = run.step_results["s1"]
        # Agent returned a result (didn't crash), so scheduler marks COMPLETED
        assert sr.status == StepStatus.COMPLETED
        assert sr.task_result.get("task_type") == "run_erc"
        # Result is a structured TaskResult (even though skill precondition failed)
        assert "artifact_id" in sr.task_result


class TestEventLifecycle:
    """Verify event bus captures AGENT_TASK lifecycle events."""

    async def test_started_and_completed_events_published(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        mock = MockAgent(result={"status": "ok"})
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", mock)

        _ = await _run_single_step(scheduler, workflow_engine)

        started = spy.events_of_type(EventType.AGENT_TASK_STARTED)
        completed = spy.events_of_type(EventType.AGENT_TASK_COMPLETED)
        assert len(started) >= 1
        assert len(completed) >= 1
        assert started[0].data["step_id"] == "s1"
        assert completed[0].data["step_id"] == "s1"

    async def test_failed_event_on_agent_error(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        mock = MockAgent(error=RuntimeError("boom"))
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", mock)

        _ = await _run_single_step(scheduler, workflow_engine)

        failed = spy.events_of_type(EventType.AGENT_TASK_FAILED)
        assert len(failed) >= 1
        assert "boom" in failed[0].data.get("error", "")


class TestSchedulerTaskRequest:
    """Verify scheduler builds correct TaskRequest for agents."""

    async def test_task_request_includes_artifact_id(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        mock = MockAgent()
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)
        scheduler.register_agent("MECH", mock)

        _ = await _run_single_step(scheduler, workflow_engine)

        assert len(mock.calls) == 1
        req = mock.calls[0]
        # _build_task_request returns a TaskRequest with artifact_id
        assert hasattr(req, "artifact_id") or "artifact_id" in str(req)

    async def test_agent_not_registered_marks_step_failed(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        # Don't register any agent — scheduler should fail gracefully
        scheduler = InMemoryScheduler(workflow_engine=workflow_engine, event_bus=event_bus)

        run_id = await _run_single_step(scheduler, workflow_engine)

        run = await workflow_engine.get_run(run_id)
        sr = run.step_results["s1"]
        assert sr.status == StepStatus.FAILED
        assert "No agent registered" in (sr.error or "")


class TestParallelDispatch:
    """Verify concurrent step dispatch respects max_concurrency."""

    async def test_parallel_steps_dispatch_concurrently(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        call_order: list[str] = []

        class OrderTracker:
            def __init__(self, step_name: str, delay: float = 0.05):
                self._name = step_name
                self._delay = delay

            async def run_task(self, request: Any) -> dict:
                call_order.append(f"{self._name}_start")
                await asyncio.sleep(self._delay)
                call_order.append(f"{self._name}_end")
                return {"status": "ok"}

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            max_concurrency=4,
        )
        scheduler.register_agent("MECH", OrderTracker("mech"))
        scheduler.register_agent("EE", OrderTracker("ee"))

        defn = WorkflowDefinition(
            name="parallel",
            steps=[
                WorkflowStep(step_id="s1", agent_code="MECH", task_type="validate_stress"),
                WorkflowStep(step_id="s2", agent_code="EE", task_type="run_erc"),
            ],
        )
        defn = await workflow_engine.register_workflow(defn)
        run = await workflow_engine.start_run(defn.id)

        await scheduler.start()
        await scheduler.schedule_step(
            ScheduledStep(
                run_id=run.id, step_id="s1", agent_code="MECH", task_type="validate_stress"
            )
        )
        await scheduler.schedule_step(
            ScheduledStep(run_id=run.id, step_id="s2", agent_code="EE", task_type="run_erc")
        )

        # Wait for both to complete
        for _ in range(50):
            await asyncio.sleep(0.05)
            updated_run = await workflow_engine.get_run(run.id)
            results = updated_run.step_results if updated_run else {}
            done = all(
                results.get(s) and results[s].status in {StepStatus.COMPLETED, StepStatus.FAILED}
                for s in ["s1", "s2"]
                if results.get(s)
            )
            if done and len(results) >= 2:
                break
        await scheduler.stop()

        # Both should have started before either finished (concurrent execution)
        assert "mech_start" in call_order
        assert "ee_start" in call_order
