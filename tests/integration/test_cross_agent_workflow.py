"""Integration tests: Cross-agent workflows (Mechanical + Electronics).

Tests multi-agent workflows where both mechanical and electronics agents
participate in the same workflow, sharing Twin state and passing data
between each other via $ref resolution.
"""

from __future__ import annotations

import asyncio
from typing import Any

from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.mechanical.agent import MechanicalAgent
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
from skill_registry.mcp_bridge import InMemoryMcpBridge
from tests.conftest import SpySubscriber
from twin_core.api import InMemoryTwinAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_done(
    engine: InMemoryWorkflowEngine, run_id: str, steps: set[str], timeout: float = 5.0
) -> None:
    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        run = await engine.get_run(run_id)
        if run is None:
            continue
        done_count = sum(
            1
            for s in steps
            if run.step_results.get(s)
            and run.step_results[s].status in {StepStatus.COMPLETED, StepStatus.FAILED}
        )
        if done_count == len(steps):
            return


# ---------------------------------------------------------------------------
# Sequential cross-agent workflow
# ---------------------------------------------------------------------------


class TestSequentialCrossAgent:
    """MECH -> EE sequential workflow."""

    async def test_mech_then_ee_sequential(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
        mech_artifact,
        ee_artifact,
    ):
        mech_agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        ee_agent = ElectronicsAgent(twin=twin, mcp=mcp_with_tools)

        defn = WorkflowDefinition(
            name="mech-then-ee",
            steps=[
                WorkflowStep(
                    step_id="stress",
                    agent_code="MECH",
                    task_type="validate_stress",
                    parameters={
                        "artifact_id": str(mech_artifact.id),
                        "mesh_file_path": "cad/bracket.inp",
                    },
                ),
                WorkflowStep(
                    step_id="erc",
                    agent_code="EE",
                    task_type="run_erc",
                    depends_on=["stress"],
                    parameters={
                        "artifact_id": str(ee_artifact.id),
                        "schematic_file": "eda/kicad/main.kicad_sch",
                    },
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
        scheduler.register_agent("MECH", mech_agent)
        scheduler.register_agent("EE", ee_agent)

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_done(workflow_engine, run.id, {"stress", "erc"})
        await scheduler.stop()

        run = await workflow_engine.get_run(run.id)
        assert run.step_results["stress"].status == StepStatus.COMPLETED
        assert run.step_results["erc"].status == StepStatus.COMPLETED


class TestParallelCrossAgent:
    """MECH and EE running in parallel."""

    async def test_mech_and_ee_parallel(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        mech_artifact,
        ee_artifact,
    ):
        mech_agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        ee_agent = ElectronicsAgent(twin=twin, mcp=mcp_with_tools)

        defn = WorkflowDefinition(
            name="parallel-agents",
            steps=[
                WorkflowStep(
                    step_id="stress",
                    agent_code="MECH",
                    task_type="validate_stress",
                    parameters={
                        "artifact_id": str(mech_artifact.id),
                        "mesh_file_path": "cad/bracket.inp",
                    },
                ),
                WorkflowStep(
                    step_id="erc",
                    agent_code="EE",
                    task_type="run_erc",
                    parameters={
                        "artifact_id": str(ee_artifact.id),
                        "schematic_file": "eda/kicad/main.kicad_sch",
                    },
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
        scheduler.register_agent("MECH", mech_agent)
        scheduler.register_agent("EE", ee_agent)

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_done(workflow_engine, run.id, {"stress", "erc"})
        await scheduler.stop()

        run = await workflow_engine.get_run(run.id)
        assert run.step_results["stress"].status == StepStatus.COMPLETED
        assert run.step_results["erc"].status == StepStatus.COMPLETED


class TestCrossAgentRefResolution:
    """Cross-agent $ref resolution: MECH output -> EE input."""

    async def test_mech_output_feeds_ee_input(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
    ):
        captured: list[dict] = []

        class CaptureMech:
            async def run_task(self, request: Any) -> dict:
                return {"status": "ok", "mesh_quality": "high", "safety_factor": 2.1}

        class CaptureEE:
            async def run_task(self, request: Any) -> dict:
                params = (
                    request.parameters
                    if hasattr(request, "parameters")
                    else request.get("parameters", {})
                )
                captured.append(params)
                return {"status": "ok"}

        defn = WorkflowDefinition(
            name="cross-ref",
            steps=[
                WorkflowStep(step_id="mech", agent_code="MECH", task_type="validate_stress"),
                WorkflowStep(
                    step_id="ee",
                    agent_code="EE",
                    task_type="run_erc",
                    depends_on=["mech"],
                    parameters={"upstream_quality": "$ref:mech.mesh_quality"},
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
        scheduler.register_agent("MECH", CaptureMech())
        scheduler.register_agent("EE", CaptureEE())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_done(workflow_engine, run.id, {"mech", "ee"})
        await scheduler.stop()

        assert len(captured) == 1
        assert captured[0]["upstream_quality"] == "high"


class TestSharedTwinState:
    """Both agents read from the same Twin state."""

    async def test_both_agents_read_same_twin(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        mech_artifact,
    ):
        mech_agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        ee_agent = ElectronicsAgent(twin=twin, mcp=mcp_with_tools)

        # Both should be able to read the same mechanical artifact
        from domain_agents.electronics.agent import TaskRequest as EETR
        from domain_agents.mechanical.agent import TaskRequest as MechTR

        mech_result = await mech_agent.run_task(
            MechTR(
                task_type="validate_stress",
                artifact_id=mech_artifact.id,
                parameters={"mesh_file_path": "cad/bracket.inp"},
            )
        )

        # EE agent reading a mechanical artifact — it exists, so no "not found"
        ee_result = await ee_agent.run_task(
            EETR(
                task_type="run_erc",
                artifact_id=mech_artifact.id,
                parameters={"schematic_file": "eda/kicad/main.kicad_sch"},
            )
        )

        assert mech_result.success is True
        # EE agent can read it (artifact exists), but the ERC itself may use it differently
        assert mech_result.artifact_id == ee_result.artifact_id


class TestMixedSuccessFailure:
    """Mixed success/failure across agent types."""

    async def test_mech_succeeds_ee_fails(
        self,
        workflow_engine: InMemoryWorkflowEngine,
        event_bus: EventBus,
        spy: SpySubscriber,
    ):
        class SuccessAgent:
            async def run_task(self, request: Any) -> dict:
                return {"status": "ok", "success": True}

        class FailAgent:
            async def run_task(self, request: Any) -> dict:
                raise RuntimeError("EE validation failed")

        defn = WorkflowDefinition(
            name="mixed",
            steps=[
                WorkflowStep(step_id="mech", agent_code="MECH", task_type="validate_stress"),
                WorkflowStep(step_id="ee", agent_code="EE", task_type="run_erc"),
            ],
        )
        defn = await workflow_engine.register_workflow(defn)
        dep_graph = DependencyGraph(defn)

        scheduler = InMemoryScheduler(
            workflow_engine=workflow_engine,
            event_bus=event_bus,
            dependency_graph=dep_graph,
        )
        scheduler.register_agent("MECH", SuccessAgent())
        scheduler.register_agent("EE", FailAgent())

        run = await workflow_engine.start_run(defn.id)
        await scheduler.start()
        await scheduler.execute_run(run)
        await _wait_done(workflow_engine, run.id, {"mech", "ee"})
        await scheduler.stop()

        run = await workflow_engine.get_run(run.id)
        assert run.step_results["mech"].status == StepStatus.COMPLETED
        assert run.step_results["ee"].status == StepStatus.FAILED

        # Event bus should have both completed and failed events
        completed = spy.events_of_type(EventType.AGENT_TASK_COMPLETED)
        failed = spy.events_of_type(EventType.AGENT_TASK_FAILED)
        assert len(completed) >= 1
        assert len(failed) >= 1
