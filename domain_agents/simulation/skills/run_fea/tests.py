"""Skill-specific tests for run_fea (FORGE-560, FORGE-561)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import RunFeaHandler
from .schema import RunFeaInput


@pytest.fixture()
def ctx() -> SkillContext:
    c = MagicMock(spec=SkillContext)
    c.twin = AsyncMock()
    c.mcp = InMemoryMcpBridge()
    c.logger = MagicMock()
    c.logger.bind = MagicMock(return_value=c.logger)
    c.session_id = uuid4()
    c.branch = "main"
    return c


CASE = {
    "mesh_file": "m.inp",
    "load_case": "tip",
    "material": {"name": "aluminium_6061"},
    "fixed_node_set": "Surface1",
    "load_node_set": "Surface6",
    "load_force_n": [0, 0, -50],
}


class TestRunFeaSkill:
    async def test_reads_the_tools_result_shape(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool("calculix.run_fea", "stress_analysis")
        ctx.mcp.register_tool_response(
            "calculix.run_fea",
            {"max_von_mises": {"global": 80.0}, "displacement": {"max": 0.2}, "solver_time": 1.0},
        )
        out = await RunFeaHandler(ctx).execute(
            RunFeaInput(work_product_id=uuid4(), yield_strength_mpa=240.0, **CASE)
        )
        assert out.max_stress_mpa == 80.0
        assert out.safety_factor == 3.0
        assert await RunFeaHandler(ctx).validate_output(out) == []

    async def test_no_stress_is_a_failure_not_a_zero(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool("calculix.run_fea", "stress_analysis")
        ctx.mcp.register_tool_response("calculix.run_fea", {"solver_time": 1.0})
        out = await RunFeaHandler(ctx).execute(RunFeaInput(work_product_id=uuid4(), **CASE))
        assert out.max_stress_mpa is None and out.safety_factor is None
        assert await RunFeaHandler(ctx).validate_output(out)

    def test_static_alias_and_missing_load(self) -> None:
        assert RunFeaInput(
            work_product_id=uuid4(), analysis_type="static", **CASE
        ).analysis_type == ("static_stress")
        with pytest.raises(ValueError, match="load_force_n"):
            RunFeaInput(work_product_id=uuid4(), **{**CASE, "load_force_n": None})
