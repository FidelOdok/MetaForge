"""Input/output schemas for the generate_cad_script skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Axis-aligned bounding box for generated geometry."""

    min_x: float = Field(default=0.0, description="Minimum X coordinate in mm")
    min_y: float = Field(default=0.0, description="Minimum Y coordinate in mm")
    min_z: float = Field(default=0.0, description="Minimum Z coordinate in mm")
    max_x: float = Field(default=0.0, description="Maximum X coordinate in mm")
    max_y: float = Field(default=0.0, description="Maximum Y coordinate in mm")
    max_z: float = Field(default=0.0, description="Maximum Z coordinate in mm")


class GenerateCadScriptInput(BaseModel):
    """Input for the generate_cad_script skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the CAD model")
    description: str = Field(
        ...,
        min_length=1,
        description="Natural language description of the desired 3D model",
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Design constraints (max dimensions, wall thickness, hole sizes, etc.)",
    )
    material: str = Field(
        default="aluminum_6061",
        description="Material name for metadata",
    )
    output_format: str = Field(
        default="step",
        description="Output file format (step, stl, brep)",
    )


class GenerateCadScriptOutput(BaseModel):
    """Output from the generate_cad_script skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    cad_file: str = Field(..., description="Path to generated CAD file")
    script_text: str = Field(..., description="The CadQuery Python script that was executed")
    volume_mm3: float = Field(..., ge=0, description="Volume in cubic millimeters")
    surface_area_mm2: float = Field(..., ge=0, description="Surface area in square millimeters")
    bounding_box: BoundingBox = Field(
        default_factory=BoundingBox, description="Axis-aligned bounding box"
    )
