"""Skill-specific tests for analyze_hazards.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_analyze_hazards.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import AnalyzeHazardsHandler
from .schema import AnalyzeHazardsInput, Hazard


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture()
def sample_input() -> AnalyzeHazardsInput:
    return AnalyzeHazardsInput(
        project_id="proj-1",
        system_name="Quadruped Leg",
        hazards=[
            Hazard(
                hazard="Pinch point",
                cause="Exposed gear mesh",
                effect="Finger injury",
                severity=4,
                likelihood=3,
                mitigation="Add guard",
            )
        ],
    )


class TestAnalyzeHazardsSkill:
    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: AnalyzeHazardsInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_hazard_analysis", "twin_hazard_analysis")
        mock_context.mcp.register_tool_response(
            "twin.commit_hazard_analysis",
            {
                "node_id": "node-1",
                "hazard_count": 1,
                "highest_risk_score": 12,
                "unmitigated_count": 0,
            },
        )
        handler = AnalyzeHazardsHandler(mock_context)
        output = await handler.execute(sample_input)
        assert output.node_id == "node-1"
        assert output.overall_risk_level == "high"

    async def test_preconditions_catch_missing_tool(
        self, mock_context: SkillContext, sample_input: AnalyzeHazardsInput
    ) -> None:
        handler = AnalyzeHazardsHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not available" in e for e in errors)
