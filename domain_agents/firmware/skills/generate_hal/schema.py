"""Input/output schemas for the generate_hal skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class GenerateHalInput(BaseModel):
    """Input for the generate_hal skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the firmware project")
    mcu_family: str = Field(
        ..., min_length=1, description="MCU family identifier (e.g., 'STM32F4', 'ESP32', 'nRF52')"
    )
    peripherals: list[str] = Field(
        ..., min_length=1, description="Peripherals to generate HAL for (e.g., ['GPIO', 'SPI'])"
    )
    output_dir: str = Field(
        default="firmware/hal", description="Output directory for generated HAL files"
    )


class GenerateHalOutput(BaseModel):
    """Output from the generate_hal skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    generated_files: list[str] = Field(
        default_factory=list, description="List of generated HAL source file paths"
    )
    pin_mappings: dict[str, Any] = Field(
        default_factory=dict, description="Pin-to-peripheral mapping used"
    )
    hal_version: str = Field(default="0.1.0", description="Version of the generated HAL")
