"""Handler for the run_firmware skill (FORGE-674/677)."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import RunFirmwareInput, RunFirmwareOutput


class RunFirmwareHandler(SkillBase[RunFirmwareInput, RunFirmwareOutput]):
    """Builds firmware and, when checks are given, runs it on an emulated board.

    ``firmware.build`` compiles the project in the firmware adapter and
    returns the compiler's own diagnostics and flash/RAM use. Only a build
    that passed is emulated: ``firmware.run_emulated`` boots the ELF on
    Renode or Espressif QEMU and checks the UART and memory. A step that did
    not run stays ``not_established``; nothing here turns it into a pass.
    """

    input_type = RunFirmwareInput
    output_type = RunFirmwareOutput

    async def validate_preconditions(self, input_data: RunFirmwareInput) -> list[str]:
        errors: list[str] = []
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        if not await self.context.mcp.is_available("firmware.build"):
            errors.append("firmware.build is not available (the firmware adapter is not running)")
        return errors

    async def execute(self, input_data: RunFirmwareInput) -> RunFirmwareOutput:
        self.logger.info(
            "Building firmware",
            work_product_id=input_data.work_product_id,
            project_path=input_data.project_path,
            board=input_data.board,
            emulate=input_data.emulate,
        )
        args: dict[str, Any] = {}
        if input_data.project_path:
            args["project_path"] = input_data.project_path
        else:
            args["files"] = [f.model_dump() for f in input_data.files]
            args["board"] = input_data.board
            args["framework"] = input_data.framework
            if input_data.build_flags:
                args["build_flags"] = input_data.build_flags
        if input_data.env:
            args["env"] = input_data.env
        built: dict[str, Any] = await self.context.mcp.invoke("firmware.build", args, timeout=900)

        out = RunFirmwareOutput(
            work_product_id=input_data.work_product_id,
            build_verdict=built.get("verdict", "not_established"),
            error_count=int(built.get("error_count", 0)),
            warning_count=int(built.get("warning_count", 0)),
            diagnostics=list(built.get("diagnostics", [])),
            flash=built.get("flash"),
            ram=built.get("ram"),
            elf_path=built.get("elf_path"),
            toolchain=dict(built.get("toolchain", {})),
            log_tail=str(built.get("log_tail", "")),
            reason=str(built.get("reason", "")),
        )
        if not input_data.emulate or out.build_verdict != "pass" or not out.elf_path:
            return out

        board = input_data.board or built.get("board")
        ran: dict[str, Any] = await self.context.mcp.invoke(
            "firmware.run_emulated",
            {
                "elf_path": out.elf_path,
                "board": board,
                "sim_time_s": input_data.sim_time_s,
                "uart_expect": input_data.uart_expect,
                "uart_forbid": input_data.uart_forbid,
                "memory_expect": [m.model_dump() for m in input_data.memory_expect],
            },
            timeout=300,
        )
        out.emulation_verdict = ran.get("verdict", "not_established")
        out.emulator = ran.get("emulator")
        out.assertions = list(ran.get("assertions", []))
        out.uart_log = str(ran.get("uart_log", ""))
        if ran.get("reason"):
            out.reason = str(ran["reason"])
        return out

    async def validate_output(self, output: RunFirmwareOutput) -> list[str]:
        errors: list[str] = []
        if output.build_verdict == "pass" and not output.elf_path:
            errors.append("build reported pass without an ELF")
        return errors
