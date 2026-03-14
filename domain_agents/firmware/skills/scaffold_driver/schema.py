"""Input/output schemas for the scaffold_driver skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ScaffoldDriverInput(BaseModel):
    """Input for the scaffold_driver skill."""

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


class ScaffoldDriverOutput(BaseModel):
    """Output from the scaffold_driver skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    driver_files: list[str] = Field(
        default_factory=list, description="List of generated driver file paths"
    )
    interface_type: str = Field(..., description="Communication interface used")
    register_map: dict[str, Any] = Field(
        default_factory=dict, description="Register map template for the peripheral"
    )
