#!/usr/bin/env python3
"""Drone Flight Controller -- Full Multi-Agent Pipeline Demo.

Runs all 6 domain agents against a drone FC project:
1. Mechanical: stress analysis on motor mount bracket
2. Electronics: ERC/DRC validation on schematic
3. Firmware: HAL generation for STM32F405
4. Simulation: FEA thermal analysis
5. Supply Chain: BOM risk scoring
6. Compliance: UKCA + CE + FCC checklist

Ends with EVT gate readiness evaluation.

Run:
    python -m examples.drone_flight_controller.demo_full_pipeline

Or directly:
    python examples/drone_flight_controller/demo_full_pipeline.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import structlog

# Ensure the project root is on sys.path when running directly
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from digital_twin.thread.gate_engine.engine import GateEngine  # noqa: E402
from digital_twin.thread.gate_engine.models import GateStage  # noqa: E402
from domain_agents.compliance.agent import ComplianceAgent  # noqa: E402
from domain_agents.compliance.agent import TaskRequest as ComplianceTaskRequest  # noqa: E402
from domain_agents.electronics.agent import ElectronicsAgent  # noqa: E402
from domain_agents.electronics.agent import TaskRequest as EETaskRequest  # noqa: E402
from domain_agents.firmware.agent import FirmwareAgent  # noqa: E402
from domain_agents.firmware.agent import TaskRequest as FWTaskRequest  # noqa: E402
from domain_agents.mechanical.agent import MechanicalAgent  # noqa: E402
from domain_agents.mechanical.agent import TaskRequest as MechTaskRequest  # noqa: E402
from domain_agents.simulation.agent import SimulationAgent  # noqa: E402
from domain_agents.simulation.agent import TaskRequest as SimTaskRequest  # noqa: E402
from domain_agents.supply_chain.agent import SupplyChainAgent  # noqa: E402
from domain_agents.supply_chain.agent import TaskRequest as SCTaskRequest  # noqa: E402
from skill_registry.mcp_bridge import InMemoryMcpBridge  # noqa: E402
from twin_core.api import InMemoryTwinAPI  # noqa: E402
from twin_core.models.enums import WorkProductType  # noqa: E402
from twin_core.models.work_product import WorkProduct  # noqa: E402

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO+
)

# ---------------------------------------------------------------------------
# Mock FEA results (realistic for Al6061-T6 motor mount)
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

# ---------------------------------------------------------------------------
# Drone FC BOM parts for supply chain risk scoring
# ---------------------------------------------------------------------------
DRONE_BOM_PARTS = [
    {
        "mpn": "STM32F405RGT6",
        "manufacturer": "STMicroelectronics",
        "description": "ARM Cortex-M4 MCU, 168MHz, 1MB Flash",
        "quantity": 1,
        "unit_price_usd": 8.50,
        "lifecycle": "active",
        "lead_time_weeks": 12,
        "num_sources": 3,
    },
    {
        "mpn": "BMI088",
        "manufacturer": "Bosch Sensortec",
        "description": "6-axis IMU (accel + gyro)",
        "quantity": 1,
        "unit_price_usd": 4.20,
        "lifecycle": "active",
        "lead_time_weeks": 8,
        "num_sources": 2,
    },
    {
        "mpn": "BMP388",
        "manufacturer": "Bosch Sensortec",
        "description": "Barometric pressure sensor",
        "quantity": 1,
        "unit_price_usd": 2.10,
        "lifecycle": "active",
        "lead_time_weeks": 6,
        "num_sources": 2,
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
        "description": "3.3V 1A step-down converter",
        "quantity": 2,
        "unit_price_usd": 1.50,
        "lifecycle": "active",
        "lead_time_weeks": 4,
        "num_sources": 3,
    },
]


async def run_pipeline() -> dict[str, Any]:
    """Execute the full multi-agent pipeline and return all results."""
    results: dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Step 1: Set up Digital Twin + MCP Bridge
    # -----------------------------------------------------------------------
    print("[1/8] Initializing Digital Twin and MCP Bridge...")
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()

    # Register mock tool responses (would be real adapters in production)
    mcp.register_tool_response(
        "calculix.run_fea",
        {
            **MOCK_FEA_RESULT,
            "max_stress_mpa": 95.4,
            "max_displacement_mm": 0.12,
            "safety_factor": 2.89,
            "solver_time_s": 18.3,
        },
    )
    mcp.register_tool_response(
        "kicad.run_erc",
        {
            "violations": [
                {
                    "rule_id": "ERC_WARN_001",
                    "severity": "warning",
                    "message": "Unconnected pin on U3 pad 14 (NC)",
                    "sheet": "main",
                    "component": "U3",
                    "pin": "14",
                }
            ],
        },
    )

    # -----------------------------------------------------------------------
    # Step 2: Create work_products in the twin
    # -----------------------------------------------------------------------
    print("[2/8] Creating project work_products in the Digital Twin...")

    cad_artifact = await twin.create_work_product(
        WorkProduct(
            name="motor-mount-bracket-v1",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="models/motor_mount_bracket.step",
            content_hash="sha256:a1b2c3d4e5f6",
            format="step",
            created_by="human",
            metadata={
                "material": "Al6061-T6",
                "yield_strength_mpa": 276.0,
                "mass_kg": 0.045,
            },
        )
    )
    print(f"       CAD model: {cad_artifact.name} (id={cad_artifact.id})")

    schematic_artifact = await twin.create_work_product(
        WorkProduct(
            name="drone-fc-schematic-v1",
            type=WorkProductType.SCHEMATIC,
            domain="electronics",
            file_path="eda/kicad/drone_fc.kicad_sch",
            content_hash="sha256:b2c3d4e5f6a7",
            format="kicad_sch",
            created_by="human",
            metadata={"mcu": "STM32F405RGT6", "layers": 4},
        )
    )
    print(f"       Schematic: {schematic_artifact.name}")

    fw_artifact = await twin.create_work_product(
        WorkProduct(
            name="drone-fc-firmware-v1",
            type=WorkProductType.FIRMWARE_SOURCE,
            domain="firmware",
            file_path="firmware/src/main.c",
            content_hash="sha256:c3d4e5f6a7b8",
            format="c",
            created_by="human",
            metadata={"mcu_family": "STM32F4", "rtos": "FreeRTOS"},
        )
    )
    print(f"       Firmware:  {fw_artifact.name}")

    bom_artifact = await twin.create_work_product(
        WorkProduct(
            name="drone-fc-bom-v1",
            type=WorkProductType.BOM,
            domain="supply_chain",
            file_path="bom/drone_fc_bom.csv",
            content_hash="sha256:d4e5f6a7b8c9",
            format="csv",
            created_by="human",
            metadata={"total_parts": len(DRONE_BOM_PARTS)},
        )
    )
    print(f"       BOM:       {bom_artifact.name}")

    await twin.create_branch("main")
    await twin.commit("main", "Add initial drone FC design work_products", "engineer")

    # -----------------------------------------------------------------------
    # Step 3: Run Mechanical Agent -- stress analysis
    # -----------------------------------------------------------------------
    print("[3/8] Running Mechanical Agent (stress analysis)...")
    mech_agent = MechanicalAgent(twin=twin, mcp=mcp)
    mech_result = await mech_agent.run_task(
        MechTaskRequest(
            task_type="validate_stress",
            work_product_id=cad_artifact.id,
            parameters={
                "mesh_file_path": "models/motor_mount_bracket.inp",
                "load_case": "hover_3g",
                "constraints": [
                    {
                        "max_von_mises_mpa": 276.0,
                        "safety_factor": 1.5,
                        "material": "Al6061-T6",
                    }
                ],
            },
        )
    )
    results["mechanical"] = mech_result
    status = "PASS" if mech_result.success else "FAIL"
    print(f"       Stress analysis: {status}")

    # -----------------------------------------------------------------------
    # Step 4: Run Electronics Agent -- ERC
    # -----------------------------------------------------------------------
    print("[4/8] Running Electronics Agent (ERC)...")
    ee_agent = ElectronicsAgent(twin=twin, mcp=mcp)
    ee_result = await ee_agent.run_task(
        EETaskRequest(
            task_type="run_erc",
            work_product_id=schematic_artifact.id,
            parameters={
                "schematic_file": "eda/kicad/drone_fc.kicad_sch",
            },
        )
    )
    results["electronics"] = ee_result
    status = "PASS" if ee_result.success else "FAIL"
    print(f"       ERC check: {status}")

    # -----------------------------------------------------------------------
    # Step 5: Run Firmware Agent -- HAL generation
    # -----------------------------------------------------------------------
    print("[5/8] Running Firmware Agent (HAL generation)...")
    fw_agent = FirmwareAgent(twin=twin, mcp=mcp)
    fw_result = await fw_agent.run_task(
        FWTaskRequest(
            task_type="generate_hal",
            work_product_id=fw_artifact.id,
            parameters={
                "mcu_family": "STM32F4",
                "peripherals": ["GPIO", "SPI", "I2C", "UART", "TIM", "ADC"],
                "output_dir": "firmware/hal",
            },
        )
    )
    results["firmware"] = fw_result
    status = "PASS" if fw_result.success else "FAIL"
    print(f"       HAL generation: {status}")

    # -----------------------------------------------------------------------
    # Step 6: Run Simulation Agent -- FEA static
    # -----------------------------------------------------------------------
    print("[6/8] Running Simulation Agent (FEA thermal/structural)...")
    sim_agent = SimulationAgent(twin=twin, mcp=mcp)
    sim_result = await sim_agent.run_task(
        SimTaskRequest(
            task_type="run_fea",
            work_product_id=cad_artifact.id,
            parameters={
                "mesh_file": "models/motor_mount_bracket.inp",
                "load_cases": [{"name": "hover_3g", "force_n": 30.0}],
                "analysis_type": "static",
                "material": "aluminum_6061",
            },
        )
    )
    results["simulation"] = sim_result
    status = "PASS" if sim_result.success else "FAIL"
    print(f"       FEA analysis: {status}")

    # -----------------------------------------------------------------------
    # Step 7: Run Supply Chain Agent -- BOM risk
    # -----------------------------------------------------------------------
    print("[7/8] Running Supply Chain Agent (BOM risk scoring)...")
    sc_agent = SupplyChainAgent(twin=twin, mcp=mcp)
    sc_result = await sc_agent.run_task(
        SCTaskRequest(
            task_type="score_bom_risk",
            work_product_id=bom_artifact.id,
            parameters={
                "parts": DRONE_BOM_PARTS,
                "risk_threshold": 0.6,
            },
        )
    )
    results["supply_chain"] = sc_result
    status = "PASS" if sc_result.success else "FAIL"
    print(f"       BOM risk scoring: {status}")

    # -----------------------------------------------------------------------
    # Step 8: Run Compliance Agent -- checklist
    # -----------------------------------------------------------------------
    print("[8/8] Running Compliance Agent (checklist generation)...")
    compliance_agent = ComplianceAgent(twin=twin, mcp=mcp)
    compliance_result = await compliance_agent.run_task(
        ComplianceTaskRequest(
            task_type="generate_checklist",
            work_product_id=bom_artifact.id,
            parameters={
                "markets": ["UKCA", "CE", "FCC"],
                "product_category": "electronic_device",
            },
        )
    )
    results["compliance"] = compliance_result
    status = "PASS" if compliance_result.success else "FAIL"
    print(f"       Compliance checklist: {status}")

    return results


def run_gate_evaluation(results: dict[str, Any]) -> Any:
    """Run EVT gate readiness evaluation on collected results."""
    gate_engine = GateEngine()

    # Map agent results to gate check names
    agent_results: dict[str, dict[str, Any]] = {}

    mech = results.get("mechanical")
    if mech is not None:
        agent_results["mechanical_stress"] = {"success": mech.success}

    ee = results.get("electronics")
    if ee is not None:
        agent_results["electronics_erc"] = {"success": ee.success}

    fw = results.get("firmware")
    if fw is not None:
        agent_results["firmware_hal"] = {"success": fw.success}

    sim = results.get("simulation")
    if sim is not None:
        agent_results["simulation_fea"] = {"success": sim.success}

    sc = results.get("supply_chain")
    if sc is not None:
        agent_results["supply_chain_bom"] = {"success": sc.success}

    comp = results.get("compliance")
    if comp is not None:
        agent_results["compliance_checklist"] = {"success": comp.success}

    return gate_engine.evaluate_readiness(GateStage.EVT, agent_results)


def print_summary(results: dict[str, Any], readiness: Any) -> None:
    """Print formatted summary of pipeline results."""
    print()
    print("=" * 70)
    print("  MetaForge Multi-Agent Pipeline -- Summary")
    print("=" * 70)
    print()

    # Agent results
    print("  Agent Results:")
    print("  " + "-" * 66)
    print(f"  {'Agent':<20} {'Task':<22} {'Status':<10} {'Details'}")
    print("  " + "-" * 66)

    for agent_name, result in results.items():
        status = "PASS" if result.success else "FAIL"
        detail = ""
        if result.skill_results:
            sr = result.skill_results[0]
            skill = sr.get("skill", "")
            if skill == "validate_stress":
                cr = sr.get("constraint_results", [])
                if cr:
                    max_stress = max(c["stress_mpa"] for c in cr)
                    detail = f"max stress {max_stress:.1f} MPa"
            elif skill == "run_erc":
                detail = sr.get("summary", "")[:30]
            elif skill == "generate_hal":
                files = sr.get("generated_files", [])
                detail = f"{len(files)} files generated"
            elif skill == "run_fea":
                sf = sr.get("safety_factor", 0)
                detail = f"safety factor {sf:.2f}"
            elif skill == "score_bom_risk":
                detail = (
                    f"risk={sr.get('overall_risk_score', 0):.0%}, "
                    f"{sr.get('high_risk_parts', 0)} high-risk"
                )
            elif skill == "generate_checklist":
                detail = (
                    f"{sr.get('total_items', 0)} items, "
                    f"{len(sr.get('markets_covered', []))} markets"
                )
        print(f"  {agent_name:<20} {result.task_type:<22} {status:<10} {detail}")

    print("  " + "-" * 66)

    # Supply chain details
    sc_result = results.get("supply_chain")
    if sc_result and sc_result.skill_results:
        sr = sc_result.skill_results[0]
        print()
        print("  BOM Risk Analysis:")
        print(f"    Overall risk score: {sr.get('overall_risk_score', 0):.0%}")
        print(f"    Total BOM cost:     ${sr.get('total_bom_cost_usd', 0):.2f}")
        high_risk_parts = [p for p in sr.get("part_risks", []) if p.get("risk_score", 0) >= 0.6]
        if high_risk_parts:
            print(f"    Critical parts ({len(high_risk_parts)}):")
            for p in high_risk_parts:
                factors = ", ".join(p.get("risk_factors", []))
                print(f"      - {p['mpn']}: risk={p['risk_score']:.0%} ({factors})")

    # Compliance details
    comp_result = results.get("compliance")
    if comp_result and comp_result.skill_results:
        sr = comp_result.skill_results[0]
        print()
        print("  Compliance Coverage:")
        print(f"    Markets:  {', '.join(sr.get('markets_covered', []))}")
        print(f"    Items:    {sr.get('total_items', 0)}")
        print(f"    Coverage: {sr.get('coverage_pct', 0):.0f}%")

    # Gate readiness
    print()
    print("  EVT Gate Readiness:")
    print(f"    Score:    {readiness.score:.0%}")
    print(f"    Status:   {'PASS' if readiness.passed else 'FAIL'}")
    if readiness.blockers:
        print(f"    Blockers ({len(readiness.blockers)}):")
        for b in readiness.blockers:
            print(f"      - {b}")
    if readiness.warnings:
        print("    Warnings:")
        for w in readiness.warnings:
            print(f"      - {w}")

    print()
    print("=" * 70)
    print("  Demo complete. All 6 domain agents exercised.")
    print("=" * 70)


async def main() -> None:
    """Run the full multi-agent pipeline demo."""
    print("=" * 70)
    print("  MetaForge E2E Demo: Drone Flight Controller Full Pipeline")
    print("=" * 70)
    print()

    results = await run_pipeline()
    readiness = run_gate_evaluation(results)
    print_summary(results, readiness)


if __name__ == "__main__":
    asyncio.run(main())
