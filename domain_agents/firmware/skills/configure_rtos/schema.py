"""Input/output schemas for the configure_rtos skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from domain_agents.firmware.codegen import GeneratedFile, RtosTask


class ConfigureRtosInput(BaseModel):
    """Input for the configure_rtos skill.

    FORGE-545: each task's ``stack_size`` (bytes) and ``priority`` are
    required. A missing stack used to be filled with 4 KB, and any stack
    under 1 KB was counted as 4 KB as well.
    """

    work_product_id: UUID = Field(..., description="Twin work_product ID for the firmware project")
    rtos_name: str = Field(
        ...,
        min_length=1,
        description="RTOS to configure: FreeRTOS or Zephyr (others are refused)",
    )
    task_definitions: list[RtosTask] = Field(
        ...,
        min_length=1,
        description=(
            "Tasks: [{'name': '...', 'priority': N (higher is more urgent), "
            "'stack_size': bytes, 'entry': optional function name}]"
        ),
    )
    heap_size_kb: int = Field(default=64, gt=0, description="Heap size in kilobytes")
    tick_rate_hz: int = Field(default=1000, gt=0, description="System tick rate in Hz")
    stack_word_bytes: int = Field(
        default=4, description="Bytes per stack word (4 on a 32-bit MCU); FreeRTOS counts words"
    )
    output_dir: str = Field(default="firmware/rtos", description="Output directory")


class ConfigureRtosOutput(BaseModel):
    """Output from the configure_rtos skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    files: list[GeneratedFile] = Field(
        default_factory=list, description="Generated files, path and full content"
    )
    config_file: str = Field(..., description="Path of the generated kernel configuration")
    tasks_configured: int = Field(..., ge=0, description="Number of tasks configured")
    memory_estimate_kb: int = Field(
        ..., ge=0, description="Heap plus every task stack, rounded up to whole KB"
    )
