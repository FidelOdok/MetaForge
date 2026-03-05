"""Input/output schemas for the configure_rtos skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConfigureRtosInput(BaseModel):
    """Input for the configure_rtos skill."""

    artifact_id: str = Field(..., description="Twin artifact ID for the firmware project")
    rtos_name: str = Field(
        ...,
        min_length=1,
        description="RTOS to configure (e.g., 'FreeRTOS', 'Zephyr', 'ChibiOS')",
    )
    task_definitions: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="Task definitions: [{'name': '...', 'priority': N, 'stack_size': N}]",
    )
    heap_size_kb: int = Field(default=64, gt=0, description="Heap size in kilobytes")
    tick_rate_hz: int = Field(default=1000, gt=0, description="System tick rate in Hz")


class ConfigureRtosOutput(BaseModel):
    """Output from the configure_rtos skill."""

    artifact_id: str = Field(..., description="Twin artifact ID")
    config_file: str = Field(..., description="Path to generated RTOS configuration file")
    tasks_configured: int = Field(..., ge=0, description="Number of tasks configured")
    memory_estimate_kb: int = Field(..., ge=0, description="Estimated memory usage in KB")
