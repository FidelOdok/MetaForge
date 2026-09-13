"""Tests for the define_system_architecture skill (MET-747 lifecycle-mapping follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_agents.shared.skills.define_system_architecture.handler import (
    DefineSystemArchitectureHandler,
)
from domain_agents.shared.skills.define_system_architecture.schema import (
    ArchComponent,
    ArchInterface,
    DefineSystemArchitectureInput,
    DefineSystemArchitectureOutput,
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
    ctx.domain = "shared"
    return ctx


@pytest.fixture()
def sample_input() -> DefineSystemArchitectureInput:
    return DefineSystemArchitectureInput(
        project_id="proj-1",
        system_name="Drone",
        components=[ArchComponent(name="MCU"), ArchComponent(name="Motor Driver")],
        interfaces=[
            ArchInterface(**{"from": "MCU", "to": "Motor Driver", "interface_type": "SPI"})
        ],
    )


class TestSchemas:
    def test_interface_accepts_from_to_aliases(self) -> None:
        i = ArchInterface(**{"from": "A", "to": "B"})
        assert i.from_component == "A"
        assert i.to_component == "B"

    def test_components_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            DefineSystemArchitectureInput(project_id="p", system_name="s", components=[])


class TestDefineSystemArchitectureHandler:
    async def test_serializes_interfaces_with_from_to_keys(
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
                "interface_count": 1,
                "dangling_interfaces": [],
            },
        )
        handler = DefineSystemArchitectureHandler(mock_context)
        await handler.execute(sample_input)
        _, params = mock_context.mcp.calls[0]
        assert params["interfaces"][0]["from"] == "MCU"
        assert params["interfaces"][0]["to"] == "Motor Driver"

    async def test_dangling_interfaces_surfaced_as_count(
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
                "interface_count": 1,
                "dangling_interfaces": [{"from": "MCU", "to": "Sensor Hub"}],
            },
        )
        handler = DefineSystemArchitectureHandler(mock_context)
        output = await handler.execute(sample_input)
        assert isinstance(output, DefineSystemArchitectureOutput)
        assert output.dangling_interface_count == 1


class TestSkillRunPipeline:
    async def test_full_run_pipeline_success(
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
                "interface_count": 1,
                "dangling_interfaces": [],
            },
        )
        handler = DefineSystemArchitectureHandler(mock_context)
        result = await handler.run(sample_input)
        assert result.success is True
        assert result.data is not None
        assert result.data.node_id == "node-1"
