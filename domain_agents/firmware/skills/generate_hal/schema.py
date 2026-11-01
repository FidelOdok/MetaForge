"""Input/output schemas for the generate_hal skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from domain_agents.firmware.codegen import GeneratedFile


class GenerateHalInput(BaseModel):
    """Input for the generate_hal skill.

    FORGE-545: the pin map is required. Without it the handler used to
    invent ``<family>_DEFAULT`` for every peripheral.
    """

    work_product_id: UUID = Field(..., description="Twin work_product ID for the firmware project")
    mcu_family: str = Field(
        ...,
        min_length=1,
        description="MCU family: STM32F4, STM32H7, ESP32, nRF52, RP2040 or ATSAMD",
    )
    pin_map: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "The board's real pin assignments, e.g. from kicad.get_pin_mapping: "
            "[{'signal': 'IMU_CS', 'pin': 'PA4', 'peripheral': 'SPI1', 'direction': 'out'}]"
        ),
    )
    peripherals: list[str] = Field(
        default_factory=list,
        description="Optional: peripherals expected on the board; each must appear in pin_map",
    )
    source: str = Field(default="", description="Where the pin map came from (schematic, rev)")
    output_dir: str = Field(
        default="firmware/hal", description="Output directory for generated HAL files"
    )


class GenerateHalOutput(BaseModel):
    """Output from the generate_hal skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    files: list[GeneratedFile] = Field(
        default_factory=list, description="Generated files, path and full content"
    )
    generated_files: list[str] = Field(
        default_factory=list, description="Paths of the generated files"
    )
    pin_mappings: dict[str, Any] = Field(
        default_factory=dict, description="Signal identifier to MCU pin, from the pin map"
    )
    hal_version: str = Field(default="0.2.0", description="Version of the generator")
