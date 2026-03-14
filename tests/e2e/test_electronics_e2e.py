"""End-to-end tests for the electronics engineering vertical.

Exercises the full stack: ElectronicsAgent → MCP Protocol → KiCad Server → Digital Twin.
Only the kicad-cli binary is stubbed; all internal interfaces are real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from domain_agents.electronics.agent import ElectronicsAgent, TaskRequest
from domain_agents.electronics.skills.run_erc.handler import RunErcHandler
from domain_agents.electronics.skills.run_erc.schema import RunErcInput
from mcp_core.client import McpClient
from mcp_core.schemas import ToolManifest as ClientToolManifest
from mcp_core.transports import LoopbackTransport
from skill_registry.mcp_client_bridge import McpClientBridge
from skill_registry.skill_base import SkillContext
from tool_registry.tools.kicad.adapter import KicadServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

# ---------------------------------------------------------------------------
# Realistic KiCad tool results for a drone flight controller schematic
# ---------------------------------------------------------------------------

CLEAN_ERC_RESULT = {
    "schematic_file": "eda/kicad/drone_fc.kicad_sch",
    "total_violations": 0,
    "errors": 0,
    "warnings": 0,
    "violations": [],
    "passed": True,
}

ERC_WITH_VIOLATIONS = {
    "schematic_file": "eda/kicad/drone_fc.kicad_sch",
    "total_violations": 3,
    "errors": 1,
    "warnings": 2,
    "violations": [
        {
            "rule_id": "ERC001",
            "severity": "error",
            "message": "Unconnected pin U1:VCC",
            "sheet": "power",
            "component": "U1",
            "pin": "VCC",
            "location": "net:VCC_3V3",
        },
        {
            "rule_id": "ERC002",
            "severity": "warning",
            "message": "Power pin not driven: U2:GND",
            "sheet": "mcu",
            "component": "U2",
            "pin": "GND",
            "location": "net:GND",
        },
        {
            "rule_id": "ERC003",
            "severity": "warning",
            "message": "No-connect flag on connected pin R1:1",
            "sheet": "sensors",
            "component": "R1",
            "pin": "1",
            "location": "net:I2C_SDA",
        },
    ],
    "passed": False,
}

CLEAN_DRC_RESULT = {
    "pcb_file": "eda/kicad/drone_fc.kicad_pcb",
    "total_violations": 0,
    "errors": 0,
    "warnings": 0,
    "violations": [],
    "passed": True,
}

DRC_WITH_VIOLATIONS = {
    "pcb_file": "eda/kicad/drone_fc.kicad_pcb",
    "total_violations": 2,
    "errors": 2,
    "warnings": 0,
    "violations": [
        {
            "rule_id": "DRC001",
            "severity": "error",
            "message": "Clearance violation between U1 pad 4 and R3 pad 2 (0.1mm < 0.15mm)",
            "component": "U1",
            "location": "layer:F.Cu",
        },
        {
            "rule_id": "DRC002",
            "severity": "error",
            "message": "Track width too narrow on net VCC_3V3 (0.2mm < 0.25mm)",
            "component": "",
            "location": "layer:F.Cu",
        },
    ],
    "passed": False,
}


def _create_kicad_server(
    erc_result: dict | None = None,
    drc_result: dict | None = None,
) -> KicadServer:
    """Create a KiCad server with stubbed CLI binaries returning realistic data."""
    server = KicadServer()
    server._execute_erc = AsyncMock(return_value=erc_result or CLEAN_ERC_RESULT)
    server._execute_drc = AsyncMock(return_value=drc_result or CLEAN_DRC_RESULT)
    server._execute_bom_export = AsyncMock(
        return_value={
            "output_file": "bom/drone_fc_bom.csv",
            "total_components": 47,
            "unique_parts": 23,
            "format": "csv",
        }
    )
    server._execute_gerber_export = AsyncMock(
        return_value={
            "output_dir": "/tmp/drone_fc_gerber",
            "files_generated": ["F.Cu.gbr", "B.Cu.gbr", "F.Mask.gbr"],
            "total_files": 7,
            "layers_exported": [
                "F.Cu",
                "B.Cu",
                "F.Mask",
                "B.Mask",
                "F.SilkS",
                "B.SilkS",
                "Edge.Cuts",
            ],
        }
    )
    server._execute_netlist_export = AsyncMock(
        return_value={
            "output_file": "eda/kicad/drone_fc.net",
            "total_nets": 62,
            "total_components": 47,
            "format": "kicad",
        }
    )
    server._execute_pin_mapping = AsyncMock(
        return_value={
            "schematic_file": "eda/kicad/drone_fc.kicad_sch",
            "components": [
                {
                    "ref": "U1",
                    "pins": [{"name": "VCC", "net": "VCC_3V3"}, {"name": "GND", "net": "GND"}],
                },
                {
                    "ref": "U2",
                    "pins": [{"name": "PA0", "net": "IMU_INT"}, {"name": "PA1", "net": "SPI_SCK"}],
                },
            ],
            "total_components": 2,
            "total_pins": 4,
        }
    )
    return server


async def _setup_kicad_mcp_stack(
    erc_result: dict | None = None,
    drc_result: dict | None = None,
) -> tuple[McpClient, McpClientBridge, KicadServer]:
    """Wire up KicadServer → LoopbackTransport → McpClient → McpClientBridge."""
    server = _create_kicad_server(erc_result, drc_result)
    transport = LoopbackTransport(server)
    client = McpClient()
    await client.connect("kicad", transport)

    for tool_id, reg in server._tools.items():
        m = reg.manifest
        client.register_manifest(
            ClientToolManifest(
                tool_id=m.tool_id,
                adapter_id=m.adapter_id,
                name=m.name,
                description=m.description,
                capability=m.capability,
                input_schema=m.input_schema,
                output_schema=m.output_schema,
                phase=m.phase,
            )
        )

    bridge = McpClientBridge(client)
    return client, bridge, server


def _make_schematic_artifact() -> WorkProduct:
    """Create a realistic drone flight controller schematic work_product."""
    return WorkProduct(
        name="drone-fc-schematic",
        type=WorkProductType.SCHEMATIC,
        domain="electronics",
        file_path="eda/kicad/drone_fc.kicad_sch",
        content_hash="sha256:ee1122334455",
        format="kicad_sch",
        created_by="human",
        metadata={
            "project": "drone-flight-controller",
            "num_sheets": 3,
            "num_components": 47,
            "mcu": "STM32F405",
        },
    )


# ---------------------------------------------------------------------------
# Test class: Full vertical through ElectronicsAgent
# ---------------------------------------------------------------------------


class TestElectronicsAgentE2E:
    """E2E tests exercising ElectronicsAgent → MCP → KiCad → Twin."""

    @pytest.fixture
    async def stack(self):
        """Set up the complete stack: Twin + MCP + Agent."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_kicad_mcp_stack()

        work_product = _make_schematic_artifact()
        created = await twin.create_work_product(work_product)

        agent = ElectronicsAgent(twin=twin, mcp=bridge)

        return {
            "twin": twin,
            "client": client,
            "bridge": bridge,
            "server": server,
            "agent": agent,
            "work_product": created,
        }

    async def test_erc_passes_clean_schematic(self, stack):
        """ERC passes when no violations are found."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_erc",
                work_product_id=s["work_product"].id,
                parameters={
                    "schematic_file": "eda/kicad/drone_fc.kicad_sch",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "run_erc"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["passed"] is True
        assert result.skill_results[0]["total_violations"] == 0
        assert result.errors == []

        s["server"]._execute_erc.assert_awaited_once()

    async def test_erc_fails_with_violations(self):
        """ERC fails when errors are found in the schematic."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_kicad_mcp_stack(erc_result=ERC_WITH_VIOLATIONS)

        work_product = _make_schematic_artifact()
        created = await twin.create_work_product(work_product)
        agent = ElectronicsAgent(twin=twin, mcp=bridge)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_erc",
                work_product_id=created.id,
                parameters={
                    "schematic_file": "eda/kicad/drone_fc.kicad_sch",
                },
            )
        )

        assert result.success is False
        assert result.skill_results[0]["total_violations"] == 3
        assert result.skill_results[0]["total_errors"] == 1
        assert result.skill_results[0]["total_warnings"] == 2

    async def test_drc_passes_clean_pcb(self, stack):
        """DRC passes when no violations are found in the PCB layout."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_drc",
                work_product_id=s["work_product"].id,
                parameters={
                    "pcb_file": "eda/kicad/drone_fc.kicad_pcb",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "run_drc"
        assert result.skill_results[0]["passed"] is True
        assert result.errors == []

    async def test_drc_fails_with_violations(self):
        """DRC fails when clearance and track width errors exist."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_kicad_mcp_stack(drc_result=DRC_WITH_VIOLATIONS)

        work_product = _make_schematic_artifact()
        created = await twin.create_work_product(work_product)
        agent = ElectronicsAgent(twin=twin, mcp=bridge)

        result = await agent.run_task(
            TaskRequest(
                task_type="run_drc",
                work_product_id=created.id,
                parameters={"pcb_file": "eda/kicad/drone_fc.kicad_pcb"},
            )
        )

        assert result.success is False
        assert result.skill_results[0]["total_errors"] == 2

    async def test_check_power_budget_not_implemented(self, stack):
        """Power budget check returns not-implemented error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="check_power_budget",
                work_product_id=s["work_product"].id,
                parameters={
                    "components": [
                        {"ref": "U1", "power_mw": 120},
                        {"ref": "U2", "power_mw": 350},
                    ],
                },
            )
        )

        assert result.success is False
        assert any("not yet implemented" in e for e in result.errors)

    async def test_full_validation_erc_and_drc(self, stack):
        """Full validation runs ERC + DRC sequentially."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="full_validation",
                work_product_id=s["work_product"].id,
                parameters={
                    "schematic_file": "eda/kicad/drone_fc.kicad_sch",
                    "pcb_file": "eda/kicad/drone_fc.kicad_pcb",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "full_validation"
        assert len(result.skill_results) == 2

        skills_run = {r["skill"] for r in result.skill_results}
        assert skills_run == {"run_erc", "run_drc"}

    async def test_full_validation_no_params_fails(self, stack):
        """Full validation fails when no check parameters are provided."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="full_validation",
                work_product_id=s["work_product"].id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("No validation checks" in e for e in result.errors)

    async def test_artifact_not_found(self, stack):
        """Agent returns error when work_product doesn't exist in Twin."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_erc",
                work_product_id=uuid4(),
                parameters={"schematic_file": "x.kicad_sch"},
            )
        )

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_unsupported_task_type(self, stack):
        """Agent rejects unknown task types."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="analyze_power_integrity",
                work_product_id=s["work_product"].id,
            )
        )

        assert result.success is False
        assert any("Unsupported" in e for e in result.errors)

    async def test_missing_schematic_file_param(self, stack):
        """ERC returns error when schematic_file is not provided."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="run_erc",
                work_product_id=s["work_product"].id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("schematic_file" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: Full vertical through RunErcHandler (skill path)
# ---------------------------------------------------------------------------


class TestRunErcSkillE2E:
    """E2E tests exercising RunErcHandler → MCP → KiCad → Twin."""

    @pytest.fixture
    async def skill_stack(self):
        """Set up the complete stack for direct skill execution."""
        twin = InMemoryTwinAPI.create()
        client, bridge, server = await _setup_kicad_mcp_stack()

        work_product = _make_schematic_artifact()
        created = await twin.create_work_product(work_product)

        import structlog

        context = SkillContext(
            twin=twin,
            mcp=bridge,
            logger=structlog.get_logger().bind(skill="run_erc"),
            session_id=uuid4(),
            branch="main",
        )
        handler = RunErcHandler(context)

        return {
            "twin": twin,
            "server": server,
            "handler": handler,
            "work_product": created,
        }

    async def test_skill_execute_clean(self, skill_stack):
        """Skill returns pass with zero violations."""
        s = skill_stack
        output = await s["handler"].execute(
            RunErcInput(
                work_product_id=s["work_product"].id,
                schematic_file="eda/kicad/drone_fc.kicad_sch",
            )
        )

        assert output.passed is True
        assert output.total_violations == 0
        assert output.total_errors == 0
        assert output.work_product_id == s["work_product"].id

    async def test_skill_run_pipeline(self, skill_stack):
        """Full skill pipeline (validate → preconditions → execute → wrap)."""
        s = skill_stack
        skill_result = await s["handler"].run(
            RunErcInput(
                work_product_id=s["work_product"].id,
                schematic_file="eda/kicad/drone_fc.kicad_sch",
            )
        )

        assert skill_result.success is True
        assert skill_result.data is not None
        assert skill_result.duration_ms > 0
        assert skill_result.errors == []

    async def test_skill_precondition_missing_artifact(self, skill_stack):
        """Skill fails preconditions when work_product not in Twin."""
        s = skill_stack
        skill_result = await s["handler"].run(
            RunErcInput(
                work_product_id=uuid4(),
                schematic_file="missing.kicad_sch",
            )
        )

        assert skill_result.success is False
        assert any("not found" in e for e in skill_result.errors)


# ---------------------------------------------------------------------------
# Test class: KiCad MCP protocol layer verification
# ---------------------------------------------------------------------------


class TestKicadMcpProtocolE2E:
    """Verify the KiCad MCP protocol stack works end-to-end."""

    async def test_tool_discovery(self):
        """McpClient discovers all 6 KiCad tools through LoopbackTransport."""
        _client, bridge, _server = await _setup_kicad_mcp_stack()
        tools = await bridge.list_tools()
        tool_ids = {t["tool_id"] for t in tools}

        assert "kicad.run_erc" in tool_ids
        assert "kicad.run_drc" in tool_ids
        assert "kicad.export_bom" in tool_ids
        assert "kicad.export_gerber" in tool_ids
        assert "kicad.export_netlist" in tool_ids
        assert "kicad.get_pin_mapping" in tool_ids

    async def test_erc_invocation_through_full_stack(self):
        """Direct ERC tool invocation through the full MCP stack."""
        _client, bridge, server = await _setup_kicad_mcp_stack()

        result = await bridge.invoke(
            "kicad.run_erc",
            {"schematic_file": "test.kicad_sch", "severity_filter": "all"},
        )

        assert result["passed"] is True
        assert result["total_violations"] == 0
        server._execute_erc.assert_awaited_once()

    async def test_capability_filter_erc(self):
        """Bridge filters KiCad tools by capability."""
        _client, bridge, _server = await _setup_kicad_mcp_stack()

        erc_tools = await bridge.list_tools(capability="erc_validation")
        assert len(erc_tools) == 1
        assert erc_tools[0]["tool_id"] == "kicad.run_erc"

        drc_tools = await bridge.list_tools(capability="drc_validation")
        assert len(drc_tools) == 1
        assert drc_tools[0]["tool_id"] == "kicad.run_drc"

    async def test_health_check(self):
        """Health check works through LoopbackTransport."""
        client, _bridge, _server = await _setup_kicad_mcp_stack()
        health = await client.health_check("kicad")

        assert health.status == "healthy"
        assert health.adapter_id == "kicad"
        assert health.tools_available == 6


# ---------------------------------------------------------------------------
# Test class: Twin integration with electronics pipeline
# ---------------------------------------------------------------------------


class TestElectronicsTwinIntegrationE2E:
    """Verify Digital Twin operations work within the electronics E2E pipeline."""

    async def test_artifact_lifecycle_erc(self):
        """Create schematic work_product, run ERC, update Twin with results."""
        twin = InMemoryTwinAPI.create()
        _client, bridge, _server = await _setup_kicad_mcp_stack()

        # 1. Create work_product in Twin
        work_product = _make_schematic_artifact()
        created = await twin.create_work_product(work_product)
        assert await twin.get_work_product(created.id) is not None

        # 2. Run ERC through agent
        agent = ElectronicsAgent(twin=twin, mcp=bridge)
        result = await agent.run_task(
            TaskRequest(
                task_type="run_erc",
                work_product_id=created.id,
                parameters={"schematic_file": "eda/kicad/drone_fc.kicad_sch"},
            )
        )
        assert result.success is True

        # 3. Update work_product with ERC results
        updated = await twin.update_work_product(
            created.id,
            {
                "metadata": {
                    **created.metadata,
                    "erc_status": {
                        "passed": True,
                        "total_violations": 0,
                        "checked_at": "2026-03-07T12:00:00Z",
                    },
                },
            },
        )
        assert "erc_status" in updated.metadata
        assert updated.metadata["erc_status"]["passed"] is True

    async def test_branched_erc_analysis(self):
        """Run ERC on a design branch."""
        twin = InMemoryTwinAPI.create()
        _client, bridge, _server = await _setup_kicad_mcp_stack()

        work_product = _make_schematic_artifact()
        created = await twin.create_work_product(work_product)

        await twin.create_branch("main")
        await twin.commit("main", "Add drone FC schematic", "engineer")
        await twin.create_branch("design/v2-power", from_branch="main")

        agent = ElectronicsAgent(twin=twin, mcp=bridge)
        result = await agent.run_task(
            TaskRequest(
                task_type="run_erc",
                work_product_id=created.id,
                parameters={"schematic_file": "eda/kicad/drone_fc.kicad_sch"},
            )
        )
        assert result.success is True
