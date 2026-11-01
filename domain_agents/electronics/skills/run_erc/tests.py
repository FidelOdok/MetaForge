"""Skill-specific tests for run_erc (FORGE-560)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import RunErcHandler
from .schema import RunErcInput


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


class TestRunErcSkill:
    async def test_warnings_pass_errors_fail(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool("kicad.run_erc", "erc_validation")
        warn = {"rule_id": "lib_symbol_issues", "severity": "warning", "message": "w"}
        err = {"rule_id": "pin_not_connected", "severity": "error", "message": "e"}
        ctx.mcp.register_tool_response("kicad.run_erc", {"violations": [warn]})
        handler = RunErcHandler(ctx)
        inp = RunErcInput(work_product_id=uuid4(), schematic_file="a.kicad_sch")
        assert (await handler.execute(inp)).passed is True
        ctx.mcp.register_tool_response("kicad.run_erc", {"violations": [warn, err]})
        assert (await handler.execute(inp)).passed is False
