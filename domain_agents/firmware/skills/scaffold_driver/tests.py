"""Skill-specific tests for scaffold_driver (FORGE-545)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.firmware.skills.scaffold_driver.handler import (
    ScaffoldDriverHandler,
)
from domain_agents.firmware.skills.scaffold_driver.schema import ScaffoldDriverInput
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


class TestScaffoldDriverSkill:
    async def test_registers_come_from_the_input(self, fw_context: SkillContext) -> None:
        out = await ScaffoldDriverHandler(fw_context).execute(
            ScaffoldDriverInput(
                work_product_id=uuid4(),
                peripheral_type="barometer",
                interface="i2c",
                driver_name="bmp280",
                registers=[{"name": "ID", "address": "0xD0", "access": "r", "expected": "0x58"}],
            )
        )
        assert out.register_map == {
            "ID": {"address": "0xD0", "access": "read-only", "expected": "0x58"}
        }
        assert out.driver_files[0] == "firmware/drivers/bmp280/bmp280_regs.h"
