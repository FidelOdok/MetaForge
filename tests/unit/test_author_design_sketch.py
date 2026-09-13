"""Tests for the author_design_sketch skill (MET-747 follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.mechanical.skills.author_design_sketch.handler import (
    AuthorDesignSketchHandler,
    _bar_row,
    _leading_number,
    _render_html,
)
from domain_agents.mechanical.skills.author_design_sketch.schema import (
    AuthorDesignSketchInput,
    AuthorDesignSketchOutput,
    ProposedChange,
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
def sample_input() -> AuthorDesignSketchInput:
    return AuthorDesignSketchInput(
        name="Leg Revision Sketch",
        subject_name="Quadruped Leg Front-Left",
        summary="Checking thigh/shin proportions before CAD",
        proposed_changes=[
            ProposedChange(
                feature="Thigh length", before="50 mm", after="60 mm", rationale="More lift"
            ),
            ProposedChange(feature="Guard", before="none", after="added guard"),
        ],
        source_node_ids=["aaaaaaaa-0000-0000-0000-000000000001"],
    )


class TestSchemas:
    def test_proposed_changes_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            AuthorDesignSketchInput(name="x", subject_name="x", proposed_changes=[])


class TestNumericHelpers:
    def test_leading_number_parses_unit_suffix(self) -> None:
        assert _leading_number("50 mm") == 50.0
        assert _leading_number("-3.5kg") == -3.5

    def test_leading_number_none_when_no_number(self) -> None:
        assert _leading_number("added guard") is None

    def test_bar_row_present_for_numeric_pair(self) -> None:
        bar = _bar_row("50 mm", "60 mm")
        assert "bar before" in bar
        assert "bar after" in bar

    def test_bar_row_empty_for_non_numeric_pair(self) -> None:
        assert _bar_row("none", "added guard") == ""


class TestRenderHtml:
    def test_render_includes_table_and_escapes_input(self) -> None:
        changes = [ProposedChange(feature="<b>x</b>", before="1", after="2")]
        html = _render_html("Name", "Subject", "Summary", changes)
        assert "<table>" in html
        assert "&lt;b&gt;x&lt;/b&gt;" in html
        assert "<b>x</b>" not in html.split("<table>")[1]

    def test_render_has_no_external_assets(self) -> None:
        html = _render_html("N", "S", "Sum", [ProposedChange(feature="f", before="1", after="2")])
        assert "http://" not in html
        assert "https://" not in html
        assert "<link" not in html
        assert "<script src" not in html


class TestAuthorDesignSketchHandler:
    async def test_invokes_commit_tool_with_rendered_html(
        self, mock_context: SkillContext, sample_input: AuthorDesignSketchInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        handler = AuthorDesignSketchHandler(mock_context)
        output = await handler.execute(sample_input)
        assert isinstance(output, AuthorDesignSketchOutput)
        assert output.change_count == 2
        _, params = mock_context.mcp.calls[0]
        assert "Thigh length" in params["html_content"]
        assert params["source_node_ids"] == ["aaaaaaaa-0000-0000-0000-000000000001"]
        assert params["source_tool"] == "mechanical.author_design_sketch"

    async def test_empty_source_node_ids_becomes_none(self, mock_context: SkillContext) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        inp = AuthorDesignSketchInput(
            name="New Design Sketch",
            subject_name="New Part",
            proposed_changes=[ProposedChange(feature="f", before="1", after="2")],
        )
        handler = AuthorDesignSketchHandler(mock_context)
        await handler.execute(inp)
        _, params = mock_context.mcp.calls[0]
        assert params["source_node_ids"] is None


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
        self, mock_context: SkillContext, sample_input: AuthorDesignSketchInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        handler = AuthorDesignSketchHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is True
        assert result.data is not None
        assert result.data.node_id == "node-1"

    async def test_full_run_pipeline_precondition_failure(
        self, mock_context: SkillContext, sample_input: AuthorDesignSketchInput
    ) -> None:
        handler = AuthorDesignSketchHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is False
        assert result.data is None
