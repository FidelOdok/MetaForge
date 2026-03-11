"""Integration tests: Workflow DAG execution with dependencies and $ref resolution.

Tests multi-step workflows where steps have dependency relationships,
including linear chains, diamond patterns, $ref parameter resolution,
and failure propagation through the dependency graph.
"""

from __future__ import annotations

import asyncio
from typing import Any

from orchestrator.dependency_engine import DependencyGraph
from orchestrator.event_bus.events import EventType
from orchestrator.event_bus.subscribers import EventBus
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepStatus,
    WorkflowDefinition,
    WorkflowStep,
)
from tests.conftest import (
    SpySubscriber,
    make_diamond_workflow,
    make_linear_workflow,
)
from tests.integration.conftest import MockAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for_workflow_done(
    workflow_engine: InMemoryWorkflowEngine,
    run_id: str,
    expected_steps: set[str],
    timeout: float = 5.0,
) -> None:
    """Poll until all expected steps are COMPLETED or FAILED, or timeout."""
    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        run = await workflow_engine.get_run(run_id)
        if run is None:
            continue
        done = all(
            run.step_results.get(s)
            and run.step_results[s].status in {StepStatus.COMPLETED, StepStatus.FAILED}
            for s in expected_steps
            if run.step_results.get(s)
        )
        if done and len([s for s in expected_steps if run.step_results.get(s)]) == len(
            expected_steps
        ):
            return
    # Timeout — tests will fail on assertions


# ---------------------------------------------------------------------------
# Linear chain tests
# ---------------------------------------------------------------------------


class TestLinearChain:
    """A -> B -> C linear dependency chain."""

    async def test_linear_chain_executes_in_order(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        execution_order: list[str] = []

        class OrderedAgent:
            async def run_task(self, request: Any) -> dict:
                task_type = (
                    request.task_type
                    if hasattr(request, "task_type")
                    else request.get("task_type", "?")
                )
                execution_order.append(task_type)
                return {"status": "ok", "task_type": task_type, "value": f"result_{task_type}"}

        defn = make_linear_workflow()
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", OrderedAgent())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_for_workflow_done(workflow_engine, run.id, {"a", "b", "c"})
        await scheduler.stop()

        # Steps must execute in dependency order
        assert execution_order == ["validate_stress", "check_tolerances", "generate_mesh"]

    async def test_step_failure_blocks_dependents(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        call_count = 0

        class FailOnFirst:
            async def run_task(self, request: Any) -> dict:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("Step A failed")
                return {"status": "ok"}

        defn = make_linear_workflow()
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", FailOnFirst())

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
        await asyncio.sleep(0.2)  # Give time for dependents to (not) execute
        await scheduler.stop()

        run = await workflow_engine.get_run(run.id)
        assert run.step_results["a"].status == StepStatus.FAILED
        # B and C should NOT have been scheduled (remain PENDING)
        b_sr = run.step_results.get("b")
        c_sr = run.step_results.get("c")
        if b_sr:
            assert b_sr.status in {StepStatus.PENDING, StepStatus.WAITING}
        if c_sr:
            assert c_sr.status in {StepStatus.PENDING, StepStatus.WAITING}
        # Only step A was called
        assert call_count == 1


class TestRefResolution:
    """$ref:step_id.field parameter resolution between steps."""

    async def test_ref_resolves_to_previous_step_output(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        captured_params: list[dict] = []

        class ParamCapture:
            async def run_task(self, request: Any) -> dict:
                params = (
                    request.parameters
                    if hasattr(request, "parameters")
                    else request.get("parameters", {})
                )
                task_type = (
                    request.task_type
                    if hasattr(request, "task_type")
                    else request.get("task_type", "?")
                )
                captured_params.append({"task_type": task_type, "params": params})
                return {"status": "ok", "computed_value": 42, "mesh_file": "/tmp/out.inp"}

        defn = WorkflowDefinition(
            name="ref-test",
            steps=[
                WorkflowStep(
                    step_id="a",
                    agent_code="MECH",
                    task_type="validate_stress",
                    parameters={"input": "raw"},
                ),
                WorkflowStep(
                    step_id="b",
                    agent_code="MECH",
                    task_type="generate_mesh",
                    depends_on=["a"],
                    parameters={"mesh_ref": "$ref:a.mesh_file", "static": "keep"},
                ),
            ],
        )
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", ParamCapture())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_for_workflow_done(workflow_engine, run.id, {"a", "b"})
        await scheduler.stop()

        # Step B should have received resolved $ref
        assert len(captured_params) == 2
        b_params = captured_params[1]["params"]
        assert b_params.get("mesh_ref") == "/tmp/out.inp"
        assert b_params.get("static") == "keep"

    async def test_unresolvable_ref_passes_through(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        captured: list[dict] = []

        class Capture:
            async def run_task(self, request: Any) -> dict:
                params = (
                    request.parameters
                    if hasattr(request, "parameters")
                    else request.get("parameters", {})
                )
                captured.append(params)
                return {"status": "ok"}

        defn = WorkflowDefinition(
            name="bad-ref",
            steps=[
                WorkflowStep(step_id="a", agent_code="MECH", task_type="validate_stress"),
                WorkflowStep(
                    step_id="b",
                    agent_code="MECH",
                    task_type="generate_mesh",
                    depends_on=["a"],
                    parameters={"missing": "$ref:a.nonexistent_field"},
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
        scheduler.register_agent("MECH", Capture())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_for_workflow_done(workflow_engine, run.id, {"a", "b"})
        await scheduler.stop()

        # Unresolvable $ref should pass through as the original string
        assert len(captured) == 2
        assert captured[1].get("missing") == "$ref:a.nonexistent_field"


class TestDiamondWorkflow:
    """A -> (B, C) -> D diamond DAG pattern."""

    async def test_diamond_parallel_middle_converging_end(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        execution_log: list[str] = []

        class LogAgent:
            async def run_task(self, request: Any) -> dict:
                task_type = (
                    request.task_type
                    if hasattr(request, "task_type")
                    else request.get("task_type", "?")
                )
                execution_log.append(task_type)
                return {"status": "ok", "task_type": task_type}

        defn = make_diamond_workflow()
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", LogAgent())
        scheduler.register_agent("EE", LogAgent())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_for_workflow_done(workflow_engine, run.id, {"a", "b", "c", "d"})
        await scheduler.stop()

        # Step A must be first, step D must be last
        assert execution_log[0] == "validate_stress"  # step a
        assert execution_log[-1] == "generate_mesh"  # step d
        # B and C in the middle (order may vary)
        middle = set(execution_log[1:3])
        assert middle == {"check_tolerances", "run_erc"}

    async def test_event_ordering_linear_is_strict(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        defn = WorkflowDefinition(
            name="ordered",
            steps=[
                WorkflowStep(step_id="x", agent_code="MECH", task_type="validate_stress"),
                WorkflowStep(
                    step_id="y", agent_code="MECH", task_type="generate_mesh", depends_on=["x"]
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
        scheduler.register_agent("MECH", MockAgent())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_for_workflow_done(workflow_engine, run.id, {"x", "y"})
        await scheduler.stop()

        # Events for x should come before events for y
        agent_events = [
            e
            for e in spy.received
            if e.type in {EventType.AGENT_TASK_STARTED, EventType.AGENT_TASK_COMPLETED}
        ]
        # x started and completed before y started
        x_completed_idx = next(
            i
            for i, e in enumerate(agent_events)
            if e.data["step_id"] == "x" and e.type == EventType.AGENT_TASK_COMPLETED
        )
        y_started_idx = next(
            (
                i
                for i, e in enumerate(agent_events)
                if e.data["step_id"] == "y" and e.type == EventType.AGENT_TASK_STARTED
            ),
            None,
        )
        if y_started_idx is not None:
            assert x_completed_idx < y_started_idx


class TestEmptyDependencies:
    """Steps with no dependencies execute immediately."""

    async def test_no_depends_on_runs_immediately(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        mock = MockAgent()
        defn = WorkflowDefinition(
            name="immediate",
            steps=[WorkflowStep(step_id="only", agent_code="MECH", task_type="validate_stress")],
        )
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", mock)

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_for_workflow_done(workflow_engine, run.id, {"only"})
        await scheduler.stop()

        assert len(mock.calls) == 1
        run = await workflow_engine.get_run(run.id)
        assert run.step_results["only"].status == StepStatus.COMPLETED
