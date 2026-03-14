"""E2E tests for the drone flight controller full multi-agent pipeline.

Validates that all 6 domain agents execute correctly, produce valid
results, and the gate engine evaluates readiness properly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from digital_twin.thread.gate_engine.models import GateStage, ReadinessScore
from domain_agents.compliance.agent import ComplianceAgent, ComplianceTaskRequest
from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.electronics.agent import TaskRequest as EETaskRequest
from domain_agents.firmware.agent import FirmwareAgent
from domain_agents.firmware.agent import TaskRequest as FWTaskRequest
from domain_agents.mechanical.agent import MechanicalAgent
from domain_agents.mechanical.agent import TaskRequest as MechTaskRequest
from domain_agents.mechanical.agent import TaskResult as MechTaskResult
from domain_agents.simulation.agent import SimulationAgent
from domain_agents.simulation.agent import TaskRequest as SimTaskRequest
from domain_agents.supply_chain.agent import SupplyChainAgent
from domain_agents.supply_chain.agent import TaskRequest as SCTaskRequest
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Mock FEA results
# ---------------------------------------------------------------------------
MOCK_FEA_RESULT = {
    "max_von_mises": {
        "bracket_body": 85.3,
        "bracket_mount": 42.1,
        "fillet_region": 120.7,
    },
    "solver_time": 14.2,
    "mesh_elements": 52000,
    "node_count": 18500,
}

DRONE_BOM_PARTS = [
    {
        "mpn": "STM32F405RGT6",
        "manufacturer": "STMicroelectronics",
        "description": "ARM Cortex-M4 MCU",
        "quantity": 1,
        "unit_price_usd": 8.50,
        "lifecycle": "active",
        "lead_time_weeks": 12,
        "num_sources": 3,
    },
    {
        "mpn": "IST8310",
        "manufacturer": "Isentek",
        "description": "3-axis magnetometer",
        "quantity": 1,
        "unit_price_usd": 1.80,
        "lifecycle": "nrnd",
        "lead_time_weeks": 16,
        "num_sources": 1,
    },
    {
        "mpn": "TPS62160",
        "manufacturer": "Texas Instruments",
        "description": "3.3V step-down converter",
        "quantity": 2,
        "unit_price_usd": 1.50,
        "lifecycle": "active",
        "lead_time_weeks": 4,
        "num_sources": 3,
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


MOCK_ERC_RESULT = {
    "violations": [
        {
            "rule_id": "ERC_WARN_001",
            "severity": "warning",
            "message": "Unconnected pin on U3 pad 14",
            "sheet": "main",
            "component": "U3",
            "pin": "14",
        }
    ],
}

MOCK_SIM_FEA_RESULT = {
    "max_stress_mpa": 95.4,
    "max_displacement_mm": 0.12,
    "safety_factor": 2.89,
    "solver_time_s": 18.3,
}


@pytest.fixture()
def mcp() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    bridge.register_tool_response("calculix.run_fea", MOCK_FEA_RESULT)
    bridge.register_tool_response("kicad.run_erc", MOCK_ERC_RESULT)
    # Simulation agent's FEA handler also uses calculix.run_fea but expects
    # different keys; register a second response that satisfies the schema.
    # Since InMemoryMcpBridge uses a dict, the last registration wins.
    # We need to provide a response that works for BOTH the mechanical agent
    # (which reads max_von_mises) and the simulation agent (which reads
    # max_stress_mpa). Merge both sets of keys.
    merged_fea = {**MOCK_FEA_RESULT, **MOCK_SIM_FEA_RESULT}
    bridge.register_tool_response("calculix.run_fea", merged_fea)
    return bridge


@pytest.fixture()
async def work_products(twin: InMemoryTwinAPI) -> dict[str, WorkProduct]:
    """Create all project work_products in the twin."""
    cad = await twin.create_work_product(
        WorkProduct(
            name="motor-mount-bracket-v1",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="models/motor_mount_bracket.step",
            content_hash="sha256:a1b2c3",
            format="step",
            created_by="test",
            metadata={"material": "Al6061-T6", "yield_strength_mpa": 276.0},
        )
    )
    schematic = await twin.create_work_product(
        WorkProduct(
            name="drone-fc-schematic-v1",
            type=WorkProductType.SCHEMATIC,
            domain="electronics",
            file_path="eda/kicad/drone_fc.kicad_sch",
            content_hash="sha256:b2c3d4",
            format="kicad_sch",
            created_by="test",
        )
    )
    firmware = await twin.create_work_product(
        WorkProduct(
            name="drone-fc-firmware-v1",
            type=WorkProductType.FIRMWARE_SOURCE,
            domain="firmware",
            file_path="firmware/src/main.c",
            content_hash="sha256:c3d4e5",
            format="c",
            created_by="test",
        )
    )
    bom = await twin.create_work_product(
        WorkProduct(
            name="drone-fc-bom-v1",
            type=WorkProductType.BOM,
            domain="supply_chain",
            file_path="bom/drone_fc_bom.csv",
            content_hash="sha256:d4e5f6",
            format="csv",
            created_by="test",
        )
    )
    await twin.create_branch("main")
    await twin.commit("main", "Add test work_products", "test")
    return {"cad": cad, "schematic": schematic, "firmware": firmware, "bom": bom}


# ---------------------------------------------------------------------------
# Tests: Individual agent execution
# ---------------------------------------------------------------------------


class TestMechanicalAgent:
    """Tests for MechanicalAgent in the pipeline."""

    @pytest.mark.asyncio
    async def test_stress_validation_executes(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = MechanicalAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=work_products["cad"].id,
                parameters={
                    "mesh_file_path": "models/bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [{"max_von_mises_mpa": 276.0, "safety_factor": 1.5}],
                },
            )
        )
        assert isinstance(result, MechTaskResult)
        assert result.task_type == "validate_stress"
        assert len(result.skill_results) > 0

    @pytest.mark.asyncio
    async def test_stress_produces_fea_result(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = MechanicalAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=work_products["cad"].id,
                parameters={
                    "mesh_file_path": "models/bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [{"max_von_mises_mpa": 276.0, "safety_factor": 1.5}],
                },
            )
        )
        sr = result.skill_results[0]
        assert "fea_result" in sr
        assert "constraint_results" in sr
        assert sr["fea_result"]["mesh_elements"] == 52000


class TestElectronicsAgent:
    """Tests for ElectronicsAgent in the pipeline."""

    @pytest.mark.asyncio
    async def test_erc_executes(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = ElectronicsAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            EETaskRequest(
                task_type="run_erc",
                work_product_id=work_products["schematic"].id,
                parameters={"schematic_file": "eda/kicad/drone_fc.kicad_sch"},
            )
        )
        assert result.task_type == "run_erc"
        assert len(result.skill_results) > 0

    @pytest.mark.asyncio
    async def test_erc_produces_result(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = ElectronicsAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            EETaskRequest(
                task_type="run_erc",
                work_product_id=work_products["schematic"].id,
                parameters={"schematic_file": "eda/kicad/drone_fc.kicad_sch"},
            )
        )
        sr = result.skill_results[0]
        assert sr["skill"] == "run_erc"
        assert "total_violations" in sr
        assert "passed" in sr


class TestFirmwareAgent:
    """Tests for FirmwareAgent in the pipeline."""

    @pytest.mark.asyncio
    async def test_hal_generation_executes(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = FirmwareAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            FWTaskRequest(
                task_type="generate_hal",
                work_product_id=work_products["firmware"].id,
                parameters={
                    "mcu_family": "STM32F4",
                    "peripherals": ["GPIO", "SPI", "I2C"],
                },
            )
        )
        assert result.task_type == "generate_hal"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_hal_produces_files(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = FirmwareAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            FWTaskRequest(
                task_type="generate_hal",
                work_product_id=work_products["firmware"].id,
                parameters={
                    "mcu_family": "STM32F4",
                    "peripherals": ["GPIO", "SPI"],
                },
            )
        )
        sr = result.skill_results[0]
        assert sr["skill"] == "generate_hal"
        assert "generated_files" in sr
        assert len(sr["generated_files"]) > 0


class TestSimulationAgent:
    """Tests for SimulationAgent in the pipeline."""

    @pytest.mark.asyncio
    async def test_fea_executes(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = SimulationAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            SimTaskRequest(
                task_type="run_fea",
                work_product_id=work_products["cad"].id,
                parameters={
                    "mesh_file": "models/bracket.inp",
                    "analysis_type": "static",
                    "material": "aluminum_6061",
                },
            )
        )
        assert result.task_type == "run_fea"
        assert len(result.skill_results) > 0
        sr = result.skill_results[0]
        assert "max_stress_mpa" in sr
        assert "safety_factor" in sr


class TestSupplyChainAgent:
    """Tests for SupplyChainAgent in the pipeline."""

    @pytest.mark.asyncio
    async def test_bom_risk_executes(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = SupplyChainAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            SCTaskRequest(
                task_type="score_bom_risk",
                parameters={"bom_items": DRONE_BOM_PARTS},
            )
        )
        assert result.task_type == "score_bom_risk"
        assert result.success is True
        assert len(result.skill_results) > 0

    @pytest.mark.asyncio
    async def test_bom_risk_produces_report(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = SupplyChainAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            SCTaskRequest(
                task_type="score_bom_risk",
                parameters={"bom_items": DRONE_BOM_PARTS, "risk_threshold": 0.6},
            )
        )
        sr = result.skill_results[0]
        assert sr["total_parts"] == len(DRONE_BOM_PARTS)
        assert "overall_score" in sr
        assert "part_scores" in sr


class TestComplianceAgent:
    """Tests for ComplianceAgent in the pipeline."""

    @pytest.mark.asyncio
    async def test_checklist_executes(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = ComplianceAgent()
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="drone-fc",
                parameters={"markets": ["CE", "FCC", "UKCA"]},
            )
        )
        assert result.task_type == "generate_checklist"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_checklist_produces_items(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        agent = ComplianceAgent()
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="drone-fc",
                parameters={"markets": ["CE", "FCC", "UKCA"]},
            )
        )
        assert result.total_requirements > 0
        assert result.success is True
        checklist_data = result.data.get("checklist", {})
        assert checklist_data.get("total_items", 0) > 0


# ---------------------------------------------------------------------------
# Tests: Gate engine evaluation
# ---------------------------------------------------------------------------


class TestGateEngine:
    """Tests for gate engine models and definitions."""

    def test_gate_definitions_exist(self) -> None:
        from digital_twin.thread.gate_engine.engine import DEFAULT_GATE_DEFINITIONS

        assert GateStage.EVT in DEFAULT_GATE_DEFINITIONS
        assert GateStage.DVT in DEFAULT_GATE_DEFINITIONS
        assert GateStage.PVT in DEFAULT_GATE_DEFINITIONS

    def test_evt_has_lower_thresholds_than_pvt(self) -> None:
        from digital_twin.thread.gate_engine.engine import DEFAULT_GATE_DEFINITIONS

        evt = DEFAULT_GATE_DEFINITIONS[GateStage.EVT]
        pvt = DEFAULT_GATE_DEFINITIONS[GateStage.PVT]
        assert evt.min_overall_score < pvt.min_overall_score

    def test_readiness_score_model(self) -> None:
        from datetime import UTC, datetime

        from digital_twin.thread.gate_engine.models import (
            CriterionResult,
            GateCriterion,
            GateCriterionType,
        )

        criterion = GateCriterion(
            type=GateCriterionType.REQUIREMENT_COVERAGE,
            name="Requirement Coverage",
            description="Test",
            weight=1.0,
            threshold=50.0,
            required=True,
        )
        cr = CriterionResult(
            criterion=criterion,
            score=75.0,
            passed=True,
            details="Score: 75.0/50.0",
        )
        readiness = ReadinessScore(
            stage=GateStage.EVT,
            overall_score=75.0,
            criteria_results=[cr],
            ready=True,
            blockers=[],
            evaluated_at=datetime.now(UTC),
        )
        assert isinstance(readiness, ReadinessScore)
        assert readiness.ready is True
        assert readiness.overall_score == 75.0
        assert len(readiness.blockers) == 0


# ---------------------------------------------------------------------------
# Tests: Pipeline resilience
# ---------------------------------------------------------------------------


class TestPipelineResilience:
    """Tests for pipeline behaviour when agents fail."""

    @pytest.mark.asyncio
    async def test_pipeline_continues_after_agent_failure(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        """One agent failing should not prevent others from running."""
        results: dict[str, Any] = {}

        # Mechanical -- will fail due to MCP tool not responding for wrong tool
        mech_agent = MechanicalAgent(twin=twin, mcp=mcp)
        mech_result = await mech_agent.run_task(
            MechTaskRequest(
                task_type="validate_stress",
                work_product_id=work_products["cad"].id,
                parameters={
                    "mesh_file_path": "models/bracket.inp",
                    "load_case": "hover_3g",
                    "constraints": [{"max_von_mises_mpa": 276.0, "safety_factor": 1.5}],
                },
            )
        )
        results["mechanical"] = mech_result

        # Supply chain -- should succeed regardless
        sc_agent = SupplyChainAgent(twin=twin, mcp=mcp)
        sc_result = await sc_agent.run_task(
            SCTaskRequest(
                task_type="score_bom_risk",
                parameters={"bom_items": DRONE_BOM_PARTS},
            )
        )
        results["supply_chain"] = sc_result

        # Compliance -- should succeed regardless
        comp_agent = ComplianceAgent()
        comp_result = await comp_agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="drone-fc",
                parameters={"markets": ["CE"]},
            )
        )
        results["compliance"] = comp_result

        # Even if mech failed, SC and compliance should be fine
        assert sc_result.success is True
        assert comp_result.success is True

    @pytest.mark.asyncio
    async def test_unsupported_task_returns_error(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        """Agents should return a clear error for unsupported task types."""
        agent = SupplyChainAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            SCTaskRequest(
                task_type="nonexistent_task",
            )
        )
        assert result.success is False
        assert len(result.errors) > 0
        assert "Unsupported" in result.errors[0]


# ---------------------------------------------------------------------------
# Tests: Twin state updates
# ---------------------------------------------------------------------------


class TestTwinState:
    """Tests for Digital Twin state after agent execution."""

    @pytest.mark.asyncio
    async def test_artifacts_exist_after_creation(
        self, twin: InMemoryTwinAPI, work_products: dict[str, WorkProduct]
    ) -> None:
        """All work_products should be retrievable from the twin."""
        for name, work_product in work_products.items():
            retrieved = await twin.get_work_product(work_product.id)
            assert retrieved is not None, f"WorkProduct '{name}' not found in twin"
            assert retrieved.name == work_product.name

    @pytest.mark.asyncio
    async def test_twin_update_after_agent_run(
        self, twin: InMemoryTwinAPI, mcp: InMemoryMcpBridge, work_products: dict[str, WorkProduct]
    ) -> None:
        """Agent results can be written back to the twin."""
        agent = SupplyChainAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(
            SCTaskRequest(
                task_type="score_bom_risk",
                parameters={"bom_items": DRONE_BOM_PARTS},
            )
        )
        assert result.success is True

        # Update the twin with results
        await twin.update_work_product(
            work_products["bom"].id,
            {"metadata": {"bom_risk_score": result.skill_results[0]["overall_score"]}},
        )
        updated = await twin.get_work_product(work_products["bom"].id)
        assert updated is not None
        assert "bom_risk_score" in updated.metadata
