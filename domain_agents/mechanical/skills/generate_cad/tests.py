"""Unit tests for the generate_cad skill."""

from __future__ import annotations

from uuid import uuid4

import pytest
import structlog

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from .handler import GenerateCadHandler
from .schema import GenerateCadInput

FREECAD_CAD_RESULT = {
    "cad_file": "output/bracket_test.step",
    "volume_mm3": 12500.0,
    "surface_area_mm2": 8400.0,
    "bounding_box": {
        "min_x": 0.0,
        "min_y": 0.0,
        "min_z": 0.0,
        "max_x": 50.0,
        "max_y": 30.0,
        "max_z": 5.0,
    },
    "parameters_used": {"width": 50.0, "height": 30.0, "thickness": 5.0},
}


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="test-bracket",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/test_bracket.step",
        content_hash="sha256:test123",
        format="step",
        created_by="human",
        metadata={"material": "Al6061-T6"},
    )


async def _make_ctx_and_handler() -> tuple[SkillContext, GenerateCadHandler, WorkProduct]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    mcp.register_tool(
        "freecad.create_parametric", capability="cad_generation", name="Create Parametric"
    )
    mcp.register_tool_response("freecad.create_parametric", FREECAD_CAD_RESULT)

    work_product = await twin.create_work_product(_make_work_product())

    ctx = SkillContext(
        twin=twin,
        mcp=mcp,
        logger=structlog.get_logger().bind(skill="generate_cad"),
        session_id=uuid4(),
        branch="main",
    )
    handler = GenerateCadHandler(ctx)
    return ctx, handler, work_product


class TestGenerateCadHandler:
    """Unit tests for GenerateCadHandler."""

    async def test_execute_bracket(self):
        """Happy path: generate a bracket shape."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
                material="aluminum_6061",
            )
        )

        assert output.cad_file == "output/bracket_test.step"
        assert output.volume_mm3 == 12500.0
        assert output.surface_area_mm2 == 8400.0
        assert output.shape_type == "bracket"
        assert output.material == "aluminum_6061"
        assert output.bounding_box.max_x == 50.0

    async def test_execute_plate(self):
        """Generate a plate shape."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="plate",
                dimensions={"width": 100.0, "height": 80.0, "thickness": 2.0},
            )
        )

        assert output.shape_type == "plate"
        assert output.cad_file == "output/bracket_test.step"

    async def test_unsupported_shape_raises(self):
        """Unsupported shape type raises ValueError."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        with pytest.raises(ValueError, match="Unsupported shape type"):
            await handler.execute(
                GenerateCadInput(
                    work_product_id=work_product.id,
                    shape_type="gearbox",
                    dimensions={"width": 10.0},
                )
            )

    async def test_preconditions_missing_artifact(self):
        """Precondition check fails when work_product is missing."""
        _ctx, handler, _artifact = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            GenerateCadInput(
                work_product_id=uuid4(),
                shape_type="bracket",
                dimensions={"width": 50.0},
            )
        )
        assert any("not found" in e for e in errors)

    async def test_preconditions_missing_tool(self):
        """Precondition check fails when MCP tool is unavailable."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        # Don't register the tool
        work_product = await twin.create_work_product(_make_work_product())

        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_cad"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateCadHandler(ctx)

        errors = await handler.validate_preconditions(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0},
            )
        )
        assert any("not available" in e for e in errors)

    async def test_run_pipeline(self):
        """Full skill pipeline (preconditions -> execute -> wrap)."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        result = await handler.run(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
            )
        )

        assert result.success is True
        assert result.data is not None
        assert result.duration_ms > 0
        assert result.errors == []

    async def test_validate_output_empty_path(self):
        """Output validation catches empty CAD file path."""
        from .schema import BoundingBox, GenerateCadOutput

        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_output(
            GenerateCadOutput(
                work_product_id=work_product.id,
                cad_file="",
                shape_type="bracket",
                volume_mm3=100.0,
                surface_area_mm2=50.0,
                bounding_box=BoundingBox(),
                parameters_used={},
                material="aluminum_6061",
            )
        )
        assert any("empty" in e for e in errors)

    async def test_validate_output_zero_volume(self):
        """Output validation catches zero volume."""
        from .schema import BoundingBox, GenerateCadOutput

        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_output(
            GenerateCadOutput(
                work_product_id=work_product.id,
                cad_file="output/test.step",
                shape_type="bracket",
                volume_mm3=0.0,
                surface_area_mm2=50.0,
                bounding_box=BoundingBox(),
                parameters_used={},
                material="aluminum_6061",
            )
        )
        assert any("volume" in e.lower() for e in errors)
