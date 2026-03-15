"""Unit tests for the create_assembly skill."""

from __future__ import annotations

from uuid import uuid4

import structlog

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from .handler import CreateAssemblyHandler
from .schema import AssemblyConstraint, AssemblyPart, CreateAssemblyInput

ASSEMBLY_RESULT = {
    "assembly_file": "output/assembly.step",
    "part_count": 3,
    "total_volume": 45000.0,
    "interference_check_passed": True,
}


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="test-assembly",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/test_assembly.step",
        content_hash="sha256:testasm",
        format="step",
        created_by="human",
        metadata={},
    )


async def _make_ctx_and_handler() -> tuple[SkillContext, CreateAssemblyHandler, WorkProduct]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    mcp.register_tool(
        "cadquery.create_assembly", capability="cad_assembly", name="Create Assembly"
    )
    mcp.register_tool_response("cadquery.create_assembly", ASSEMBLY_RESULT)

    work_product = await twin.create_work_product(_make_work_product())

    ctx = SkillContext(
        twin=twin,
        mcp=mcp,
        logger=structlog.get_logger().bind(skill="create_assembly"),
        session_id=uuid4(),
        branch="main",
    )
    handler = CreateAssemblyHandler(ctx)
    return ctx, handler, work_product


class TestCreateAssemblyHandler:
    """Unit tests for CreateAssemblyHandler."""

    async def test_execute_basic(self):
        """Happy path: create a 3-part assembly."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[
                    AssemblyPart(name="base", file="parts/base.step"),
                    AssemblyPart(
                        name="bracket",
                        file="parts/bracket.step",
                        location={"x": 0, "y": 0, "z": 10},
                    ),
                    AssemblyPart(
                        name="cover",
                        file="parts/cover.step",
                        location={"x": 0, "y": 0, "z": 20},
                    ),
                ],
            )
        )

        assert output.assembly_file == "output/assembly.step"
        assert output.part_count == 3
        assert output.total_volume == 45000.0
        assert output.interference_check_passed is True

    async def test_preconditions_duplicate_names(self):
        """Precondition check catches duplicate part names."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[
                    AssemblyPart(name="base", file="parts/base.step"),
                    AssemblyPart(name="base", file="parts/base2.step"),
                ],
            )
        )
        assert any("unique" in e.lower() for e in errors)

    async def test_preconditions_bad_constraint_ref(self):
        """Precondition check catches constraint referencing unknown parts."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[
                    AssemblyPart(name="base", file="parts/base.step"),
                ],
                constraints=[
                    AssemblyConstraint(part_a="base", part_b="missing", type="Plane"),
                ],
            )
        )
        assert any("unknown part" in e.lower() for e in errors)

    async def test_preconditions_missing_tool(self):
        """Precondition check fails when tool is unavailable."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_work_product())

        ctx = SkillContext(
            twin=twin,
            mcp=mcp,
            logger=structlog.get_logger().bind(skill="create_assembly"),
            session_id=uuid4(),
            branch="main",
        )
        handler = CreateAssemblyHandler(ctx)

        errors = await handler.validate_preconditions(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[AssemblyPart(name="base", file="parts/base.step")],
            )
        )
        assert any("not available" in e for e in errors)

    async def test_run_pipeline(self):
        """Full skill pipeline."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        result = await handler.run(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[
                    AssemblyPart(name="base", file="parts/base.step"),
                    AssemblyPart(name="top", file="parts/top.step"),
                ],
            )
        )

        assert result.success is True
        assert result.data is not None
