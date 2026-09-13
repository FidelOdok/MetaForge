"""Skill-specific tests for author_design_sketch.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_author_design_sketch.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import AuthorDesignSketchHandler
from .schema import AuthorDesignSketchInput, ProposedChange


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture()
def sample_input() -> AuthorDesignSketchInput:
    return AuthorDesignSketchInput(
        name="Leg Revision Sketch",
        subject_name="Quadruped Leg Front-Left",
        summary="Checking thigh/shin proportions before CAD",
        proposed_changes=[
            ProposedChange(feature="Thigh length", before="50 mm", after="60 mm"),
        ],
    )


class TestAuthorDesignSketchSkill:
    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: AuthorDesignSketchInput
    ) -> None:
        mock_context.mcp.register_tool("twin.commit_design_sketch", "twin_design_sketch")
        mock_context.mcp.register_tool_response("twin.commit_design_sketch", {"node_id": "node-1"})
        handler = AuthorDesignSketchHandler(mock_context)
        output = await handler.execute(sample_input)
        assert output.node_id == "node-1"
        assert output.approved is False

    async def test_preconditions_catch_missing_tool(
        self, mock_context: SkillContext, sample_input: AuthorDesignSketchInput
    ) -> None:
        handler = AuthorDesignSketchHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not available" in e for e in errors)
