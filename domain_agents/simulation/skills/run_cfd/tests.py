"""Skill-specific tests for run_cfd (FORGE-543)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.simulation.skills.run_cfd.handler import RunCfdHandler
from domain_agents.simulation.skills.run_cfd.schema import RunCfdInput
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

CASE = {
    "mesh_file": "m.inp",
    "material": {"name": "aluminium_6061"},
    "heat_source_node_set": "Surface2",
    "power_dissipation_w": 3.0,
    "sink_node_set": "Surface1",
    "sink_temp_c": 40.0,
}


@pytest.fixture()
def cfd_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


class TestRunCfdSkill:
    async def test_conduction_only(self, cfd_context: SkillContext) -> None:
        cfd_context.mcp.register_tool("calculix.run_thermal", "thermal_analysis")
        cfd_context.mcp.register_tool_response(
            "calculix.run_thermal", {"max_temperature_c": 47.2, "min_temperature_c": 40.0}
        )
        out = await RunCfdHandler(cfd_context).execute(
            RunCfdInput(work_product_id=uuid4(), conduction=CASE)
        )
        assert out.analysis == "conduction_only"
        assert out.max_temperature_c == 47.2
        assert out.max_velocity_ms is None and out.pressure_drop_pa is None

    async def test_flow_is_refused(self, cfd_context: SkillContext) -> None:
        with pytest.raises(ValueError, match="no flow solver"):
            await RunCfdHandler(cfd_context).execute(
                RunCfdInput(
                    work_product_id=uuid4(),
                    conduction=CASE,
                    boundary_conditions={"inlet_velocity_ms": 2.0},
                )
            )
