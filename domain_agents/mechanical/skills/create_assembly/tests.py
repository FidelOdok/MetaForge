"""Unit tests for the create_assembly skill."""

from __future__ import annotations

from uuid import uuid4

import pytest
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
    mcp.register_tool("cadquery.create_assembly", capability="cad_assembly", name="Create Assembly")
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

    def test_part_requires_exactly_one_reference(self):
        """FORGE-85: a part must give exactly one of node_id or file."""
        with pytest.raises(ValueError, match="exactly one"):
            AssemblyPart(name="base")
        with pytest.raises(ValueError, match="exactly one"):
            AssemblyPart(name="base", node_id="n1", file="parts/base.step")

    async def test_work_product_id_is_optional(self):
        """FORGE-85: work_product_id is no longer required, mirroring generate_cad."""
        _ctx, handler, _work_product = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            CreateAssemblyInput(parts=[AssemblyPart(name="base", file="parts/base.step")])
        )
        assert errors == []

        output = await handler.execute(
            CreateAssemblyInput(parts=[AssemblyPart(name="base", file="parts/base.step")])
        )
        assert output.work_product_id is None

    async def test_node_id_part_is_staged_before_assembling(self):
        """FORGE-85: a node_id-referenced part is materialized via
        twin.stage_work_product_file, and the STAGED file path (not the raw
        node_id) is what reaches cadquery.create_assembly."""
        ctx, handler, work_product = await _make_ctx_and_handler()
        ctx.mcp.register_tool("twin.stage_work_product_file", capability="twin_read", name="Stage")
        ctx.mcp.register_tool_response(
            "twin.stage_work_product_file",
            {
                "node_id": "abc-123",
                "file_path": "/workspace/_staged_work_products/abc-123/base.step",
            },
        )

        output = await handler.execute(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[AssemblyPart(name="base", node_id="abc-123")],
            )
        )

        assert output.part_count == 3  # from the mocked cadquery response

    async def test_preconditions_node_id_without_stager(self):
        """A part references node_id but twin.stage_work_product_file isn't
        registered -- fails loudly at precondition time, not mid-execute."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        errors = await handler.validate_preconditions(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[AssemblyPart(name="base", node_id="abc-123")],
            )
        )
        assert any("stage_work_product_file" in e for e in errors)

    async def test_commit_skipped_when_tool_unavailable(self):
        """FORGE-85: default commit=True degrades gracefully when
        twin.commit_geometry isn't registered -- this skill never used to
        attempt a commit at all, so this is new coverage, not a regression."""
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[AssemblyPart(name="base", file="parts/base.step")],
            )
        )

        assert output.committed is False
        assert output.commit_error == "twin.commit_geometry tool is not available"

    async def test_commit_geometry_invoked_when_available(self, tmp_path):
        """FORGE-85: when commit=True and the tool is available, the STEP
        file is read and committed -- mirrors generate_cad's own test."""
        ctx, handler, work_product = await _make_ctx_and_handler()

        step_file = tmp_path / "assembly.step"
        step_file.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
        ctx.mcp.register_tool_response(
            "cadquery.create_assembly", {**ASSEMBLY_RESULT, "assembly_file": str(step_file)}
        )
        ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        ctx.mcp.register_tool_response(
            "twin.commit_geometry",
            {"node_id": "node-789", "model_url": "https://twin.local/models/node-789"},
        )

        output = await handler.execute(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[AssemblyPart(name="base", file="parts/base.step")],
                project_id="13d60463-433b-4735-af07-690cbf8e07b9",
            )
        )

        assert output.committed is True
        assert output.twin_node_id == "node-789"
        assert output.model_url == "https://twin.local/models/node-789"
        assert output.commit_error is None

    async def test_commit_false_skips_persistence(self):
        """FORGE-85: commit=False never attempts to persist."""
        _ctx, handler, work_product = await _make_ctx_and_handler()
        _ctx.mcp.register_tool("twin.commit_geometry", capability="twin_geometry", name="Commit")
        _ctx.mcp.register_tool_response("twin.commit_geometry", {"node_id": "node-789"})

        output = await handler.execute(
            CreateAssemblyInput(
                work_product_id=work_product.id,
                parts=[AssemblyPart(name="base", file="parts/base.step")],
                commit=False,
            )
        )

        assert output.committed is False
        assert output.commit_error is None
