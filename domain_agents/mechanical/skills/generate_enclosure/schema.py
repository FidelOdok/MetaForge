"""Input/output schemas for the generate_enclosure skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ConnectorCutout(BaseModel):
    """Definition of a connector cutout on the enclosure wall."""

    width: float = Field(..., gt=0, description="Cutout width in mm")
    height: float = Field(..., gt=0, description="Cutout height in mm")
    x: float = Field(default=0.0, description="Horizontal offset from center of face in mm")
    z: float = Field(default=0.0, description="Vertical offset from center of face in mm")
    side: str = Field(
        default="front",
        description="Which face: front, back, left, right",
    )


class MountingHole(BaseModel):
    """Definition of a PCB mounting hole position."""

    x: float = Field(..., description="X position on PCB in mm (from PCB origin)")
    y: float = Field(..., description="Y position on PCB in mm (from PCB origin)")
    diameter: float = Field(default=3.0, gt=0, description="Hole diameter in mm")


class ExternalDimensions(BaseModel):
    """External dimensions of the generated enclosure."""

    length: float = Field(..., description="External length in mm")
    width: float = Field(..., description="External width in mm")
    height: float = Field(..., description="External height in mm")


class MountingInfo(BaseModel):
    """Summary of mounting features in the enclosure."""

    hole_count: int = Field(default=0, ge=0, description="Number of mounting holes")
    cutout_count: int = Field(default=0, ge=0, description="Number of connector cutouts")


class GenerateEnclosureInput(BaseModel):
    """Input for the generate_enclosure skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the enclosure")
    pcb_length: float = Field(..., gt=0, description="PCB length in mm")
    pcb_width: float = Field(..., gt=0, description="PCB width in mm")
    pcb_thickness: float = Field(default=1.6, gt=0, description="PCB thickness in mm")
    component_max_height: float = Field(
        default=10.0, gt=0, description="Max component height above PCB in mm"
    )
    connector_cutouts: list[ConnectorCutout] = Field(
        default_factory=list, description="Connector cutout definitions"
    )
    mounting_holes: list[MountingHole] = Field(
        default_factory=list, description="Mounting hole positions"
    )
    wall_thickness: float = Field(default=2.0, gt=0, description="Enclosure wall thickness in mm")
    material: str = Field(default="ABS", description="Material name for metadata")


class GenerateEnclosureOutput(BaseModel):
    """Output from the generate_enclosure skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    cad_file: str = Field(..., description="Path to generated enclosure STEP file")
    internal_volume: float = Field(..., ge=0, description="Internal volume in mm^3")
    external_dimensions: ExternalDimensions = Field(
        ..., description="External enclosure dimensions"
    )
    mounting_info: MountingInfo = Field(
        default_factory=MountingInfo, description="Mounting feature summary"
    )
    material: str = Field(..., description="Material used")
