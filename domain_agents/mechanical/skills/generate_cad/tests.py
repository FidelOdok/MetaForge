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

CADQUERY_CAD_RESULT = {
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

FREECAD_CAD_RESULT = {
    "cad_file": "output/bracket_freecad.step",
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


async def _make_ctx_and_handler(
    register_cadquery: bool = True,
    register_freecad: bool = False,
) -> tuple[SkillContext, GenerateCadHandler, WorkProduct]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()

    if register_cadquery:
        mcp.register_tool(
            "cadquery.create_parametric", capability="cad_generation", name="Create Parametric"
        )
        mcp.register_tool_response("cadquery.create_parametric", CADQUERY_CAD_RESULT)

    if register_freecad:
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

    async def test_execute_bracket_cadquery(self):
        """Happy path: generate a bracket shape with CadQuery backend."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
                material="aluminum_6061",
                backend="cadquery",
            )
        )

        assert output.cad_file == "output/bracket_test.step"
        assert output.volume_mm3 == 12500.0
        assert output.surface_area_mm2 == 8400.0
        assert output.shape_type == "bracket"
        assert output.material == "aluminum_6061"
        assert output.bounding_box.max_x == 50.0

    async def test_execute_bracket_freecad(self):
        """Generate a bracket shape with FreeCAD backend."""
        _ctx, handler, work_product = await _make_ctx_and_handler(
            register_cadquery=False, register_freecad=True
        )

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
                backend="freecad",
            )
        )

        assert output.cad_file == "output/bracket_freecad.step"

    async def test_fallback_to_freecad_when_cadquery_unavailable(self):
        """Falls back to FreeCAD when CadQuery is not available."""
        _ctx, handler, work_product = await _make_ctx_and_handler(
            register_cadquery=False, register_freecad=True
        )

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
                backend="cadquery",  # Requested cadquery but only freecad is available
            )
        )

        assert output.cad_file == "output/bracket_freecad.step"

    async def test_fallback_to_cadquery_when_freecad_unavailable(self):
        """Falls back to CadQuery when FreeCAD is not available."""
        _ctx, handler, work_product = await _make_ctx_and_handler(
            register_cadquery=True, register_freecad=False
        )

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="plate",
                dimensions={"width": 100.0, "height": 80.0, "thickness": 2.0},
                backend="freecad",  # Requested freecad but only cadquery is available
            )
        )

        assert output.cad_file == "output/bracket_test.step"

    async def test_no_backend_available_raises(self):
        """Raises RuntimeError when no CAD backend is available."""
        _ctx, handler, work_product = await _make_ctx_and_handler(
            register_cadquery=False, register_freecad=False
        )

        with pytest.raises(RuntimeError, match="No CAD backend available"):
            await handler.execute(
                GenerateCadInput(
                    work_product_id=work_product.id,
                    shape_type="bracket",
                    dimensions={"width": 50.0},
                )
            )

    async def test_default_backend_is_cadquery(self):
        """Default backend is cadquery."""
        inp = GenerateCadInput(
            work_product_id=uuid4(),
            shape_type="bracket",
            dimensions={"width": 50.0},
        )
        assert inp.backend == "cadquery"

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

    async def test_preconditions_no_backend(self):
        """Precondition check fails when no MCP tool is available."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
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
        assert any("No CAD backend" in e for e in errors)

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

    async def test_commit_skipped_when_tool_unavailable(self):
        """Default commit=True degrades gracefully when twin.commit_geometry isn't registered."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
            )
        )

        assert output.committed is False
        assert output.twin_node_id is None
        assert output.commit_error == "twin.commit_geometry tool is not available"

    async def test_commit_geometry_invoked_when_available(self, tmp_path):
        """When commit=True and the tool is available, the STEP file is read and committed."""
        ctx, handler, work_product = await _make_ctx_and_handler()

        step_file = tmp_path / "bracket_test.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
        ctx.mcp.register_tool_response(
            "cadquery.create_parametric", {**CADQUERY_CAD_RESULT, "cad_file": str(step_file)}
        )
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response(
            "twin.commit_geometry",
            {"node_id": "node-123", "model_url": "https://twin.local/models/node-123"},
        )

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
                project_id="13d60463-433b-4735-af07-690cbf8e07b9",
            )
        )

        assert output.committed is True
        assert output.twin_node_id == "node-123"
        assert output.model_url == "https://twin.local/models/node-123"
        assert output.commit_error is None

    async def test_commit_false_skips_persistence(self):
        """commit=False never attempts to persist, even when the tool is available."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-123"})

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
                commit=False,
            )
        )

        assert output.committed is False
        assert output.commit_error is None

    async def test_commit_error_when_file_unreadable(self):
        """A cad_file this process can't read (e.g. containerized backend) reports commit_error."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-123"})

        output = await handler.execute(
            GenerateCadInput(
                work_product_id=work_product.id,
                shape_type="bracket",
                dimensions={"width": 50.0, "height": 30.0, "thickness": 5.0},
            )
        )

        assert output.committed is False
        assert output.commit_error is not None
        assert "output/bracket_test.step" in output.commit_error

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
