"""Skill-specific tests for decide_sketch_needed.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_decide_sketch_needed.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import DecideSketchNeededHandler
from .schema import DecideSketchNeededInput


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


class TestDecideSketchNeededSkill:
    """Co-located skill tests -- smoke tests for the handler."""

    async def test_simple_new_part_does_not_need_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(part_count=1))

        assert output.sketch_needed is False
        assert output.is_revision is False
        assert output.recommended_source_node_ids == []

    async def test_revision_of_existing_design_needs_a_sketch(
        self, mock_context: SkillContext
    ) -> None:
        source_id = uuid4()
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(
            DecideSketchNeededInput(source_node_ids=[source_id], part_count=1)
        )

        assert output.sketch_needed is True
        assert output.is_revision is True
        assert output.recommended_source_node_ids == [source_id]
        assert any("already-built" in r for r in output.reasons)

    async def test_kinematic_assembly_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(
            DecideSketchNeededInput(part_count=3, has_moving_joints=True)
        )

        assert output.sketch_needed is True
        assert any("moving joints" in r for r in output.reasons)

    async def test_single_part_with_joints_flag_is_not_kinematic(
        self, mock_context: SkillContext
    ) -> None:
        """A single part can't have inter-part joints -- the rule requires
        part_count > 1, so a stray has_moving_joints=True on one part
        shouldn't trigger the kinematic-assembly reason."""
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(
            DecideSketchNeededInput(part_count=1, has_moving_joints=True)
        )

        assert output.sketch_needed is False

    async def test_novel_topology_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(topology_is_novel=True))

        assert output.sketch_needed is True
        assert any("no prior built reference" in r for r in output.reasons)

    async def test_high_part_count_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(part_count=4))

        assert output.sketch_needed is True
        assert any("assembly complexity" in r for r in output.reasons)

    async def test_explicit_request_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(user_requested_review=True))

        assert output.sketch_needed is True
        assert any("explicitly requested" in r for r in output.reasons)

    async def test_multiple_triggered_reasons_all_reported(
        self, mock_context: SkillContext
    ) -> None:
        source_id = uuid4()
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(
            DecideSketchNeededInput(
                source_node_ids=[source_id],
                part_count=5,
                has_moving_joints=True,
                topology_is_novel=True,
                user_requested_review=True,
            )
        )

        assert output.sketch_needed is True
        assert len(output.reasons) == 5
