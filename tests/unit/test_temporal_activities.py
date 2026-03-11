"""Unit tests for Temporal activity wrappers and workflows (MET-186).

Tests run without a Temporal server — all SDK interactions are mocked.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from orchestrator.activities.approval_activity import wait_for_approval
from orchestrator.activities.base_activity import (
    AgentActivityInput,
    AgentActivityOutput,
    ApprovalRequest,
    ApprovalResult,
    get_default_retry_policy,
)
from orchestrator.activities.electronics_activity import run_electronics_agent
from orchestrator.activities.firmware_activity import run_firmware_agent
from orchestrator.activities.mechanical_activity import run_mechanical_agent
from orchestrator.activities.simulation_activity import run_simulation_agent
from orchestrator.workflows.hardware_design_workflow import (
    HardwareDesignWorkflow,
    HardwareDesignWorkflowInput,
)
from orchestrator.workflows.single_agent_workflow import (
    AGENT_ACTIVITIES,
    SingleAgentWorkflow,
    SingleAgentWorkflowInput,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ARTIFACT_ID = str(uuid4())
SESSION_ID = str(uuid4())
RUN_ID = str(uuid4())


def _make_input(agent_code: str, task_type: str, **extra: Any) -> AgentActivityInput:
    """Build a standard AgentActivityInput for testing."""
    task_request = {
        "task_type": task_type,
        "artifact_id": ARTIFACT_ID,
        "parameters": extra.get("parameters", {}),
        "branch": "main",
    }
    return AgentActivityInput(
        agent_code=agent_code,
        task_request=task_request,
        session_id=SESSION_ID,
        run_id=RUN_ID,
        step_id=f"{agent_code}_step",
    )


def _mock_agent(success: bool = True) -> AsyncMock:
    """Return a mock agent whose run_task returns a stub TaskResult."""
    agent = AsyncMock()
    result = MagicMock()
    result.success = success
    result.model_dump.return_value = {
        "task_type": "test",
        "artifact_id": ARTIFACT_ID,
        "success": success,
        "skill_results": [],
        "errors": [] if success else ["Test error"],
        "warnings": [],
    }
    agent.run_task.return_value = result
    return agent


def _patch_agent_activity(agent_module: str, agent_cls_name: str, agent: Any) -> Any:
    """Patch the source modules that are lazily imported in activity functions.

    Since activities use late imports inside the function body, we need to
    patch the source modules rather than the activity module's namespace.
    """
    return (
        patch(f"domain_agents.{agent_module}.agent.{agent_cls_name}", return_value=agent),
        patch("twin_core.api.InMemoryTwinAPI"),
        patch("skill_registry.mcp_bridge.InMemoryMcpBridge"),
    )


# ---------------------------------------------------------------------------
# Base models
# ---------------------------------------------------------------------------


class TestBaseModels:
    """Test AgentActivityInput / AgentActivityOutput serialisation."""

    def test_activity_input_roundtrip(self) -> None:
        inp = _make_input("mechanical", "validate_stress")
        data = inp.model_dump()
        restored = AgentActivityInput.model_validate(data)
        assert restored.agent_code == "mechanical"
        assert restored.session_id == SESSION_ID
        assert restored.task_request["task_type"] == "validate_stress"

    def test_activity_output_roundtrip(self) -> None:
        out = AgentActivityOutput(
            task_result={"success": True},
            agent_code="mechanical",
            duration_ms=42.0,
            tool_calls=[],
        )
        data = out.model_dump()
        restored = AgentActivityOutput.model_validate(data)
        assert restored.duration_ms == 42.0

    def test_approval_request_defaults(self) -> None:
        req = ApprovalRequest(
            approval_id="a1",
            description="test",
            run_id=RUN_ID,
            step_id="s1",
        )
        assert req.required_role == "reviewer"
        assert req.requested_at  # non-empty

    def test_approval_result_defaults(self) -> None:
        res = ApprovalResult(approved=True, approver_id="user-1")
        assert res.approved is True
        assert res.timestamp  # non-empty

    def test_default_retry_policy(self) -> None:
        policy = get_default_retry_policy()
        # Should return something (RetryPolicy or dict)
        assert policy is not None


# ---------------------------------------------------------------------------
# Individual activities — agent instantiation & I/O conversion
# ---------------------------------------------------------------------------


class TestMechanicalActivity:
    """Test run_mechanical_agent activity."""

    async def test_successful_execution(self) -> None:
        agent = _mock_agent(success=True)
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("mechanical", "validate_stress")
            result = await run_mechanical_agent(inp)

            assert isinstance(result, AgentActivityOutput)
            assert result.agent_code == "mechanical"
            assert result.duration_ms > 0
            assert result.task_result["success"] is True
            agent.run_task.assert_awaited_once()

    async def test_agent_failure_propagates(self) -> None:
        agent = _mock_agent(success=False)
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("mechanical", "validate_stress")
            result = await run_mechanical_agent(inp)
            assert result.task_result["success"] is False

    async def test_exception_raises(self) -> None:
        agent = AsyncMock()
        agent.run_task.side_effect = RuntimeError("solver crash")
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("mechanical", "validate_stress")
            with pytest.raises(RuntimeError, match="solver crash"):
                await run_mechanical_agent(inp)


class TestElectronicsActivity:
    """Test run_electronics_agent activity."""

    async def test_successful_execution(self) -> None:
        agent = _mock_agent(success=True)
        p1, p2, p3 = _patch_agent_activity("electronics", "ElectronicsAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("electronics", "run_erc")
            result = await run_electronics_agent(inp)

            assert isinstance(result, AgentActivityOutput)
            assert result.agent_code == "electronics"
            assert result.task_result["success"] is True


class TestFirmwareActivity:
    """Test run_firmware_agent activity."""

    async def test_successful_execution(self) -> None:
        agent = _mock_agent(success=True)
        p1, p2, p3 = _patch_agent_activity("firmware", "FirmwareAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("firmware", "generate_hal")
            result = await run_firmware_agent(inp)

            assert isinstance(result, AgentActivityOutput)
            assert result.agent_code == "firmware"
            assert result.task_result["success"] is True


class TestSimulationActivity:
    """Test run_simulation_agent activity."""

    async def test_successful_execution(self) -> None:
        agent = _mock_agent(success=True)
        p1, p2, p3 = _patch_agent_activity("simulation", "SimulationAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("simulation", "run_spice")
            result = await run_simulation_agent(inp)

            assert isinstance(result, AgentActivityOutput)
            assert result.agent_code == "simulation"
            assert result.task_result["success"] is True


# ---------------------------------------------------------------------------
# Approval activity
# ---------------------------------------------------------------------------


class TestApprovalActivity:
    """Test wait_for_approval activity."""

    async def test_auto_approves_without_temporal(self) -> None:
        """Without Temporal runtime, the activity auto-approves."""
        from unittest.mock import patch

        req = ApprovalRequest(
            approval_id="test-1",
            description="Test approval",
            run_id=RUN_ID,
            step_id="approval_step",
        )
        # Force the non-Temporal code path regardless of SDK availability
        with patch("orchestrator.activities.approval_activity.HAS_TEMPORAL", False):
            result = await wait_for_approval(req)
        assert isinstance(result, ApprovalResult)
        assert result.approved is True
        assert result.approver_id == "auto"


# ---------------------------------------------------------------------------
# SingleAgentWorkflow
# ---------------------------------------------------------------------------


class TestSingleAgentWorkflow:
    """Test SingleAgentWorkflow dispatches correct activity."""

    def test_agent_activity_mapping(self) -> None:
        """All four agents are registered."""
        assert "mechanical" in AGENT_ACTIVITIES
        assert "electronics" in AGENT_ACTIVITIES
        assert "firmware" in AGENT_ACTIVITIES
        assert "simulation" in AGENT_ACTIVITIES

    async def test_dispatches_mechanical(self) -> None:
        agent = _mock_agent(success=True)
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()

            wf = SingleAgentWorkflow()
            inp = SingleAgentWorkflowInput(
                agent_code="mechanical",
                task_request={
                    "task_type": "validate_stress",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
            )
            result = await wf.run(inp)

            assert result.status == "completed"
            assert result.agent_code == "mechanical"
            assert result.activity_output["task_result"]["success"] is True

    async def test_unknown_agent_returns_failure(self) -> None:
        wf = SingleAgentWorkflow()
        inp = SingleAgentWorkflowInput(
            agent_code="nonexistent",
            task_request={"task_type": "noop"},
        )
        result = await wf.run(inp)
        assert result.status == "failed"
        assert "Unknown agent_code" in str(result.activity_output)

    async def test_dispatches_electronics(self) -> None:
        agent = _mock_agent(success=True)
        p1, p2, p3 = _patch_agent_activity("electronics", "ElectronicsAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()

            wf = SingleAgentWorkflow()
            inp = SingleAgentWorkflowInput(
                agent_code="electronics",
                task_request={
                    "task_type": "run_erc",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
            )
            result = await wf.run(inp)
            assert result.status == "completed"
            assert result.agent_code == "electronics"


# ---------------------------------------------------------------------------
# HardwareDesignWorkflow
# ---------------------------------------------------------------------------


class TestHardwareDesignWorkflow:
    """Test HardwareDesignWorkflow DAG execution order."""

    def _start_all_patches(self) -> tuple[list[Any], list[Any]]:
        """Start patches for all agent activities.

        Returns (patch_objects, mock_twins) so the caller can
        set up InMemoryTwinAPI.create() and stop them in finally.
        """
        agents = [
            ("domain_agents.mechanical.agent.MechanicalAgent", _mock_agent(True)),
            ("domain_agents.electronics.agent.ElectronicsAgent", _mock_agent(True)),
            ("domain_agents.firmware.agent.FirmwareAgent", _mock_agent(True)),
            ("domain_agents.simulation.agent.SimulationAgent", _mock_agent(True)),
        ]
        patch_objs = []
        mock_twins = []
        for cls_path, agent_mock in agents:
            p = patch(cls_path, return_value=agent_mock)
            p.start()
            patch_objs.append(p)

        p_twin = patch("twin_core.api.InMemoryTwinAPI")
        mt = p_twin.start()
        mt.create.return_value = MagicMock()
        patch_objs.append(p_twin)
        mock_twins.append(mt)

        p_mcp = patch("skill_registry.mcp_bridge.InMemoryMcpBridge")
        p_mcp.start()
        patch_objs.append(p_mcp)

        return patch_objs, mock_twins

    async def test_full_dag_execution(self) -> None:
        """All steps execute in order: MECH -> [EE || FW] -> SIM -> APPROVAL."""
        patches, _ = self._start_all_patches()
        try:
            wf = HardwareDesignWorkflow()
            inp = HardwareDesignWorkflowInput(
                mechanical_task={
                    "task_type": "validate_stress",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                electronics_task={
                    "task_type": "run_erc",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                firmware_task={
                    "task_type": "generate_hal",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                simulation_task={
                    "task_type": "run_spice",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                require_approval=True,  # auto-approves without Temporal
            )

            result = await wf.run(inp)

            assert result.status == "completed"
            # 4 steps: mechanical, electronics, firmware, simulation
            assert len(result.steps) == 4
            assert all(s.status == "completed" for s in result.steps)
            # Approval auto-approved
            assert result.approval.get("approved") is True
        finally:
            for p in patches:
                p.stop()

    async def test_parallel_ee_fw_execution(self) -> None:
        """EE and FW should both complete even when run in parallel."""
        patches, _ = self._start_all_patches()
        try:
            wf = HardwareDesignWorkflow()
            inp = HardwareDesignWorkflowInput(
                mechanical_task={},  # skip mechanical
                electronics_task={
                    "task_type": "run_erc",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                firmware_task={
                    "task_type": "generate_hal",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                simulation_task={},  # skip simulation
                require_approval=False,
            )

            result = await wf.run(inp)

            assert result.status == "completed"
            assert len(result.steps) == 2
            agent_codes = {s.agent_code for s in result.steps}
            assert "electronics" in agent_codes
            assert "firmware" in agent_codes
        finally:
            for p in patches:
                p.stop()

    async def test_mechanical_failure_stops_workflow(self) -> None:
        """If mechanical fails, no further steps should run."""
        mech_agent = AsyncMock()
        mech_agent.run_task.side_effect = RuntimeError("FEA crash")

        with (
            patch(
                "domain_agents.mechanical.agent.MechanicalAgent",
                return_value=mech_agent,
            ),
            patch("twin_core.api.InMemoryTwinAPI") as mock_twin,
            patch("skill_registry.mcp_bridge.InMemoryMcpBridge"),
        ):
            mock_twin.create.return_value = MagicMock()

            wf = HardwareDesignWorkflow()
            inp = HardwareDesignWorkflowInput(
                mechanical_task={
                    "task_type": "validate_stress",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                electronics_task={
                    "task_type": "run_erc",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                require_approval=False,
            )

            result = await wf.run(inp)

            assert result.status == "failed"
            # Only mechanical step should have run
            assert len(result.steps) == 1
            assert result.steps[0].agent_code == "mechanical"
            assert result.steps[0].status == "failed"

    async def test_no_approval_skips_gate(self) -> None:
        """When require_approval=False, the approval gate is skipped."""
        patches, _ = self._start_all_patches()
        try:
            wf = HardwareDesignWorkflow()
            inp = HardwareDesignWorkflowInput(
                mechanical_task={
                    "task_type": "validate_stress",
                    "artifact_id": ARTIFACT_ID,
                    "parameters": {},
                    "branch": "main",
                },
                require_approval=False,
            )

            result = await wf.run(inp)

            assert result.status == "completed"
            assert result.approval == {}
        finally:
            for p in patches:
                p.stop()


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    """Test that activity failures lead to proper retry semantics."""

    async def test_activity_raises_on_error(self) -> None:
        """Activities re-raise exceptions so Temporal can retry them."""
        agent = AsyncMock()
        agent.run_task.side_effect = RuntimeError("transient error")
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("mechanical", "validate_stress")
            with pytest.raises(RuntimeError, match="transient error"):
                await run_mechanical_agent(inp)

    async def test_retry_then_success(self) -> None:
        """Simulate retry: first call fails, second succeeds."""
        call_count = 0

        async def flaky_run_task(request: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient")
            result = MagicMock()
            result.success = True
            result.model_dump.return_value = {
                "task_type": "test",
                "artifact_id": ARTIFACT_ID,
                "success": True,
                "skill_results": [],
                "errors": [],
                "warnings": [],
            }
            return result

        agent = AsyncMock()
        agent.run_task = flaky_run_task
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("mechanical", "validate_stress")

            # First call raises
            with pytest.raises(RuntimeError, match="transient"):
                await run_mechanical_agent(inp)

            # Second call succeeds (simulating Temporal retry)
            result = await run_mechanical_agent(inp)
            assert result.task_result["success"] is True
            assert call_count == 2


# ---------------------------------------------------------------------------
# Timeout behavior
# ---------------------------------------------------------------------------


class TestTimeoutBehavior:
    """Test that long-running activities respect timeouts."""

    async def test_timeout_error_propagates(self) -> None:
        """When an agent exceeds its timeout, the error propagates for Temporal retry."""
        agent = AsyncMock()
        agent.run_task.side_effect = TimeoutError("activity timed out")
        p1, p2, p3 = _patch_agent_activity("mechanical", "MechanicalAgent", agent)
        with p1, p2 as mock_twin, p3:
            mock_twin.create.return_value = MagicMock()
            inp = _make_input("mechanical", "validate_stress")
            with pytest.raises(asyncio.TimeoutError):
                await run_mechanical_agent(inp)


# ---------------------------------------------------------------------------
# Temporal worker
# ---------------------------------------------------------------------------


class TestTemporalWorker:
    """Test worker factory."""

    def test_all_activities_registered(self) -> None:
        from orchestrator.temporal_worker import ALL_ACTIVITIES, ALL_WORKFLOWS

        assert len(ALL_ACTIVITIES) == 5  # 4 agents + approval
        assert len(ALL_WORKFLOWS) == 2  # single + hardware design

    def test_create_worker_without_temporal_raises(self) -> None:
        """create_worker raises ImportError when temporalio is not installed."""
        from orchestrator.temporal_worker import HAS_TEMPORAL

        if not HAS_TEMPORAL:
            from orchestrator.temporal_worker import create_worker

            with pytest.raises(ImportError, match="temporalio"):
                create_worker(MagicMock())
