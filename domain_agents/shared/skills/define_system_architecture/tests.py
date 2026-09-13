"""Skill-specific tests for define_system_architecture.

These tests live alongside the skill for co-location. The main test suite
is at tests/unit/test_define_system_architecture.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext

from .handler import DefineSystemArchitectureHandler
from .schema import ArchComponent, DefineSystemArchitectureInput


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
def sample_input() -> DefineSystemArchitectureInput:
    return DefineSystemArchitectureInput(
        project_id="proj-1",
        system_name="Drone",
        components=[ArchComponent(name="MCU"), ArchComponent(name="Motor Driver")],
    )


class TestDefineSystemArchitectureSkill:
    async def test_execute_returns_output(
        self, mock_context: SkillContext, sample_input: DefineSystemArchitectureInput
    ) -> None:
        mock_context.mcp.register_tool(
            "twin.commit_system_architecture", "twin_system_architecture"
        )
        mock_context.mcp.register_tool_response(
            "twin.commit_system_architecture",
            {
                "node_id": "node-1",
                "component_count": 2,
                "interface_count": 0,
                "dangling_interfaces": [],
            },
        )
        handler = DefineSystemArchitectureHandler(mock_context)
        output = await handler.execute(sample_input)
        assert output.node_id == "node-1"
        assert output.dangling_interface_count == 0

    async def test_preconditions_catch_missing_tool(
        self, mock_context: SkillContext, sample_input: DefineSystemArchitectureInput
    ) -> None:
        handler = DefineSystemArchitectureHandler(mock_context)
        errors = await handler.validate_preconditions(sample_input)
        assert any("not available" in e for e in errors)
