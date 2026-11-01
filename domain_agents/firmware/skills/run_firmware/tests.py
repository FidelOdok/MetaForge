"""Skill-specific tests for run_firmware (FORGE-674/677)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from domain_agents.firmware.codegen import GeneratedFile
from domain_agents.firmware.skills.run_firmware.handler import RunFirmwareHandler
from domain_agents.firmware.skills.run_firmware.schema import RunFirmwareInput
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.skill_base import SkillContext


@pytest.fixture()
def fw_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.mcp = InMemoryMcpBridge()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


def _built(verdict: str = "pass") -> dict:
    return {
        "verdict": verdict,
        "error_count": 0 if verdict == "pass" else 1,
        "warning_count": 0,
        "diagnostics": []
        if verdict == "pass"
        else [{"file": "src/main.c", "line": 5, "severity": "error", "message": "x"}],
        "flash": {"used_bytes": 3644, "total_bytes": 1048576, "used_pct": 0.35},
        "ram": {"used_bytes": 16672, "total_bytes": 131072, "used_pct": 12.72},
        "elf_path": "firmware/abc/project/.pio/build/disco/firmware.elf"
        if verdict == "pass"
        else None,
        "board": "disco_f407vg",
        "toolchain": {"platform": "ST STM32", "platform_version": "20.0.0"},
    }


class TestRunFirmwareSkill:
    async def test_build_then_emulate(self, fw_context: SkillContext) -> None:
        fw_context.mcp.register_tool("firmware.build", "firmware_build")
        fw_context.mcp.register_tool("firmware.run_emulated", "firmware_emulation")
        fw_context.mcp.register_tool_response("firmware.build", _built())
        fw_context.mcp.register_tool_response(
            "firmware.run_emulated",
            {
                "verdict": "pass",
                "emulator": "renode",
                "assertions": [{"kind": "uart_expect", "pattern": "boot", "status": "pass"}],
                "uart_log": "MetaForge boot\n",
            },
        )
        out = await RunFirmwareHandler(fw_context).execute(
            RunFirmwareInput(work_product_id=uuid4(), project_path="fw", uart_expect=["boot"])
        )
        assert out.build_verdict == "pass" and out.flash["used_bytes"] == 3644
        assert out.emulation_verdict == "pass" and out.emulator == "renode"

    async def test_a_failed_build_is_not_emulated(self, fw_context: SkillContext) -> None:
        fw_context.mcp.register_tool("firmware.build", "firmware_build")
        fw_context.mcp.register_tool("firmware.run_emulated", "firmware_emulation")
        fw_context.mcp.register_tool_response("firmware.build", _built("fail"))
        out = await RunFirmwareHandler(fw_context).execute(
            RunFirmwareInput(
                work_product_id=uuid4(),
                files=[GeneratedFile(path="src/main.c", content="x")],
                board="disco_f407vg",
                framework="stm32cube",
                uart_expect=["boot"],
            )
        )
        assert out.build_verdict == "fail" and out.error_count == 1
        assert out.emulation_verdict is None

    async def test_build_only_without_checks(self, fw_context: SkillContext) -> None:
        fw_context.mcp.register_tool("firmware.build", "firmware_build")
        fw_context.mcp.register_tool_response("firmware.build", _built())
        out = await RunFirmwareHandler(fw_context).execute(
            RunFirmwareInput(work_product_id=uuid4(), project_path="fw")
        )
        assert out.build_verdict == "pass" and out.emulation_verdict is None

    async def test_precondition_names_the_missing_adapter(self, fw_context: SkillContext) -> None:
        errors = await RunFirmwareHandler(fw_context).validate_preconditions(
            RunFirmwareInput(work_product_id=uuid4(), project_path="fw")
        )
        assert any("firmware adapter is not running" in e for e in errors)

    async def test_static_check_alongside_the_build(self, fw_context: SkillContext) -> None:
        fw_context.mcp.register_tool("firmware.build", "firmware_build")
        fw_context.mcp.register_tool("firmware.static_check", "firmware_static_analysis")
        fw_context.mcp.register_tool_response("firmware.build", _built())
        fw_context.mcp.register_tool_response(
            "firmware.static_check",
            {
                "verdict": "fail",
                "counts": {"high": 1, "medium": 0, "low": 2},
                "findings": [{"severity": "high", "id": "nullPointer", "file": "src/main.c"}],
            },
        )
        out = await RunFirmwareHandler(fw_context).execute(
            RunFirmwareInput(work_product_id=uuid4(), project_path="fw", static_check=True)
        )
        assert out.build_verdict == "pass"
        assert out.check_verdict == "fail" and out.check_counts["high"] == 1

    def test_exactly_one_source(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            RunFirmwareInput(work_product_id=uuid4())
        with pytest.raises(ValueError, match="board and framework"):
            RunFirmwareInput(work_product_id=uuid4(), files=[GeneratedFile(path="a.c", content="")])
