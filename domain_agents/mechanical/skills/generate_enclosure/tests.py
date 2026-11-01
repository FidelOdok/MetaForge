"""Unit tests for the generate_enclosure skill."""

from __future__ import annotations

from uuid import uuid4

import pytest
import structlog
from pydantic import ValidationError

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from .handler import GenerateEnclosureHandler
from .schema import ConnectorCutout, GenerateEnclosureInput, MountingHole

ENCLOSURE_RESULT = {
    "cad_file": "output/enclosure.step",
    "internal_volume": 12870.0,
    "external_dimensions": {
        "length": 90.0,
        "width": 60.0,
        "height": 19.2,
    },
    "mounting_info": {
        "hole_count": 4,
        "cutout_count": 1,
    },
    "material": "ABS",
    # Real cadquery.generate_enclosure spreads _get_shape_properties() (+
    # mass_kg, FORGE-100) into its response alongside the fields above.
    "volume_mm3": 15200.0,
    "surface_area_mm2": 9800.0,
    "bounding_box": {
        "min_x": -45.0,
        "min_y": -30.0,
        "min_z": 0.0,
        "max_x": 45.0,
        "max_y": 30.0,
        "max_z": 19.2,
    },
    "mass_kg": 0.0158,
}


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="test-enclosure",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/test_enclosure.step",
        content_hash="sha256:test789",
        format="step",
        created_by="human",
        metadata={},
    )


async def _make_ctx_and_handler() -> tuple[SkillContext, GenerateEnclosureHandler, WorkProduct]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    mcp.register_tool(
        "cadquery.generate_enclosure", capability="cad_enclosure", name="Generate Enclosure"
    )
    mcp.register_tool_response("cadquery.generate_enclosure", ENCLOSURE_RESULT)

    work_product = await twin.create_work_product(_make_work_product())

    ctx = SkillContext(
        twin=twin,
        mcp=mcp,
        logger=structlog.get_logger().bind(skill="generate_enclosure"),
        session_id=uuid4(),
        branch="main",
    )
    handler = GenerateEnclosureHandler(ctx)
    return ctx, handler, work_product


class TestGenerateEnclosureHandler:
    """Unit tests for GenerateEnclosureHandler."""

    async def test_execute_basic(self):
        """Happy path: generate an enclosure from PCB dimensions."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
                connector_cutouts=[
                    ConnectorCutout(width=12.0, height=6.0, side="back"),
                ],
                mounting_holes=[
                    MountingHole(x=5.0, y=5.0),
                    MountingHole(x=75.0, y=5.0),
                    MountingHole(x=5.0, y=45.0),
                    MountingHole(x=75.0, y=45.0),
                ],
            )
        )

        assert output.cad_file == "output/enclosure.step"
        assert output.internal_volume == 12870.0
        assert output.external_dimensions.length == 90.0
        assert output.mounting_info.hole_count == 4
        assert output.mounting_info.cutout_count == 1

    async def test_execute_minimal(self):
        """Generate enclosure with minimal inputs (no cutouts/holes)."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=60.0,
                pcb_width=40.0,
            )
        )

        assert output.cad_file == "output/enclosure.step"

    async def test_preconditions_missing_tool(self):
        """Precondition check fails when tool is unavailable."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_work_product())

        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="generate_enclosure"),
            session_id=uuid4(),
            branch="main",
        )
        handler = GenerateEnclosureHandler(ctx)

        errors = await handler.validate_preconditions(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
            )
        )
        assert any("not available" in e for e in errors)

    async def test_run_pipeline(self):
        """Full skill pipeline."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        result = await handler.run(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
            )
        )

        assert result.success is True
        assert result.data is not None

    async def test_work_product_id_is_optional(self):
        """FORGE-84: work_product_id is no longer required -- the precondition
        check is skipped entirely when it's omitted, same as generate_cad."""
        _ctx, handler, _work_product = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            GenerateEnclosureInput(name="Test Enclosure", pcb_length=80.0, pcb_width=50.0)
        )
        assert errors == []

        output = await handler.execute(
            GenerateEnclosureInput(name="Test Enclosure", pcb_length=80.0, pcb_width=50.0)
        )
        assert output.work_product_id is None
        assert output.cad_file == "output/enclosure.step"

    async def test_commit_skipped_when_tool_unavailable(self):
        """FORGE-84: default commit=True degrades gracefully when
        twin.commit_geometry isn't registered -- this skill never used to
        attempt a commit at all, so this is new coverage, not a regression."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
            )
        )

        assert output.committed is False
        assert output.twin_node_id is None
        assert output.commit_error == "twin.commit_geometry tool is not available"

    async def test_commit_geometry_invoked_when_available(self, tmp_path):
        """FORGE-84: when commit=True and the tool is available, the STEP
        file is read and committed -- mirrors generate_cad's own test."""
        ctx, handler, work_product = await _make_ctx_and_handler()

        step_file = tmp_path / "enclosure.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
        ctx.mcp.register_tool_response(
            "cadquery.generate_enclosure", {**ENCLOSURE_RESULT, "cad_file": str(step_file)}
        )
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response(
            "twin.commit_geometry",
            {"node_id": "node-456", "model_url": "https://twin.local/models/node-456"},
        )

        output = await handler.execute(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
                project_id="13d60463-433b-4735-af07-690cbf8e07b9",
            )
        )

        assert output.committed is True
        assert output.twin_node_id == "node-456"
        assert output.model_url == "https://twin.local/models/node-456"
        assert output.commit_error is None

    async def test_commit_uses_the_caller_supplied_name_not_a_generic_one(self, tmp_path):
        """FORGE-97: never the old synthetic "Enclosure (material)" pattern."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        step_file = tmp_path / "enclosure.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
        ctx.mcp.register_tool_response(
            "cadquery.generate_enclosure", {**ENCLOSURE_RESULT, "cad_file": str(step_file)}
        )
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-456"})

        await handler.execute(
            GenerateEnclosureInput(
                name="Base Housing",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
            )
        )

        commit_call = next(c for c in ctx.mcp.calls if c[0] == "twin.commit_geometry")
        assert commit_call[1]["name"] == "Base Housing"

    async def test_commit_threads_measured_properties_as_extra_metadata(self, tmp_path):
        """FORGE-100: measured properties from the tool result reach the Twin
        as top-level work-product metadata, not just embedded in a nested
        enclosure-specific field."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        step_file = tmp_path / "enclosure.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
        ctx.mcp.register_tool_response(
            "cadquery.generate_enclosure", {**ENCLOSURE_RESULT, "cad_file": str(step_file)}
        )
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-456"})

        await handler.execute(
            GenerateEnclosureInput(
                name="Base Housing",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
            )
        )

        commit_call = next(c for c in ctx.mcp.calls if c[0] == "twin.commit_geometry")
        assert commit_call[1]["extra_metadata"] == {
            "volume_mm3": 15200.0,
            "surface_area_mm2": 9800.0,
            "mass_kg": 0.0158,
            "bbox_mm": ENCLOSURE_RESULT["bounding_box"],
        }

    def test_name_is_required_and_non_empty(self):
        with pytest.raises(ValidationError):
            GenerateEnclosureInput(pcb_length=80.0, pcb_width=50.0)
        with pytest.raises(ValidationError):
            GenerateEnclosureInput(name="", pcb_length=80.0, pcb_width=50.0)

    async def test_commit_false_skips_persistence(self):
        """FORGE-84: commit=False never attempts to persist, even when the
        tool is available."""
        _ctx, handler, work_product = await _make_ctx_and_handler()
        _ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        _ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-456"})

        output = await handler.execute(
            GenerateEnclosureInput(
                name="Test Enclosure",
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
                commit=False,
            )
        )

        assert output.committed is False
        assert output.commit_error is None
