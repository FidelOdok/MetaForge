"""Skill-specific tests for generate_mesh (FORGE-560)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import GenerateMeshHandler
from .schema import GenerateMeshInput


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


class TestGenerateMeshSkill:
    async def test_measured_quality_is_judged(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        ctx.mcp.register_tool_response(
            "freecad.generate_mesh",
            {
                "mesh_file": "m.inp",
                "num_nodes": 795,
                "num_elements": 2569,
                "quality_metrics": {
                    "min_angle": 13.2,
                    "max_aspect_ratio": 2.46,
                    "avg_quality": 0.78,
                },
            },
        )
        out = await GenerateMeshHandler(ctx).execute(
            GenerateMeshInput(work_product_id=uuid4(), cad_file="part.step")
        )
        assert out.quality_acceptable is True

    async def test_unmeasured_mesh_is_not_acceptable(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool("freecad.generate_mesh", "mesh_generation")
        ctx.mcp.register_tool_response(
            "freecad.generate_mesh", {"mesh_file": "m.unv", "num_nodes": 1, "num_elements": 1}
        )
        out = await GenerateMeshHandler(ctx).execute(
            GenerateMeshInput(work_product_id=uuid4(), cad_file="part.step", output_format="unv")
        )
        assert out.quality_acceptable is False
