"""Tests for the decide_sketch_needed skill (follow-up to MET-740/747)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.mechanical.skills.decide_sketch_needed.handler import (
    DecideSketchNeededHandler,
)
from domain_agents.mechanical.skills.decide_sketch_needed.schema import (
    DecideSketchNeededInput,
    DecideSketchNeededOutput,
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


# ---------------------------------------------------------------------------
# TestSchemas
# ---------------------------------------------------------------------------


class TestDecideSketchNeededSchemas:
    def test_valid_input_defaults(self) -> None:
        inp = DecideSketchNeededInput()
        assert inp.source_node_ids == []
        assert inp.part_count == 1
        assert inp.has_moving_joints is False
        assert inp.topology_is_novel is False
        assert inp.user_requested_review is False

    def test_part_count_must_be_at_least_one(self) -> None:
        with pytest.raises(ValidationError):
            DecideSketchNeededInput(part_count=0)

    def test_output_requires_at_least_one_reason(self) -> None:
        with pytest.raises(ValidationError):
            DecideSketchNeededOutput(
                sketch_needed=False,
                reasons=[],
                is_revision=False,
                recommended_source_node_ids=[],
            )


# ---------------------------------------------------------------------------
# TestDecideSketchNeededHandler
# ---------------------------------------------------------------------------


class TestDecideSketchNeededHandler:
    async def test_simple_new_single_part_passes_the_gate(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(part_count=1))

        assert output.sketch_needed is False
        assert output.is_revision is False
        assert output.recommended_source_node_ids == []
        assert len(output.reasons) == 1

    async def test_revision_of_existing_design_needs_a_sketch(
        self, mock_context: SkillContext
    ) -> None:
        source_a, source_b = uuid4(), uuid4()
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(
            DecideSketchNeededInput(source_node_ids=[source_a, source_b], part_count=1)
        )

        assert output.sketch_needed is True
        assert output.is_revision is True
        assert output.recommended_source_node_ids == [source_a, source_b]
        assert any("already-built work product(s)" in r for r in output.reasons)

    async def test_kinematic_assembly_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(
            DecideSketchNeededInput(part_count=3, has_moving_joints=True)
        )

        assert output.sketch_needed is True
        assert any("moving joints" in r for r in output.reasons)

    async def test_single_part_cannot_trigger_kinematic_rule(
        self, mock_context: SkillContext
    ) -> None:
        """part_count=1 with has_moving_joints=True is a malformed signal (a
        single part has no inter-part joints) -- the rule requires
        part_count > 1 and should not fire on this input alone."""
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

    async def test_part_count_at_threshold_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(part_count=4))

        assert output.sketch_needed is True
        assert any("assembly complexity" in r for r in output.reasons)

    async def test_part_count_below_threshold_alone_does_not_need_a_sketch(
        self, mock_context: SkillContext
    ) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(part_count=3))

        assert output.sketch_needed is False

    async def test_explicit_request_needs_a_sketch(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(user_requested_review=True))

        assert output.sketch_needed is True
        assert any("explicitly requested" in r for r in output.reasons)

    async def test_all_rules_can_fire_together(self, mock_context: SkillContext) -> None:
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

    async def test_recommended_source_node_ids_empty_when_sketch_not_needed(
        self, mock_context: SkillContext
    ) -> None:
        """Even if source_node_ids were somehow passed alongside signals that
        don't independently trigger the gate, is_revision being true already
        forces sketch_needed -- so recommended_source_node_ids is only ever
        empty on the genuine pass-through path."""
        handler = DecideSketchNeededHandler(mock_context)
        output = await handler.execute(DecideSketchNeededInput(part_count=2))

        assert output.sketch_needed is False
        assert output.recommended_source_node_ids == []


# ---------------------------------------------------------------------------
# TestSkillRunPipeline
# ---------------------------------------------------------------------------


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(self, mock_context: SkillContext) -> None:
        handler = DecideSketchNeededHandler(mock_context)
        result = await handler.run(DecideSketchNeededInput(part_count=1))

        assert result.success is True
        assert result.data is not None
        assert isinstance(result.data, DecideSketchNeededOutput)
        assert result.data.sketch_needed is False
        assert result.duration_ms >= 0
        assert result.errors == []

    async def test_full_run_pipeline_with_dict_input(self, mock_context: SkillContext) -> None:
        """run() coerces a plain dict into the input model."""
        handler = DecideSketchNeededHandler(mock_context)
        result = await handler.run({"part_count": 4})  # type: ignore[arg-type]

        assert result.success is True
        assert result.data is not None
        assert result.data.sketch_needed is True
