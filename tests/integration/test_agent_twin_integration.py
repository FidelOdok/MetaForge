"""Integration tests: Agent <-> Digital Twin interaction.

Tests that agents correctly read work_products from the Twin, handle missing
work_products, work with branches, and that constraint evaluation integrates
with the Twin's graph state.
"""

from __future__ import annotations

from uuid import uuid4

from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.electronics.agent import TaskRequest as EETaskRequest
from domain_agents.mechanical.agent import MechanicalAgent
from domain_agents.mechanical.agent import TaskRequest as MechTaskRequest
from skill_registry.mcp_bridge import InMemoryMcpBridge
from tests.conftest import make_work_product
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Agent reads Twin
# ---------------------------------------------------------------------------


class TestAgentReadsArtifact:
    """Agent correctly reads work_products from InMemoryTwinAPI."""

    async def test_mech_agent_reads_artifact_by_id(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        mech_work_product: WorkProduct,
    ):
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        result = await agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=mech_work_product.id,
                parameters={"mesh_file_path": "cad/bracket.inp"},
            )
        )
        # Agent successfully read the work_product (didn't get "not found" error)
        assert result.success is True
        assert result.work_product_id == mech_work_product.id

    async def test_agent_returns_error_for_missing_artifact(
        self, twin: InMemoryTwinAPI, mcp_with_tools: InMemoryMcpBridge
    ):
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)
        missing_id = uuid4()
        result = await agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=missing_id,
            )
        )
        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_ee_agent_reads_correct_artifact(
        self, twin: InMemoryTwinAPI, mcp_with_tools: InMemoryMcpBridge, ee_work_product: WorkProduct
    ):
        """EE agent reads work_product by UUID and runs the ERC skill successfully.

        The str/UUID mismatch was fixed — all skill schemas now use UUID.
        """
        agent = ElectronicsAgent(twin=twin, mcp=mcp_with_tools)
        result = await agent.run_task(
            EETaskRequest(
                task_type="run_erc",
                work_product_id=ee_work_product.id,
                parameters={"schematic_file": "eda/kicad/main.kicad_sch"},
            )
        )
        assert result.work_product_id == ee_work_product.id
        assert result.success is True
        assert len(result.errors) == 0

    async def test_twin_state_persists_across_agent_reads(
        self,
        twin: InMemoryTwinAPI,
        mcp_with_tools: InMemoryMcpBridge,
        mech_work_product: WorkProduct,
    ):
        agent = MechanicalAgent(twin=twin, mcp=mcp_with_tools)

        # First read
        result1 = await agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=mech_work_product.id,
                parameters={"mesh_file_path": "cad/bracket.inp"},
            )
        )
        # Second read — work_product should still be there
        result2 = await agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=mech_work_product.id,
                parameters={"mesh_file_path": "cad/bracket.inp"},
            )
        )
        assert result1.success is True
        assert result2.success is True


# ---------------------------------------------------------------------------
# Branch operations
# ---------------------------------------------------------------------------


class TestBranchOperations:
    """Twin branch lifecycle: create, read from branch, commit, merge."""

    async def test_create_branch_and_read(
        self, twin: InMemoryTwinAPI, mech_work_product: WorkProduct
    ):
        await twin.create_branch("feature/stress-fix", from_branch="main")
        # WorkProduct from main should be accessible
        work_product = await twin.get_work_product(
            mech_work_product.id, branch="feature/stress-fix"
        )
        assert work_product is not None
        assert work_product.id == mech_work_product.id

    async def test_commit_and_log(self, twin: InMemoryTwinAPI, mech_work_product: WorkProduct):
        await twin.create_branch("feature/test", from_branch="main")
        version = await twin.commit(
            branch="feature/test",
            message="Add stress analysis result",
            author="test-agent",
        )
        assert version is not None

        log = await twin.log(branch="feature/test", limit=5)
        assert len(log) >= 1

    async def test_merge_branch(self, twin: InMemoryTwinAPI, mech_work_product: WorkProduct):
        # Must commit on main first so the branch head exists
        await twin._version.create_branch("main")
        await twin.commit(branch="main", message="Initial", author="test")
        await twin.create_branch("feature/merge-test", from_branch="main")
        await twin.commit(
            branch="feature/merge-test",
            message="Work done",
            author="test-agent",
        )
        merge_result = await twin.merge(
            source="feature/merge-test",
            target="main",
            message="Merge feature",
            author="test-agent",
        )
        assert merge_result is not None

    async def test_diff_between_branches(
        self, twin: InMemoryTwinAPI, mech_work_product: WorkProduct
    ):
        # Initialize main branch with a commit
        await twin._version.create_branch("main")
        await twin.commit(branch="main", message="Initial", author="test")
        await twin.create_branch("feature/diff-test", from_branch="main")
        # Create a new work_product only on the branch
        new_artifact = make_work_product(
            name="new-part",
            work_product_type=WorkProductType.CAD_MODEL,
            domain="mechanical",
        )
        await twin.create_work_product(new_artifact, branch="feature/diff-test")
        diff = await twin.diff("main", "feature/diff-test")
        assert diff is not None


# ---------------------------------------------------------------------------
# Constraint evaluation
# ---------------------------------------------------------------------------


class TestConstraintEvaluation:
    """Constraint evaluation integrates with Twin graph state."""

    async def test_evaluate_constraints_with_no_constraints(self, twin: InMemoryTwinAPI):
        result = await twin.evaluate_constraints()
        assert result.passed is True
        assert result.evaluated_count == 0

    async def test_passing_constraint(self, twin: InMemoryTwinAPI, mech_work_product: WorkProduct):
        constraint = Constraint(
            name="always-true",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            message="Should always pass",
            domain="mechanical",
            source="test",
            cross_domain=False,
        )
        # Use the constraint engine directly via twin's internal graph
        await twin._constraints.add_constraint(constraint, [mech_work_product.id])

        result = await twin.evaluate_constraints()
        assert result.passed is True
        assert result.evaluated_count >= 1

    async def test_failing_constraint(self, twin: InMemoryTwinAPI, mech_work_product: WorkProduct):
        constraint = Constraint(
            name="always-fail",
            expression="False",
            severity=ConstraintSeverity.ERROR,
            message="This constraint always fails",
            domain="mechanical",
            source="test",
            cross_domain=False,
        )
        await twin._constraints.add_constraint(constraint, [mech_work_product.id])

        result = await twin.evaluate_constraints()
        assert result.passed is False
        assert len(result.violations) >= 1
        assert result.violations[0].constraint_name == "always-fail"
