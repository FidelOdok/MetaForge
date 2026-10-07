"""Skill-specific tests for run_drc (FORGE-560)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import RunDrcHandler
from .schema import RunDrcInput


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


class TestRunDrcSkill:
    async def test_unrouted_board_fails_and_location_is_text(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool("kicad.run_drc", "drc_validation")
        ctx.mcp.register_tool_response(
            "kicad.run_drc",
            {
                "violations": [
                    {
                        "rule_id": "silk_overlap",
                        "severity": "warning",
                        "message": "silk",
                        "location": {"x": 1.5, "y": 2.0, "layer": "F.SilkS"},
                    }
                ],
                "unconnected_items": 2,
            },
        )
        out = await RunDrcHandler(ctx).execute(
            RunDrcInput(work_product_id=uuid4(), pcb_file="b.kicad_pcb")
        )
        assert out.passed is False
        assert out.unconnected_items == 2
        assert out.violations[0].location == "(1.5, 2.0) mm"
