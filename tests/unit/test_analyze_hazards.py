"""Tests for the analyze_hazards skill (MET-747 lifecycle-mapping follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.compliance.skills.analyze_hazards.handler import AnalyzeHazardsHandler
from domain_agents.compliance.skills.analyze_hazards.schema import (
    AnalyzeHazardsInput,
    AnalyzeHazardsOutput,
    Hazard,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    ctx.metrics_collector = None
    ctx.domain = "compliance"
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
            ),
            Hazard(
                hazard="Thermal runaway",
                cause="Undersized heatsink",
                effect="Fire",
                severity=5,
                likelihood=5,
                mitigation="",
            ),
        ],
    )


class TestHazardSchemas:
    def test_severity_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            Hazard(hazard="x", cause="x", effect="x", severity=6, likelihood=1)

    def test_hazards_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            AnalyzeHazardsInput(project_id="p", system_name="s", hazards=[])


class TestAnalyzeHazardsHandler:
    async def test_risk_level_classification(
        self, mock_context: SkillContext, sample_input: AnalyzeHazardsInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_hazard_analysis", "twin_hazard_analysis")
        mock_context.mcp.register_tool_response(
            "twin.commit_hazard_analysis",
            {
                "node_id": "node-1",
                "hazard_count": 2,
                "highest_risk_score": 25,
                "unmitigated_count": 1,
            },
        )
        handler = AnalyzeHazardsHandler(mock_context)
        output = await handler.execute(sample_input)
        assert isinstance(output, AnalyzeHazardsOutput)
        assert output.overall_risk_level == "critical"
        assert output.unmitigated_count == 1

    async def test_invokes_commit_tool_with_serialized_hazards(
        self, mock_context: SkillContext, sample_input: AnalyzeHazardsInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_hazard_analysis", "twin_hazard_analysis")
        mock_context.mcp.register_tool_response(
            "twin.commit_hazard_analysis",
            {
                "node_id": "node-1",
                "hazard_count": 2,
                "highest_risk_score": 25,
                "unmitigated_count": 1,
            },
        )
        handler = AnalyzeHazardsHandler(mock_context)
        await handler.execute(sample_input)
        tool_id, params = mock_context.mcp.calls[0]
        assert tool_id == "twin.commit_hazard_analysis"
        assert params["system_name"] == "Quadruped Leg"
        assert len(params["hazards"]) == 2
        assert params["hazards"][0]["hazard"] == "Pinch point"


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
        self, mock_context: SkillContext, sample_input: AnalyzeHazardsInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_hazard_analysis", "twin_hazard_analysis")
        mock_context.mcp.register_tool_response(
            "twin.commit_hazard_analysis",
            {
                "node_id": "node-1",
                "hazard_count": 2,
                "highest_risk_score": 25,
                "unmitigated_count": 1,
            },
        )
        handler = AnalyzeHazardsHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is True
        assert result.data is not None
        assert result.data.node_id == "node-1"

    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, sample_input: AnalyzeHazardsInput
    ) -> None:
        handler = AnalyzeHazardsHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is False
        assert result.data is None
