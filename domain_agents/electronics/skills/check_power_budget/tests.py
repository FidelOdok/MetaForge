"""Skill-specific tests for check_power_budget (FORGE-544)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import CheckPowerBudgetHandler
from .schema import CheckPowerBudgetInput

RESULT = {
    "verdict": "fail",
    "passed": False,
    "derating": 0.8,
    "rails": [
        {"name": "3V3", "status": "fail", "load_ma": 500.0, "allowed_ma": 480.0},
        {"name": "5V0", "status": "not_established", "load_ma": 500.0},
    ],
    "worst_rail": "3V3",
    "source_power_mw": None,
}


@pytest.fixture()
def power_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    ctx.metrics_collector = None
    ctx.domain = "electronics"
    return ctx


class TestCheckPowerBudgetSkill:
    async def test_passes_the_tool_verdict_through(self, power_context: SkillContext) -> None:
        power_context.mcp.register_tool("power.check_budget", "power_budget")
        power_context.mcp.register_tool_response("power.check_budget", RESULT)
        out = await CheckPowerBudgetHandler(power_context).execute(
            CheckPowerBudgetInput(rails=[{"name": "3V3"}], derating=0.8)
        )
        assert out.verdict == "fail"
        assert out.passed is False
        assert "Over budget: 3V3" in out.summary
        assert "Not established" in out.summary

    async def test_precondition_names_the_missing_tool(self, power_context: SkillContext) -> None:
        errors = await CheckPowerBudgetHandler(power_context).validate_preconditions(
            CheckPowerBudgetInput(rails=[{"name": "3V3"}], derating=0.8)
        )
        assert errors == ["power.check_budget tool is not available"]
