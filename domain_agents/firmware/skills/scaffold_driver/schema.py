"""Input/output schemas for the scaffold_driver skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from domain_agents.firmware.codegen import GeneratedFile


class ScaffoldDriverInput(BaseModel):
    """Input for the scaffold_driver skill.

    FORGE-545: the register list is required and comes from the part's
    datasheet. The handler used to return one hard-coded map (WHO_AM_I at
    0x00, CTRL_REG1 at 0x20, ...) for every part.
    """

    work_product_id: UUID = Field(..., description="Twin work_product ID for the firmware project")
    peripheral_type: str = Field(
        ...,
        min_length=1,
        description="Peripheral type (e.g., 'accelerometer', 'temperature_sensor', 'display')",
    )
    interface: str = Field(
        default="spi", description="Communication interface: spi, i2c, uart, parallel"
    )
    driver_name: str = Field(
        ..., min_length=1, description="Name for the driver (e.g., 'bmi088', 'bmp280')"
    )
    registers: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "From the datasheet: [{'name': 'CHIP_ID', 'address': '0x00', 'access': 'r', "
            "'reset': '0x1E', 'expected': '0x1E', 'description': '...'}]. access is r, w "
            "or rw; 'expected' marks the identity register init() checks"
        ),
    )
    address_bits: int = Field(default=8, ge=1, le=16, description="Register address width")
    source: str = Field(default="", description="Datasheet and revision the registers came from")


class ScaffoldDriverOutput(BaseModel):
    """Output from the scaffold_driver skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    files: list[GeneratedFile] = Field(
        default_factory=list, description="Generated files, path and full content"
    )
    driver_files: list[str] = Field(
        default_factory=list, description="Paths of the generated driver files"
    )
    interface_type: str = Field(..., description="Communication interface used")
    register_map: dict[str, Any] = Field(
        default_factory=dict, description="The register map the driver was generated from"
    )
