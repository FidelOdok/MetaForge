"""Input/output schemas for the run_firmware skill (FORGE-674/677)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from domain_agents.firmware.codegen import GeneratedFile

Verdict = Literal["pass", "fail", "not_established"]


class MemoryCheck(BaseModel):
    """A 32-bit word to read after the emulated run (Renode only)."""

    address: str = Field(..., description="Address, e.g. 0x40020C14")
    value: str = Field(..., description="Expected value, e.g. 0x1000")
    mask: str = Field(default="0xFFFFFFFF", description="Bits compared")


class RunFirmwareInput(BaseModel):
    """Input for the run_firmware skill: the firmware.build and firmware.run_emulated contract."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the firmware project")
    project_path: str | None = Field(
        default=None, description="A PlatformIO project on the adapter workspace"
    )
    files: list[GeneratedFile] = Field(
        default_factory=list,
        description="Sources to build instead of a project, e.g. the generate_hal output",
    )
    board: str | None = Field(
        default=None, description="PlatformIO board id with files: disco_f407vg, esp32dev"
    )
    framework: str | None = Field(default=None, description="With files: stm32cube or espidf")
    env: str | None = Field(default=None, description="PlatformIO env; default the first")
    build_flags: list[str] = Field(default_factory=list, description="Extra flags with files")
    uart_expect: list[str] = Field(
        default_factory=list, description="Regexes the UART must show, in order"
    )
    uart_forbid: list[str] = Field(
        default_factory=list, description="Regexes the UART must not show"
    )
    memory_expect: list[MemoryCheck] = Field(
        default_factory=list, description="Words to check after the run (Renode only)"
    )
    sim_time_s: float = Field(default=2.0, gt=0, le=600, description="Emulated seconds to run")
    static_check: bool = Field(
        default=False, description="Also run firmware.static_check (cppcheck) on the sources"
    )
    fail_on: Literal["high", "medium", "low"] = Field(
        default="high", description="Lowest static-check severity that fails"
    )

    @model_validator(mode="after")
    def _one_source(self) -> RunFirmwareInput:
        if bool(self.project_path) == bool(self.files):
            raise ValueError("give exactly one of project_path or files")
        if self.files and not (self.board and self.framework):
            raise ValueError("files needs board and framework")
        return self

    @property
    def emulate(self) -> bool:
        return bool(self.uart_expect or self.uart_forbid or self.memory_expect)


class RunFirmwareOutput(BaseModel):
    """Output from the run_firmware skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    build_verdict: Verdict = Field(..., description="firmware.build: pass, fail, not_established")
    error_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    diagnostics: list[dict[str, Any]] = Field(
        default_factory=list, description="Compiler/linker errors and warnings at file:line"
    )
    flash: dict[str, Any] | None = Field(default=None, description="Flash used vs the part")
    ram: dict[str, Any] | None = Field(default=None, description="RAM used vs the part")
    elf_path: str | None = Field(default=None, description="Built ELF on the adapter workspace")
    toolchain: dict[str, Any] = Field(default_factory=dict, description="Platform and packages")
    emulation_verdict: Verdict | None = Field(
        default=None, description="firmware.run_emulated, when checks were given and it built"
    )
    emulator: str | None = Field(default=None, description="renode or espressif-qemu")
    assertions: list[dict[str, Any]] = Field(
        default_factory=list, description="Each UART or memory check with its status"
    )
    uart_log: str = Field(default="", description="What the firmware printed (tail)")
    log_tail: str = Field(default="", description="Build log tail when the build failed")
    reason: str = Field(default="", description="Why a step was not established")
    check_verdict: Verdict | None = Field(
        default=None, description="firmware.static_check, when it was asked for"
    )
    check_counts: dict[str, int] = Field(
        default_factory=dict, description="Static-check findings per severity"
    )
    check_findings: list[dict[str, Any]] = Field(
        default_factory=list, description="Static-check findings, most severe first"
    )
