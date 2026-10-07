"""Skill-specific tests for run_spice (FORGE-542)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.simulation.skills.run_spice.handler import RunSpiceHandler
from domain_agents.simulation.skills.run_spice.schema import RunSpiceInput
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext


@pytest.fixture()
def spice_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


class TestRunSpiceSkill:
    async def test_text_netlist_and_failure_log(self, spice_context: SkillContext) -> None:
        spice_context.mcp.register_tool("spice.run_simulation", "circuit_simulation")
        spice_context.mcp.register_tool_response(
            "spice.run_simulation",
            {"convergence": False, "results": {}, "log": "Error: unknown subckt"},
        )
        out = await RunSpiceHandler(spice_context).execute(
            RunSpiceInput(work_product_id=uuid4(), netlist="V1 a 0 1\n", analysis_type="op")
        )
        assert out.convergence is False
        assert "unknown subckt" in out.log

    def test_exactly_one_netlist_source(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            RunSpiceInput(work_product_id=uuid4(), analysis_type="op")
