"""Skill-specific tests for generate_technical_drawing.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_generate_technical_drawing.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import GenerateTechnicalDrawingHandler
from .schema import DrawingDimension, GenerateTechnicalDrawingInput


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
def sample_input() -> GenerateTechnicalDrawingInput:
    return GenerateTechnicalDrawingInput(
        work_product_id=uuid4(),
        part_name="Bracket",
        dimensions=[DrawingDimension(feature="bore_dia", nominal_mm=8.0)],
    )


class TestGenerateTechnicalDrawingSkill:
    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: GenerateTechnicalDrawingInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("twin.commit_technical_drawing", "twin_technical_drawing")
        mock_context.mcp.register_tool_response(
            "twin.commit_technical_drawing",
            {
                "node_id": "node-1",
                "dimension_count": 1,
                "gdt_callout_count": 0,
                "surface_finish_count": 0,
            },
        )
        handler = GenerateTechnicalDrawingHandler(mock_context)
        output = await handler.execute(sample_input)
        assert output.node_id == "node-1"

    async def test_preconditions_catch_missing_cad_model(
        self, mock_context: SkillContext, sample_input: GenerateTechnicalDrawingInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = None
        mock_context.mcp.register_tool("twin.commit_technical_drawing", "twin_technical_drawing")
        handler = GenerateTechnicalDrawingHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not found" in e for e in errors)
