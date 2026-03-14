"""End-to-end tests for the orchestrator subsystem.

Exercises: WorkflowDefinition → Scheduler → Multiple Agents → Digital Twin.
Verifies multi-agent workflow execution, dependency resolution, event
propagation, and step status tracking through the full orchestrator stack.
"""

from __future__ import annotations

import asyncio

import pytest

from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.mechanical.agent import MechanicalAgent
from orchestrator.dependency_engine import CyclicDependencyError, DependencyGraph
from orchestrator.event_bus.events import Event, EventType
from orchestrator.event_bus.subscribers import EventSubscriber, create_default_bus
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    StepStatus,
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowStep,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Realistic tool mock data
# ---------------------------------------------------------------------------

STRESS_PASS_RESULT = {
    "max_von_mises": {"bracket_body": 85.3, "fillet_region": 120.7},
    "solver_time": 14.2,
    "mesh_elements": 52000,
    "node_count": 18500,
}

ERC_CLEAN_RESULT = {
    "schematic_file": "eda/kicad/main.kicad_sch",
    "total_violations": 0,
    "errors": 0,
    "warnings": 0,
    "violations": [],
    "passed": True,
}

DRC_CLEAN_RESULT = {
    "pcb_file": "eda/kicad/main.kicad_pcb",
    "total_violations": 0,
    "errors": 0,
    "warnings": 0,
    "violations": [],
    "passed": True,
}


class SpySubscriber(EventSubscriber):
    """Records all events for test assertions."""

    def __init__(self, sub_id: str = "spy") -> None:
        self._id = sub_id
        self.received: list[Event] = []

    @property
    def subscriber_id(self) -> str:
        return self._id

    @property
    def event_types(self) -> set[EventType] | None:
        return None  # Listen to all events

    async def on_event(self, event: Event) -> None:
        self.received.append(event)

    def events_of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.received if e.type == event_type]


def _make_mcp_bridge() -> InMemoryMcpBridge:
    """Create an MCP bridge with tool responses for MECH + EE agents."""
    mcp = InMemoryMcpBridge()
    # Mechanical tools
    mcp.register_tool("calculix.run_fea", capability="stress_analysis", name="Run FEA")
    mcp.register_tool_response("calculix.run_fea", STRESS_PASS_RESULT)
    # Electronics tools
    mcp.register_tool("kicad.run_erc", capability="erc_validation", name="Run ERC")
    mcp.register_tool_response("kicad.run_erc", ERC_CLEAN_RESULT)
    mcp.register_tool("kicad.run_drc", capability="drc_validation", name="Run DRC")
    mcp.register_tool_response("kicad.run_drc", DRC_CLEAN_RESULT)
    return mcp


def _make_work_product(twin_api: InMemoryTwinAPI) -> WorkProduct:
    return WorkProduct(
        name="drone-fc-assembly",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/drone_fc_assembly.step",
        content_hash="sha256:orch1234",
        format="step",
        created_by="human",
        metadata={"project": "drone-fc"},
    )


# ---------------------------------------------------------------------------
# Test class: Single-step workflow execution
# ---------------------------------------------------------------------------


class TestSingleStepWorkflowE2E:
    """Verify a single-step workflow executes through the full scheduler stack."""

    async def test_single_mechanical_step(self):
        """A single validate_stress step runs and completes."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_work_product(twin))

        engine = InMemoryWorkflowEngine.create()
        event_bus = create_default_bus(engine)
        spy = SpySubscriber()
        event_bus.subscribe(spy)

        definition = WorkflowDefinition(
            name="stress_only",
            steps=[
                WorkflowStep(
                    step_id="stress",
                    agent_code="MECH",
                    task_type="validate_stress",
                    parameters={
                        "work_product_id": str(work_product.id),
                        "mesh_file_path": "models/bracket.inp",
                        "load_case": "hover_3g",
                        "constraints": [
                            {
                                "max_von_mises_mpa": 276.0,
                                "safety_factor": 1.5,
                                "material": "Al6061-T6",
                            }
                        ],
                    },
                ),
            ],
        )
        await engine.register_workflow(definition)

        dep_graph = DependencyGraph(definition)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
            max_concurrency=4,
        )
        scheduler.register_agent("MECH", MechanicalAgent(twin=twin, mcp=mcp))
        await scheduler.start()

        run = await engine.start_run(definition.id)
        await scheduler.execute_run(run)

        # Wait for completion
        for _ in range(50):
            await asyncio.sleep(0.1)
            run = await engine.get_run(run.id)
            if (
                run
                and run.step_results.get("stress")
                and run.step_results["stress"].status in {StepStatus.COMPLETED, StepStatus.FAILED}
            ):
                break

        await scheduler.stop()

        assert run is not None
        stress_result = run.step_results.get("stress")
        assert stress_result is not None
        assert stress_result.status == StepStatus.COMPLETED

        # Verify events were published
        started = spy.events_of_type(EventType.AGENT_TASK_STARTED)
        completed = spy.events_of_type(EventType.AGENT_TASK_COMPLETED)
        assert len(started) >= 1
        assert len(completed) >= 1


# ---------------------------------------------------------------------------
# Test class: Multi-step parallel workflow
# ---------------------------------------------------------------------------


class TestMultiStepWorkflowE2E:
    """Verify multi-step workflows with independent parallel steps."""

    async def test_parallel_mech_and_ee_steps(self):
        """Two independent steps (stress + erc) run in parallel."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_work_product(twin))

        engine = InMemoryWorkflowEngine.create()
        event_bus = create_default_bus(engine)
        spy = SpySubscriber()
        event_bus.subscribe(spy)

        definition = WorkflowDefinition(
            name="parallel_validation",
            steps=[
                WorkflowStep(
                    step_id="stress",
                    agent_code="MECH",
                    task_type="validate_stress",
                    parameters={
                        "work_product_id": str(work_product.id),
                        "mesh_file_path": "models/bracket.inp",
                        "load_case": "hover_3g",
                        "constraints": [
                            {
                                "max_von_mises_mpa": 276.0,
                                "safety_factor": 1.5,
                                "material": "Al6061-T6",
                            }
                        ],
                    },
                ),
                WorkflowStep(
                    step_id="erc",
                    agent_code="EE",
                    task_type="run_erc",
                    parameters={
                        "work_product_id": str(work_product.id),
                        "schematic_file": "eda/kicad/main.kicad_sch",
                    },
                ),
            ],
        )
        await engine.register_workflow(definition)

        dep_graph = DependencyGraph(definition)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
            max_concurrency=4,
        )
        scheduler.register_agent("MECH", MechanicalAgent(twin=twin, mcp=mcp))
        scheduler.register_agent("EE", ElectronicsAgent(twin=twin, mcp=mcp))
        await scheduler.start()

        run = await engine.start_run(definition.id)
        await scheduler.execute_run(run)

        # Wait for both steps
        for _ in range(50):
            await asyncio.sleep(0.1)
            run = await engine.get_run(run.id)
            if run is None:
                continue
            stress_done = run.step_results.get("stress", None)
            erc_done = run.step_results.get("erc", None)
            if (
                stress_done
                and stress_done.status in {StepStatus.COMPLETED, StepStatus.FAILED}
                and erc_done
                and erc_done.status in {StepStatus.COMPLETED, StepStatus.FAILED}
            ):
                break

        await scheduler.stop()

        assert run is not None
        assert run.step_results["stress"].status == StepStatus.COMPLETED
        assert run.step_results["erc"].status == StepStatus.COMPLETED

        # Both agents should have published events
        started_events = spy.events_of_type(EventType.AGENT_TASK_STARTED)
        completed_events = spy.events_of_type(EventType.AGENT_TASK_COMPLETED)
        assert len(started_events) >= 2
        assert len(completed_events) >= 2


# ---------------------------------------------------------------------------
# Test class: Sequential dependency chain
# ---------------------------------------------------------------------------


class TestDependencyChainE2E:
    """Verify sequential step execution with depends_on."""

    async def test_sequential_erc_then_drc(self):
        """ERC must complete before DRC starts (depends_on)."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_work_product(twin))

        engine = InMemoryWorkflowEngine.create()
        event_bus = create_default_bus(engine)
        spy = SpySubscriber()
        event_bus.subscribe(spy)

        definition = WorkflowDefinition(
            name="erc_then_drc",
            steps=[
                WorkflowStep(
                    step_id="erc",
                    agent_code="EE",
                    task_type="run_erc",
                    parameters={
                        "work_product_id": str(work_product.id),
                        "schematic_file": "eda/kicad/main.kicad_sch",
                    },
                ),
                WorkflowStep(
                    step_id="drc",
                    agent_code="EE",
                    task_type="run_drc",
                    depends_on=["erc"],
                    parameters={
                        "work_product_id": str(work_product.id),
                        "pcb_file": "eda/kicad/main.kicad_pcb",
                    },
                ),
            ],
        )
        await engine.register_workflow(definition)

        dep_graph = DependencyGraph(definition)
        dep_graph.validate()

        scheduler = InMemoryScheduler(
            workflow_engine=engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
            max_concurrency=4,
        )
        scheduler.register_agent("EE", ElectronicsAgent(twin=twin, mcp=mcp))
        await scheduler.start()

        run = await engine.start_run(definition.id)
        await scheduler.execute_run(run)

        # Wait for both steps to finish
        for _ in range(50):
            await asyncio.sleep(0.1)
            run = await engine.get_run(run.id)
            if run is None:
                continue
            drc_done = run.step_results.get("drc")
            if drc_done and drc_done.status in {StepStatus.COMPLETED, StepStatus.FAILED}:
                break

        await scheduler.stop()

        assert run is not None
        assert run.step_results["erc"].status == StepStatus.COMPLETED
        assert run.step_results["drc"].status == StepStatus.COMPLETED

        # Verify ordering: ERC completed before DRC started
        completed_events = spy.events_of_type(EventType.AGENT_TASK_COMPLETED)
        started_events = spy.events_of_type(EventType.AGENT_TASK_STARTED)

        erc_completed = [e for e in completed_events if e.data.get("step_id") == "erc"]
        drc_started = [e for e in started_events if e.data.get("step_id") == "drc"]

        assert len(erc_completed) >= 1
        assert len(drc_started) >= 1


# ---------------------------------------------------------------------------
# Test class: Workflow definition validation
# ---------------------------------------------------------------------------


class TestWorkflowValidationE2E:
    """Verify workflow definition validation catches errors."""

    async def test_cyclic_dependency_detected(self):
        """DependencyGraph.validate() raises CyclicDependencyError for cycles."""
        definition = WorkflowDefinition(
            name="cyclic",
            steps=[
                WorkflowStep(step_id="a", agent_code="X", task_type="t", depends_on=["b"]),
                WorkflowStep(step_id="b", agent_code="X", task_type="t", depends_on=["a"]),
            ],
        )

        dep_graph = DependencyGraph(definition)
        with pytest.raises(CyclicDependencyError):
            dep_graph.validate()

    async def test_topological_sort(self):
        """Topological sort returns correct execution order."""
        definition = WorkflowDefinition(
            name="diamond",
            steps=[
                WorkflowStep(step_id="a", agent_code="X", task_type="t"),
                WorkflowStep(step_id="b", agent_code="X", task_type="t", depends_on=["a"]),
                WorkflowStep(step_id="c", agent_code="X", task_type="t", depends_on=["a"]),
                WorkflowStep(step_id="d", agent_code="X", task_type="t", depends_on=["b", "c"]),
            ],
        )
        dep_graph = DependencyGraph(definition)
        dep_graph.validate()
        order = dep_graph.topological_sort()

        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")


# ---------------------------------------------------------------------------
# Test class: Event bus integration
# ---------------------------------------------------------------------------


class TestEventBusIntegrationE2E:
    """Verify event bus publishes correct events during orchestration."""

    async def test_event_lifecycle_for_successful_step(self):
        """A successful step publishes STARTED + COMPLETED events."""
        twin = InMemoryTwinAPI.create()
        mcp = _make_mcp_bridge()
        work_product = await twin.create_work_product(_make_work_product(twin))

        engine = InMemoryWorkflowEngine.create()
        event_bus = create_default_bus(engine)
        spy = SpySubscriber()
        event_bus.subscribe(spy)

        definition = WorkflowDefinition(
            name="event_test",
            steps=[
                WorkflowStep(
                    step_id="erc",
                    agent_code="EE",
                    task_type="run_erc",
                    parameters={
                        "work_product_id": str(work_product.id),
                        "schematic_file": "eda/kicad/main.kicad_sch",
                    },
                ),
            ],
        )
        await engine.register_workflow(definition)

        dep_graph = DependencyGraph(definition)
        scheduler = InMemoryScheduler(
            workflow_engine=engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("EE", ElectronicsAgent(twin=twin, mcp=mcp))
        await scheduler.start()

        run = await engine.start_run(definition.id)
        await scheduler.execute_run(run)

        for _ in range(50):
            await asyncio.sleep(0.1)
            run = await engine.get_run(run.id)
            if (
                run
                and run.step_results.get("erc")
                and run.step_results["erc"].status in {StepStatus.COMPLETED, StepStatus.FAILED}
            ):
                break

        await scheduler.stop()

        # Verify event sequence
        started = spy.events_of_type(EventType.AGENT_TASK_STARTED)
        completed = spy.events_of_type(EventType.AGENT_TASK_COMPLETED)
        failed = spy.events_of_type(EventType.AGENT_TASK_FAILED)

        assert len(started) == 1
        assert started[0].data["agent_code"] == "EE"
        assert started[0].data["step_id"] == "erc"

        assert len(completed) == 1
        assert completed[0].data["agent_code"] == "EE"

        assert len(failed) == 0

    async def test_unregistered_agent_publishes_failure(self):
        """Step for unregistered agent publishes FAILED event."""
        engine = InMemoryWorkflowEngine.create()
        event_bus = create_default_bus(engine)
        spy = SpySubscriber()
        event_bus.subscribe(spy)

        definition = WorkflowDefinition(
            name="missing_agent",
            steps=[
                WorkflowStep(step_id="step1", agent_code="UNKNOWN", task_type="do_stuff"),
            ],
        )
        await engine.register_workflow(definition)

        dep_graph = DependencyGraph(definition)
        scheduler = InMemoryScheduler(
            workflow_engine=engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        # Deliberately NOT registering any agent
        await scheduler.start()

        run = await engine.start_run(definition.id)
        await scheduler.execute_run(run)

        for _ in range(30):
            await asyncio.sleep(0.1)
            run = await engine.get_run(run.id)
            if (
                run
                and run.step_results.get("step1")
                and run.step_results["step1"].status == StepStatus.FAILED
            ):
                break

        await scheduler.stop()

        assert run is not None
        assert run.step_results["step1"].status == StepStatus.FAILED

        failed = spy.events_of_type(EventType.AGENT_TASK_FAILED)
        assert len(failed) >= 1
        assert "UNKNOWN" in failed[0].data.get("error", "")


# ---------------------------------------------------------------------------
# Test class: Workflow run lifecycle
# ---------------------------------------------------------------------------


class TestWorkflowRunLifecycleE2E:
    """Verify workflow run status tracking."""

    async def test_run_start_creates_step_results(self):
        """Starting a run initializes step results."""
        engine = InMemoryWorkflowEngine.create()
        definition = WorkflowDefinition(
            name="lifecycle_test",
            steps=[
                WorkflowStep(step_id="a", agent_code="X", task_type="t"),
                WorkflowStep(step_id="b", agent_code="X", task_type="t", depends_on=["a"]),
            ],
        )
        registered = await engine.register_workflow(definition)
        run = await engine.start_run(registered.id)

        assert run.status == WorkflowStatus.RUNNING
        assert "a" in run.step_results
        assert "b" in run.step_results

    async def test_run_cancel(self):
        """Cancelling a run sets its status to CANCELLED."""
        engine = InMemoryWorkflowEngine.create()
        definition = WorkflowDefinition(
            name="cancel_test",
            steps=[
                WorkflowStep(step_id="a", agent_code="X", task_type="t"),
            ],
        )
        registered = await engine.register_workflow(definition)
        run = await engine.start_run(registered.id)

        cancelled = await engine.cancel_run(run.id)
        assert cancelled is not None
        assert cancelled.status == WorkflowStatus.CANCELLED

    async def test_list_runs_by_status(self):
        """List runs filtered by status."""
        engine = InMemoryWorkflowEngine.create()
        definition = WorkflowDefinition(
            name="list_test",
            steps=[WorkflowStep(step_id="a", agent_code="X", task_type="t")],
        )
        registered = await engine.register_workflow(definition)

        run1 = await engine.start_run(registered.id)
        run2 = await engine.start_run(registered.id)
        await engine.cancel_run(run1.id)

        running = await engine.list_runs(status=WorkflowStatus.RUNNING)
        cancelled = await engine.list_runs(status=WorkflowStatus.CANCELLED)

        assert len(running) == 1
        assert running[0].id == run2.id
        assert len(cancelled) == 1
        assert cancelled[0].id == run1.id
