"""Tests for the author_design_sketch skill (MET-747 follow-up).

Covers the v2 rewrite: the skill draws an actual scaled 2D kinematic-chain
diagram (segments end-to-end, proportioned from real mm values) instead of
just tabulating before/after numbers -- verified live against the real
Quadruped Robot project that a table-only v1 produced unstyled prose while
this version produces a real recognizable leg diagram.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.mechanical.skills.author_design_sketch.handler import (
    AuthorDesignSketchHandler,
    _chain,
    _dimension_rows,
    _fit_scale,
    _render_html,
)
from domain_agents.mechanical.skills.author_design_sketch.schema import (
    AuthorDesignSketchInput,
    AuthorDesignSketchOutput,
    Segment,
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
def revision_input() -> AuthorDesignSketchInput:
    return AuthorDesignSketchInput(
        name="Leg Revision Sketch",
        subject_name="Quadruped Leg Front-Left",
        summary="Checking thigh/shin proportions before CAD",
        before_segments=[
            Segment(name="Thigh", length_mm=50, thickness_mm=8),
            Segment(name="Shin", length_mm=50, thickness_mm=8, joint_angle_deg=35),
        ],
        after_segments=[
            Segment(name="Thigh", length_mm=60, thickness_mm=8),
            Segment(name="Shin", length_mm=50, thickness_mm=7, joint_angle_deg=35),
        ],
        change_notes=["Increase thigh length for lift"],
        source_node_ids=["aaaaaaaa-0000-0000-0000-000000000001"],
    )


class TestSchemas:
    def test_after_segments_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            AuthorDesignSketchInput(name="x", subject_name="x", after_segments=[])

    def test_segment_length_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            Segment(name="x", length_mm=0, thickness_mm=5)

    def test_before_segments_defaults_empty(self) -> None:
        inp = AuthorDesignSketchInput(
            name="x",
            subject_name="x",
            after_segments=[Segment(name="A", length_mm=1, thickness_mm=1)],
        )
        assert inp.before_segments == []


class TestChainGeometry:
    def test_straight_chain_extends_in_one_direction(self) -> None:
        chain = _chain([Segment(name="A", length_mm=50, thickness_mm=8, joint_angle_deg=0)])
        _seg, x0, y0, x1, y1 = chain[0]
        assert (x0, y0) == (0.0, 0.0)
        # Starts pointing "up" (-90 deg): x unchanged, y decreases.
        assert abs(x1 - x0) < 1e-6
        assert y1 < y0

    def test_second_segment_continues_from_first_segments_end(self) -> None:
        chain = _chain(
            [
                Segment(name="A", length_mm=50, thickness_mm=8),
                Segment(name="B", length_mm=30, thickness_mm=8, joint_angle_deg=90),
            ]
        )
        _seg_a, _, _, ax1, ay1 = chain[0]
        _seg_b, bx0, by0, _bx1, _by1 = chain[1]
        assert (bx0, by0) == (ax1, ay1)

    def test_longer_segment_travels_further(self) -> None:
        short_chain = _chain([Segment(name="A", length_mm=30, thickness_mm=8)])
        long_chain = _chain([Segment(name="A", length_mm=60, thickness_mm=8)])
        _, _, _, _, short_y1 = short_chain[0]
        _, _, _, _, long_y1 = long_chain[0]
        assert abs(long_y1) > abs(short_y1)


class TestFitScale:
    def test_scale_shrinks_for_larger_spans(self) -> None:
        small = _chain([Segment(name="A", length_mm=10, thickness_mm=2)])
        large = _chain([Segment(name="A", length_mm=1000, thickness_mm=2)])
        small_scale, _, _ = _fit_scale([small])
        large_scale, _, _ = _fit_scale([large])
        assert large_scale < small_scale

    def test_shared_scale_across_before_and_after(self) -> None:
        before = _chain([Segment(name="A", length_mm=50, thickness_mm=8)])
        after = _chain([Segment(name="A", length_mm=200, thickness_mm=8)])
        scale, _, _ = _fit_scale([before, after])
        # Fitted to the larger (after) chain's span, not the smaller one.
        _, _, _, _, after_y1 = after[0]
        assert scale * abs(after_y1) <= 260  # fits within the SVG viewBox


class TestDimensionRows:
    def test_matches_before_after_by_name(self) -> None:
        before = [Segment(name="Thigh", length_mm=50, thickness_mm=8)]
        after = [Segment(name="Thigh", length_mm=60, thickness_mm=8)]
        rows = _dimension_rows(before, after)
        assert rows == [("Thigh", "50 mm", "60 mm", "8 mm", "8 mm")]

    def test_new_segment_with_no_prior_shows_em_dash(self) -> None:
        rows = _dimension_rows([], [Segment(name="Foot", length_mm=20, thickness_mm=6)])
        assert rows == [("Foot", "—", "20 mm", "—", "6 mm")]


class TestRenderHtml:
    def test_revision_renders_before_and_after_diagrams(self) -> None:
        html = _render_html(
            "Name",
            "Subject",
            "Summary",
            [Segment(name="A", length_mm=50, thickness_mm=8)],
            [Segment(name="A", length_mm=60, thickness_mm=8)],
            [],
        )
        assert html.count("<svg") == 2
        assert ">Before<" in html
        assert ">After<" in html

    def test_brand_new_design_renders_single_proposed_diagram(self) -> None:
        html = _render_html(
            "Name", "Subject", "Summary", [], [Segment(name="A", length_mm=50, thickness_mm=8)], []
        )
        assert html.count("<svg") == 1
        assert ">Proposed<" in html
        assert ">Before<" not in html

    def test_escapes_segment_names(self) -> None:
        html = _render_html(
            "Name",
            "Subject",
            "Summary",
            [],
            [Segment(name="<b>x</b>", length_mm=1, thickness_mm=1)],
            [],
        )
        assert "&lt;b&gt;x&lt;/b&gt;" in html
        assert "<b>x</b>" not in html.split("<style>")[1].split("</style>")[1]

    def test_change_notes_rendered_as_list_items(self) -> None:
        html = _render_html(
            "Name",
            "Subject",
            "Summary",
            [],
            [Segment(name="A", length_mm=1, thickness_mm=1)],
            ["Reduce weight", "Improve lift"],
        )
        assert "<li>Reduce weight</li>" in html
        assert "<li>Improve lift</li>" in html

    def test_no_external_assets(self) -> None:
        html = _render_html(
            "N", "S", "Sum", [], [Segment(name="f", length_mm=1, thickness_mm=1)], []
        )
        assert "http://" not in html
        assert "https://" not in html
        assert "<link" not in html
        assert "<script" not in html


class TestAuthorDesignSketchHandler:
    async def test_invokes_commit_tool_with_rendered_html(
        self, mock_context: SkillContext, revision_input: AuthorDesignSketchInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        handler = AuthorDesignSketchHandler(mock_context)
        output = await handler.execute(revision_input)
        assert isinstance(output, AuthorDesignSketchOutput)
        assert output.segment_count == 2
        assert output.is_revision is True
        _, params = mock_context.mcp.calls[0]
        assert "<svg" in params["html_content"]
        assert params["source_node_ids"] == ["aaaaaaaa-0000-0000-0000-000000000001"]
        assert params["source_tool"] == "mechanical.author_design_sketch"

    async def test_brand_new_design_is_revision_false(self, mock_context: SkillContext) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        inp = AuthorDesignSketchInput(
            name="New Design Sketch",
            subject_name="New Part",
            after_segments=[Segment(name="A", length_mm=1, thickness_mm=1)],
        )
        handler = AuthorDesignSketchHandler(mock_context)
        output = await handler.execute(inp)
        assert output.is_revision is False


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
        self, mock_context: SkillContext, revision_input: AuthorDesignSketchInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        handler = AuthorDesignSketchHandler(mock_context)
        result = await handler.run(revision_input)
        assert result.success is True
        assert result.data is not None
        assert result.data.node_id == "node-1"

    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, revision_input: AuthorDesignSketchInput
    ) -> None:
        handler = AuthorDesignSketchHandler(mock_context)
        result = await handler.run(revision_input)
        assert result.success is False
        assert result.data is None
