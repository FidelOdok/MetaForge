"""Input/output schemas for the create_assembly skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class AssemblyPart(BaseModel):
    """Definition of a part in the assembly."""

    name: str = Field(..., min_length=1, description="Part name (unique within assembly)")
    file: str = Field(..., min_length=1, description="Path to STEP file for this part")
    location: dict[str, float] = Field(
        default_factory=dict,
        description="Position and rotation: x, y, z, rx, ry, rz (defaults to origin)",
    )


class AssemblyConstraint(BaseModel):
    """Constraint between two parts in the assembly."""

    part_a: str = Field(..., description="First part name")
    part_b: str = Field(..., description="Second part name")
    type: str = Field(
        ...,
        description="Constraint type: Point, Axis, Plane, PointInPlane",
    )


class CreateAssemblyInput(BaseModel):
    """Input for the create_assembly skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the assembly")
    parts: list[AssemblyPart] = Field(
        ...,
        min_length=1,
        description="List of parts to assemble",
    )
    constraints: list[AssemblyConstraint] = Field(
        default_factory=list,
        description="Assembly constraints between parts",
    )
    output_path: str = Field(
        default="",
        description="Optional output STEP file path",
    )


class CreateAssemblyOutput(BaseModel):
    """Output from the create_assembly skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    assembly_file: str = Field(..., description="Path to generated assembly STEP file")
    part_count: int = Field(..., ge=0, description="Number of parts in the assembly")
    total_volume: float = Field(..., ge=0, description="Total volume of all parts in mm^3")
    interference_check_passed: bool = Field(
        ..., description="Whether assembly passed interference check"
    )
