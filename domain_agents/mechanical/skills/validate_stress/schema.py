"""Input/output schemas for the validate_stress skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class StressConstraint(BaseModel):
    """A constraint on allowable stress values."""

    max_von_mises_mpa: float = Field(
        ..., gt=0, description="Maximum allowable von Mises stress in MPa"
    )
    safety_factor: float = Field(default=1.5, ge=1.0, description="Required safety factor")
    material: str = Field(..., description="Material name for property lookup")


class ValidateStressInput(BaseModel):
    """Input for stress validation skill."""

    work_product_id: UUID = Field(..., description="ID of the CAD model work_product in the Twin")
    mesh_file_path: str = Field(..., min_length=1, description="Path to the mesh file (.inp)")
    load_case: str = Field(..., min_length=1, description="Load case identifier")
    constraints: list[StressConstraint] = Field(
        ..., min_length=1, description="Stress constraints to check"
    )


class StressResult(BaseModel):
    """Result for a single stress check in a region."""

    region: str = Field(..., description="Region or element set name")
    max_von_mises_mpa: float = Field(..., description="Maximum von Mises stress found")
    allowable_mpa: float = Field(..., description="Allowable stress for this region")
    safety_factor_achieved: float = Field(..., description="Actual safety factor achieved")
    passed: bool = Field(..., description="Whether this region passes the constraint")


class ValidateStressOutput(BaseModel):
    """Output from stress validation skill."""

    work_product_id: UUID = Field(..., description="ID of the analyzed work_product")
    overall_passed: bool = Field(..., description="Whether all constraints passed")
    results: list[StressResult] = Field(..., description="Per-region stress results")
    max_stress_mpa: float = Field(..., description="Global maximum stress found")
    critical_region: str = Field(..., description="Region with highest stress")
    solver_time_seconds: float = Field(default=0.0, description="FEA solver execution time")
    mesh_elements: int = Field(default=0, description="Number of mesh elements used")
