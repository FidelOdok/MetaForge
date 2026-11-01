"""Skill-specific tests for generate_hal (FORGE-545)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.firmware.skills.generate_hal.handler import GenerateHalHandler
from domain_agents.firmware.skills.generate_hal.schema import GenerateHalInput
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


class TestGenerateHalSkill:
    async def test_pins_come_from_the_pin_map(self, fw_context: SkillContext) -> None:
        out = await GenerateHalHandler(fw_context).execute(
            GenerateHalInput(
                work_product_id=uuid4(),
                mcu_family="RP2040",
                pin_map=[{"signal": "LED", "pin": "GP25"}],
            )
        )
        assert "#define BOARD_LED_PIN 25" in out.files[0].content

    async def test_requested_peripheral_without_pins_is_refused(
        self, fw_context: SkillContext
    ) -> None:
        with pytest.raises(ValueError, match="no pins"):
            await GenerateHalHandler(fw_context).execute(
                GenerateHalInput(
                    work_product_id=uuid4(),
                    mcu_family="RP2040",
                    pin_map=[{"signal": "LED", "pin": "GP25"}],
                    peripherals=["SPI0"],
                )
            )
