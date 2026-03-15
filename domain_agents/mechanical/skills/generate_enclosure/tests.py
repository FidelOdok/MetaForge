"""Unit tests for the generate_enclosure skill."""

from __future__ import annotations

from uuid import uuid4

import structlog

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
                work_product_id=work_product.id,
                pcb_length=80.0,
                pcb_width=50.0,
            )
        )

        assert result.success is True
        assert result.data is not None
