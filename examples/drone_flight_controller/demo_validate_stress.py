#!/usr/bin/env python3
"""End-to-end demo: Drone motor mount bracket stress validation.

Exercises the complete MetaForge vertical stack:
  Human intent → Digital Twin → Mechanical Agent → validate_stress skill
  → MCP Protocol → CalculiX FEA → Results back to Twin

Run:
    python -m examples.drone_flight_controller.demo_validate_stress

Or directly:
    python examples/drone_flight_controller/demo_validate_stress.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import structlog

# Ensure the project root is on sys.path when running directly
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from domain_agents.mechanical.agent import MechanicalAgent, TaskRequest  # noqa: E402
from mcp_core.client import McpClient  # noqa: E402
from mcp_core.schemas import ToolManifest as ClientToolManifest  # noqa: E402
from mcp_core.transports import LoopbackTransport  # noqa: E402
from skill_registry.mcp_client_bridge import McpClientBridge  # noqa: E402
from tool_registry.tools.calculix.adapter import CalculixServer  # noqa: E402
from twin_core.api import InMemoryTwinAPI  # noqa: E402
from twin_core.models.artifact import Artifact  # noqa: E402
from twin_core.models.enums import ArtifactType  # noqa: E402

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO+
)
log = structlog.get_logger()

# Realistic FEA results for the drone motor mount bracket
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


async def main() -> None:
    print("=" * 70)
    print("  MetaForge E2E Demo: Drone Motor Mount Stress Validation")
    print("=" * 70)
    print()

    # --- Step 1: Set up the Digital Twin ---
    print("[1/6] Initializing Digital Twin...")
    twin = InMemoryTwinAPI.create()

    artifact = Artifact(
        name="motor-mount-bracket-v1",
        type=ArtifactType.CAD_MODEL,
        domain="mechanical",
        file_path="models/motor_mount_bracket.step",
        content_hash="sha256:a1b2c3d4e5f6",
        format="step",
        created_by="human",
        metadata={
            "material": "Al6061-T6",
            "yield_strength_mpa": 276.0,
            "mass_kg": 0.045,
            "description": "Motor mount bracket for drone flight controller",
        },
    )
    created = await twin.create_artifact(artifact)
    await twin.create_branch("main")
    await twin.commit("main", "Add motor mount bracket CAD model", "engineer")
    print(f"       Artifact '{created.name}' added to Twin (id={created.id})")

    # --- Step 2: Set up CalculiX tool adapter ---
    print("[2/6] Starting CalculiX tool adapter...")
    server = CalculixServer()
    # Stub the solver binary (would call ccx in production)
    server._execute_solver = AsyncMock(return_value=MOCK_FEA_RESULT)
    print(f"       Registered tools: {server.tool_ids}")

    # --- Step 3: Wire up MCP protocol stack ---
    print("[3/6] Connecting MCP protocol stack...")
    transport = LoopbackTransport(server)
    client = McpClient()
    await client.connect("calculix", transport)

    # Register tool manifests on the client
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

    # Verify health
    health = await client.health_check("calculix")
    print(
        f"       CalculiX adapter: {health.status} (v{health.version}, "
        f"{health.tools_available} tools)"
    )

    bridge = McpClientBridge(client)

    # --- Step 4: Create MechanicalAgent ---
    print("[4/6] Creating Mechanical Agent...")
    agent = MechanicalAgent(twin=twin, mcp=bridge)

    # --- Step 5: Run stress validation ---
    print("[5/6] Running stress validation (hover 3g load case)...")
    print()

    result = await agent.run_task(
        TaskRequest(
            task_type="validate_stress",
            artifact_id=created.id,
            parameters={
                "mesh_file_path": "models/motor_mount_bracket.inp",
                "load_case": "hover_3g",
                "constraints": [
                    {
                        "max_von_mises_mpa": 276.0,  # Al6061-T6 yield strength
                        "safety_factor": 1.5,
                        "material": "Al6061-T6",
                    }
                ],
            },
        )
    )

    # --- Step 6: Display results ---
    print("[6/6] Results:")
    print()
    status = "PASS" if result.success else "FAIL"
    print(f"  Overall: {status}")
    print(f"  Task:    {result.task_type}")
    print()

    if result.skill_results:
        sr = result.skill_results[0]
        fea = sr.get("fea_result", {})
        print(f"  FEA Solver Time:  {fea.get('solver_time', 0):.1f}s")
        print(f"  Mesh Elements:    {fea.get('mesh_elements', 0):,}")
        print()
        print("  Stress Results by Region:")
        print("  " + "-" * 62)
        print(f"  {'Region':<20} {'Stress (MPa)':<15} {'Allowable':<15} {'Status':<10}")
        print("  " + "-" * 62)

        for cr in sr.get("constraint_results", []):
            status_str = "PASS" if cr["passed"] else "FAIL"
            print(
                f"  {cr['region']:<20} "
                f"{cr['stress_mpa']:<15.1f} "
                f"{cr['allowable_mpa']:<15.1f} "
                f"{status_str:<10}"
            )
        print("  " + "-" * 62)

    if result.warnings:
        print()
        print("  Warnings:")
        for w in result.warnings:
            print(f"    - {w}")

    if result.errors:
        print()
        print("  Errors:")
        for e in result.errors:
            print(f"    - {e}")

    # --- Update Twin with results ---
    if result.success and result.skill_results:
        sr = result.skill_results[0]
        await twin.update_artifact(
            created.id,
            {
                "metadata": {
                    **created.metadata,
                    "stress_analysis": {
                        "status": "passed",
                        "load_case": "hover_3g",
                        "max_stress_mpa": max(
                            float(v) for v in sr["fea_result"]["max_von_mises"].values()
                        ),
                        "critical_region": "fillet_region",
                        "safety_factor_min": min(
                            cr["allowable_mpa"] / cr["stress_mpa"]
                            for cr in sr["constraint_results"]
                            if cr["stress_mpa"] > 0
                        ),
                    },
                }
            },
        )
        print()
        print("  Twin updated with stress analysis results.")

    print()
    print("=" * 70)
    print("  Demo complete. All layers exercised successfully.")
    print("=" * 70)

    # Cleanup
    await client.disconnect("calculix")


if __name__ == "__main__":
    asyncio.run(main())
