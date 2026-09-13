"""Tests for the generate_technical_drawing skill (MET-747 lifecycle-mapping follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.mechanical.skills.generate_technical_drawing.handler import (
    GenerateTechnicalDrawingHandler,
)
from domain_agents.mechanical.skills.generate_technical_drawing.schema import (
    DrawingDimension,
    GdtCallout,
    GenerateTechnicalDrawingInput,
    GenerateTechnicalDrawingOutput,
    SurfaceFinish,
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
    ctx.domain = "mechanical"
    return ctx


@pytest.fixture()
def sample_input() -> GenerateTechnicalDrawingInput:
    return GenerateTechnicalDrawingInput(
        work_product_id=uuid4(),
        part_name="Bracket",
        dimensions=[
            DrawingDimension(
                feature="bore_dia", nominal_mm=8.0, tolerance_plus_mm=0.05, tolerance_minus_mm=0.0
            )
        ],
        gdt_callouts=[
            GdtCallout(
                feature="bore_dia", symbol="position", tolerance_value_mm=0.02, datum_refs=["A"]
            )
        ],
        surface_finishes=[SurfaceFinish(feature="bore_dia", ra_um=1.6)],
        inspection_requirements=["CMM bore check"],
    )


class TestSchemas:
    def test_dimensions_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            GenerateTechnicalDrawingInput(work_product_id=uuid4(), part_name="x", dimensions=[])

    def test_surface_finish_ra_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SurfaceFinish(feature="x", ra_um=0)


class TestGenerateTechnicalDrawingHandler:
    async def test_source_node_ids_includes_source_cad_model(
        self, mock_context: SkillContext, sample_input: GenerateTechnicalDrawingInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("twin.commit_technical_drawing", "twin_technical_drawing")
        mock_context.mcp.register_tool_response(
            "twin.commit_technical_drawing",
            {
                "node_id": "node-1",
                "dimension_count": 1,
                "gdt_callout_count": 1,
                "surface_finish_count": 1,
            },
        )
        handler = GenerateTechnicalDrawingHandler(mock_context)
        await handler.execute(sample_input)
        _, params = mock_context.mcp.calls[0]
        assert params["source_node_ids"] == [str(sample_input.work_product_id)]
        assert params["dimensions"][0]["feature"] == "bore_dia"

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
                "gdt_callout_count": 1,
                "surface_finish_count": 1,
            },
        )
        handler = GenerateTechnicalDrawingHandler(mock_context)
        output = await handler.execute(sample_input)
        assert isinstance(output, GenerateTechnicalDrawingOutput)
        assert output.gdt_callout_count == 1


class TestPreconditions:
    async def test_missing_cad_model_fails(
        self, mock_context: SkillContext, sample_input: GenerateTechnicalDrawingInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = None
        mock_context.mcp.register_tool("twin.commit_technical_drawing", "twin_technical_drawing")
        handler = GenerateTechnicalDrawingHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert len(errors) == 1
        assert "not found in Twin" in errors[0]

    async def test_tool_unavailable_fails(
        self, mock_context: SkillContext, sample_input: GenerateTechnicalDrawingInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        handler = GenerateTechnicalDrawingHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not available" in e for e in errors)
