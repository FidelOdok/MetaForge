"""Input/output schemas for the generate_cad skill."""

from __future__ import annotations

from typing import Any, Literal
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


class GenerateCadInput(BaseModel):
    """Input for the generate_cad skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the CAD model")
    shape_type: str = Field(
        ...,
        description="Parametric shape type: bracket, plate, enclosure, cylinder",
    )
    dimensions: dict[str, float] = Field(
        ...,
        min_length=1,
        description="Shape-specific dimensions in mm (e.g., width, height, thickness)",
    )
    material: str = Field(
        default="aluminum_6061",
        description="Material name for metadata",
    )
    output_path: str = Field(
        default="",
        description="Optional output STEP file path (auto-generated if empty)",
    )
    constraints: dict[str, float] = Field(
        default_factory=dict,
        description="Optional constraints (max_mass_kg, min_wall_thickness_mm)",
    )
    backend: Literal["cadquery", "freecad"] = Field(
        default="cadquery",
        description="CAD backend to use. CadQuery is lighter and recommended; FreeCAD as fallback.",
    )


class GenerateCadOutput(BaseModel):
    """Output from the generate_cad skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    cad_file: str = Field(..., description="Path to generated STEP file")
    shape_type: str = Field(..., description="Shape type that was generated")
    volume_mm3: float = Field(..., ge=0, description="Volume in cubic millimeters")
    surface_area_mm2: float = Field(..., ge=0, description="Surface area in square millimeters")
    bounding_box: BoundingBox = Field(
        default_factory=BoundingBox, description="Axis-aligned bounding box"
    )
    parameters_used: dict[str, Any] = Field(
        default_factory=dict, description="Parameters passed to FreeCAD"
    )
    material: str = Field(..., description="Material used")
