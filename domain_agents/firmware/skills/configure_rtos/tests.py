"""Skill-specific tests for configure_rtos (FORGE-545)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.firmware.skills.configure_rtos.handler import ConfigureRtosHandler
from domain_agents.firmware.skills.configure_rtos.schema import ConfigureRtosInput
from skill_registry.skill_base import SkillContext


@pytest.fixture()
def fw_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = MagicMock()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


class TestConfigureRtosSkill:
    async def test_memory_estimate_is_exact(self, fw_context: SkillContext) -> None:
        out = await ConfigureRtosHandler(fw_context).execute(
            ConfigureRtosInput(
                work_product_id=uuid4(),
                rtos_name="FreeRTOS",
                task_definitions=[{"name": "a", "priority": 1, "stack_size": 512}],
                heap_size_kb=8,
            )
        )
        # 8 KB heap + 512 B stack = 8.5 KB, rounded up; a sub-KB stack used to count as 4 KB
        assert out.memory_estimate_kb == 9
        assert out.config_file == "firmware/rtos/FreeRTOSConfig.h"

    async def test_rtos_without_a_generator_is_refused(self, fw_context: SkillContext) -> None:
        with pytest.raises(ValueError, match="Unsupported RTOS"):
            await ConfigureRtosHandler(fw_context).execute(
                ConfigureRtosInput(
                    work_product_id=uuid4(),
                    rtos_name="ChibiOS",
                    task_definitions=[{"name": "a", "priority": 1, "stack_size": 512}],
                )
            )
