"""Skill-specific tests for design_cad_iterative.

The loop itself is tested in tests/unit/test_cad_design_loop.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import TOOL, DesignCadIterativeHandler
from .schema import DesignCadIterativeInput

PROJECT = "66666666-6666-6666-6666-666666666666"


@pytest.fixture()
def ctx() -> SkillContext:
    context = MagicMock(spec=SkillContext)
    context.twin = AsyncMock()
    context.mcp = InMemoryMcpBridge()
    context.logger = MagicMock()
    context.logger.bind = MagicMock(return_value=context.logger)
    context.session_id = uuid4()
    context.branch = "main"
    return context


class TestDesignCadIterativeSkill:
    async def test_it_starts_the_loop_through_mcp(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool(TOOL, "cad_generation")
        ctx.mcp.register_tool_response(
            TOOL, {"job_id": "job-1", "status": "running", "poll": "twin.job_status"}
        )
        out = await DesignCadIterativeHandler(ctx).execute(
            DesignCadIterativeInput(project_id=PROJECT, sketch_key="PSK-BASE")
        )
        assert out.job_id == "job-1" and out.status == "running"
        assert "never validates" in out.note

    async def test_a_finished_run_reports_its_stop_reason(self, ctx: SkillContext) -> None:
        ctx.mcp.register_tool(TOOL, "cad_generation")
        ctx.mcp.register_tool_response(
            TOOL,
            {
                "node_id": "n1",
                "stop_reason": "needs_input",
                "stop_detail": "How thick?",
                "iterations": [{"n": 1}],
                "questions": [{"name": "f.depth", "question": "How thick?"}],
                "result": None,
            },
        )
        out = await DesignCadIterativeHandler(ctx).execute(
            DesignCadIterativeInput(project_id=PROJECT, component_id="CMP-base", wait=True)
        )
        assert out.stop_reason == "needs_input" and out.iterations == 1
        assert out.questions[0]["name"] == "f.depth" and out.result is None

    async def test_preconditions_catch_a_missing_tool(self, ctx: SkillContext) -> None:
        errors = await DesignCadIterativeHandler(ctx).validate_preconditions(
            DesignCadIterativeInput(project_id=PROJECT, sketch_key="PSK-BASE")
        )
        assert errors and TOOL in errors[0]

    def test_a_component_must_be_named(self) -> None:
        with pytest.raises(ValidationError):
            DesignCadIterativeInput(project_id=PROJECT)
