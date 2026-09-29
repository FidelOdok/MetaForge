"""Unit tests for the generate_parametric_feature skill (FORGE-269)."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
import structlog

from domain_agents.shared.design_ir_macros import bolt_pattern_entities, rib_entities
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from .handler import GenerateParametricFeatureHandler
from .schema import GenerateParametricFeatureInput


class TestBoltPatternEntities:
    def test_produces_six_entities_in_order(self) -> None:
        entities = bolt_pattern_entities(
            plate_length_mm=60.0,
            plate_width_mm=40.0,
            plate_thickness_mm=5.0,
            hole_diameter_mm=4.0,
            hole_count=4,
            pattern_radius_mm=15.0,
        )
        assert [e["op"] for e in entities] == [
            "create_body",
            "sketch",
            "pad",
            "sketch",
            "pocket",
            "polar_pattern",
        ]
        assert entities[-1]["source_ref"] == "hole1"
        assert entities[-1]["count"] == 4

    def test_hole_pocket_is_reversed_to_actually_cut_the_plate(self) -> None:
        # Regression test for a real bug, confirmed live against a real
        # FreeCAD adapter (fidel-dev): reversed=False (PocketEntity's own
        # default) cuts AWAY from the plate's own pad direction -- into
        # empty space below the plate, removing no material at all. Live
        # measurement: pad alone -> 10000.0mm^3, pocket reversed=False ->
        # UNCHANGED 10000.0mm^3, pocket reversed=True -> 9971.73mm^3
        # (exactly one hole's volume removed, as expected).
        entities = bolt_pattern_entities(
            plate_length_mm=60.0,
            plate_width_mm=40.0,
            plate_thickness_mm=5.0,
            hole_diameter_mm=4.0,
            hole_count=4,
            pattern_radius_mm=15.0,
        )
        pocket = next(e for e in entities if e["op"] == "pocket")
        assert pocket["reversed"] is True

    def test_rejects_non_positive_dimensions(self) -> None:
        with pytest.raises(ValueError, match="plate dimensions"):
            bolt_pattern_entities(
                plate_length_mm=0.0,
                plate_width_mm=40.0,
                plate_thickness_mm=5.0,
                hole_diameter_mm=4.0,
                hole_count=4,
                pattern_radius_mm=15.0,
            )

    def test_rejects_too_few_holes(self) -> None:
        with pytest.raises(ValueError, match="hole_count"):
            bolt_pattern_entities(
                plate_length_mm=60.0,
                plate_width_mm=40.0,
                plate_thickness_mm=5.0,
                hole_diameter_mm=4.0,
                hole_count=1,
                pattern_radius_mm=15.0,
            )

    def test_rejects_pattern_radius_outside_plate(self) -> None:
        with pytest.raises(ValueError, match="plate material"):
            bolt_pattern_entities(
                plate_length_mm=20.0,
                plate_width_mm=20.0,
                plate_thickness_mm=5.0,
                hole_diameter_mm=4.0,
                hole_count=4,
                pattern_radius_mm=15.0,  # leaves no material at the plate edge
            )

    def test_rejects_overlapping_holes(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            bolt_pattern_entities(
                plate_length_mm=200.0,
                plate_width_mm=200.0,
                plate_thickness_mm=5.0,
                hole_diameter_mm=50.0,  # huge holes, tiny radius -> guaranteed overlap
                hole_count=8,
                pattern_radius_mm=10.0,
            )


class TestRibEntities:
    def test_produces_three_entities(self) -> None:
        entities = rib_entities(length_mm=30.0, height_mm=20.0, thickness_mm=3.0)
        assert [e["op"] for e in entities] == ["create_body", "sketch", "pad"]
        assert entities[-1]["depth"] == 3.0
        assert entities[-1]["midplane"] is True

    def test_triangle_is_closed(self) -> None:
        entities = rib_entities(length_mm=30.0, height_mm=20.0, thickness_mm=3.0)
        elements = entities[1]["elements"]
        assert len(elements) == 3
        assert elements[0]["start"] == [0.0, 0.0]
        assert elements[-1]["end"] == [0.0, 0.0]

    def test_rejects_non_positive_dimensions(self) -> None:
        with pytest.raises(ValueError, match="length_mm/height_mm"):
            rib_entities(length_mm=0.0, height_mm=20.0, thickness_mm=3.0)
        with pytest.raises(ValueError, match="thickness_mm"):
            rib_entities(length_mm=30.0, height_mm=20.0, thickness_mm=0.0)


def _make_work_product() -> WorkProduct:
    return WorkProduct(
        name="test-feature-model",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="models/test_feature.step",
        content_hash="sha256:testfeat",
        format="step",
        created_by="human",
        metadata={},
    )


def _register_freecad_session_tools(mcp: InMemoryMcpBridge) -> None:
    mcp.register_tool("freecad.open_session", capability="cad_session")
    mcp.register_tool_response("freecad.open_session", {"session_id": "sess-1"})
    mcp.register_tool("freecad.close_session", capability="cad_session")
    mcp.register_tool_response("freecad.close_session", {})
    for tool_id in (
        "freecad.create_body",
        "freecad.create_sketch",
        "freecad.pad_sketch",
        "freecad.pocket_sketch",
        "freecad.polar_pattern",
    ):
        mcp.register_tool(tool_id, capability="cad_author")
        mcp.register_tool_response(tool_id, {"obj_id": f"{tool_id.split('.')[1]}_1"})
    mcp.register_tool("freecad.measure", capability="cad_inspect")
    mcp.register_tool_response(
        "freecad.measure",
        {
            "volume_mm3": 6000.0,
            "surface_area_mm2": 2200.0,
            "bounding_box": {
                "min_x": -30.0,
                "min_y": -20.0,
                "min_z": 0.0,
                "max_x": 30.0,
                "max_y": 20.0,
                "max_z": 5.0,
            },
        },
    )
    mcp.register_tool("freecad.export_model", capability="cad_export")
    mcp.register_tool_response(
        "freecad.export_model",
        {"step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii")},
    )


async def _make_ctx_and_handler() -> tuple[
    SkillContext, GenerateParametricFeatureHandler, WorkProduct
]:
    twin = InMemoryTwinAPI.create()
    mcp = InMemoryMcpBridge()
    _register_freecad_session_tools(mcp)

    work_product = await twin.create_work_product(_make_work_product())

    ctx = SkillContext(
        twin=twin,
        mcp=mcp,
        logger=structlog.get_logger().bind(skill="generate_parametric_feature"),
        session_id=uuid4(),
        branch="main",
    )
    handler = GenerateParametricFeatureHandler(ctx)
    return ctx, handler, work_product


class TestGenerateParametricFeatureHandler:
    async def test_execute_bolt_pattern(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateParametricFeatureInput(
                name="Test Bolt Pattern",
                work_product_id=work_product.id,
                feature={
                    "feature_type": "bolt_pattern",
                    "plate_length_mm": 60.0,
                    "plate_width_mm": 40.0,
                    "plate_thickness_mm": 5.0,
                    "hole_diameter_mm": 4.0,
                    "hole_count": 4,
                    "pattern_radius_mm": 15.0,
                },
            )
        )

        assert output.feature_type == "bolt_pattern"
        assert output.entity_count == 6
        assert output.volume_mm3 == 6000.0
        assert output.cad_file

    async def test_execute_rib(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _ctx, handler, work_product = await _make_ctx_and_handler()

        output = await handler.execute(
            GenerateParametricFeatureInput(
                name="Test Rib",
                work_product_id=work_product.id,
                feature={
                    "feature_type": "rib",
                    "length_mm": 30.0,
                    "height_mm": 20.0,
                    "thickness_mm": 3.0,
                },
            )
        )

        assert output.feature_type == "rib"
        assert output.entity_count == 3

    async def test_invalid_feature_params_rejected_before_any_mcp_call(self) -> None:
        ctx, handler, work_product = await _make_ctx_and_handler()

        with pytest.raises(ValueError, match="Invalid bolt_pattern parameters"):
            await handler.execute(
                GenerateParametricFeatureInput(
                    name="Bad Bolt Pattern",
                    work_product_id=work_product.id,
                    feature={
                        "feature_type": "bolt_pattern",
                        "plate_length_mm": 20.0,
                        "plate_width_mm": 20.0,
                        "plate_thickness_mm": 5.0,
                        "hole_diameter_mm": 4.0,
                        "hole_count": 4,
                        "pattern_radius_mm": 15.0,
                    },
                )
            )
        assert ctx.mcp.calls == []

    async def test_missing_work_product_rejected_by_preconditions(self) -> None:
        _ctx, handler, _work_product = await _make_ctx_and_handler()
        input_data = GenerateParametricFeatureInput(
            name="Test Rib",
            work_product_id=uuid4(),
            feature={
                "feature_type": "rib",
                "length_mm": 30.0,
                "height_mm": 20.0,
                "thickness_mm": 3.0,
            },
        )
        errors = await handler.validate_preconditions(input_data)
        assert any("not found in Twin" in e for e in errors)

    async def test_run_full_pipeline(self, tmp_path, monkeypatch) -> None:
        """Exercise SkillBase.run() (validate -> preconditions -> execute ->
        validate_output), not just execute() directly."""
        monkeypatch.chdir(tmp_path)
        _ctx, handler, work_product = await _make_ctx_and_handler()

        result = await handler.run(
            GenerateParametricFeatureInput(
                name="Test Rib",
                work_product_id=work_product.id,
                feature={
                    "feature_type": "rib",
                    "length_mm": 30.0,
                    "height_mm": 20.0,
                    "thickness_mm": 3.0,
                },
            )
        )
        assert result.success is True
        assert result.errors == []
