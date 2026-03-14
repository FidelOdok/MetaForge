"""Skill-specific tests for validate_stress.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_validate_stress.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import ValidateStressHandler
from .schema import StressConstraint, ValidateStressInput


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
def sample_input() -> ValidateStressInput:
    return ValidateStressInput(
        work_product_id=uuid4(),
        mesh_file_path="/project/mesh/bracket.inp",
        load_case="static_load_1",
        constraints=[
            StressConstraint(
                max_von_mises_mpa=250.0,
                safety_factor=1.5,
                material="aluminum_6061",
            )
        ],
    )


class TestValidateStressSkill:
    """Co-located skill tests — smoke tests for the handler."""

    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = {"id": str(sample_input.work_product_id)}
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")
        mock_context.mcp.register_tool_response(
            "calculix.run_fea",
            {
                "max_von_mises": {"bracket_body": 100.0},
                "solver_time": 5.0,
                "mesh_elements": 25000,
            },
        )

        handler = ValidateStressHandler(mock_context)
        output = await handler.execute(sample_input)

        assert output.overall_passed is True
        assert output.work_product_id == sample_input.work_product_id

    async def test_preconditions_catch_missing_artifact(
        self, mock_context: SkillContext, sample_input: ValidateStressInput
    ) -> None:
        mock_context.twin.get_work_product.return_value = None
        mock_context.mcp.register_tool("calculix.run_fea", "stress_analysis")

        handler = ValidateStressHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)

        assert any("not found" in e for e in errors)
