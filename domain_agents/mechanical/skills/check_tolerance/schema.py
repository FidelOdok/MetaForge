"""Input/output schemas for the check_tolerance skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToleranceSpec(BaseModel):
    """A single tolerance specification for a dimension."""

    dimension_id: str = Field(..., description="Dimension identifier (e.g., 'D1', 'D2')")
    feature_name: str = Field(
        ..., description="Feature name (e.g., 'bore_diameter', 'wall_thickness')"
    )
    nominal_value: float = Field(..., gt=0, description="Nominal dimension in mm")
    upper_tolerance: float = Field(..., description="Upper deviation in mm (e.g., +0.05)")
    lower_tolerance: float = Field(..., description="Lower deviation in mm (e.g., -0.05)")
    tolerance_grade: str = Field(default="", description="ISO grade, e.g., 'IT7', 'IT8'")
    tolerance_type: str = Field(
        default="bilateral",
        description="Tolerance type: bilateral, unilateral_plus, unilateral_minus",
    )


class ManufacturingProcess(BaseModel):
    """Manufacturing process capabilities."""

    process_type: str = Field(
        ...,
        description=(
            "Process type (e.g., 'cnc_milling', 'injection_molding', '3d_printing_fdm')"
        ),
    )
    achievable_tolerance: float = Field(
        ..., gt=0, description="Best achievable tolerance in mm for this process"
    )
    surface_finish_ra: float = Field(
        default=0.0, ge=0, description="Achievable surface finish Ra in um"
    )
    min_feature_size: float = Field(
        default=0.0, ge=0, description="Minimum feature size in mm"
    )
    max_aspect_ratio: float = Field(
        default=0.0, ge=0, description="Maximum aspect ratio (depth/width)"
    )


class CheckToleranceInput(BaseModel):
    """Input for the check_tolerance skill."""

    artifact_id: str = Field(..., description="ID of the artifact in the Digital Twin")
    tolerances: list[ToleranceSpec] = Field(
        default_factory=list, description="List of tolerance specifications to check"
    )
    manufacturing_process: ManufacturingProcess = Field(
        ..., description="Manufacturing process capabilities"
    )
    material: str = Field(default="aluminum_6061", description="Material identifier")
    check_stack_up: bool = Field(
        default=False, description="Whether to check tolerance stack-up (RSS method)"
    )


class ToleranceViolation(BaseModel):
    """A single tolerance violation."""

    dimension_id: str = Field(..., description="Dimension identifier")
    feature_name: str = Field(..., description="Feature name")
    violation_type: str = Field(
        ...,
        description=(
            "Violation type: 'too_tight', 'below_min_feature', "
            "'aspect_ratio_exceeded', 'stack_up_exceeded'"
        ),
    )
    severity: str = Field(..., description="Severity: 'error' or 'warning'")
    specified_tolerance: float = Field(
        ..., description="What was specified (tolerance band in mm)"
    )
    achievable_tolerance: float = Field(
        ..., description="What the process can achieve (tolerance in mm)"
    )
    message: str = Field(..., description="Human-readable explanation")
    recommendation: str = Field(..., description="Suggested fix")


class ToleranceResult(BaseModel):
    """Result for a single tolerance check."""

    dimension_id: str = Field(..., description="Dimension identifier")
    feature_name: str = Field(..., description="Feature name")
    nominal_value: float = Field(..., description="Nominal dimension in mm")
    tolerance_range: float = Field(
        ..., description="Total tolerance band (upper - lower) in mm"
    )
    status: str = Field(..., description="Status: 'pass', 'warning', or 'fail'")
    capability_index: float = Field(
        ...,
        description="Process capability index Cp = tolerance_range / (6 * process_sigma)",
    )
    message: str = Field(..., description="Human-readable result explanation")


class CheckToleranceOutput(BaseModel):
    """Output from the check_tolerance skill."""

    artifact_id: str = Field(..., description="ID of the analyzed artifact")
    process_type: str = Field(..., description="Manufacturing process used for analysis")
    total_dimensions_checked: int = Field(
        ..., ge=0, description="Number of dimensions checked"
    )
    passed: int = Field(..., ge=0, description="Number of dimensions that passed")
    warnings: int = Field(..., ge=0, description="Number of dimensions with warnings")
    failures: int = Field(..., ge=0, description="Number of dimensions that failed")
    overall_status: str = Field(
        ..., description="Overall status: 'pass', 'marginal', or 'fail'"
    )
    results: list[ToleranceResult] = Field(
        default_factory=list, description="Per-dimension tolerance results"
    )
    violations: list[ToleranceViolation] = Field(
        default_factory=list, description="List of tolerance violations"
    )
    summary: str = Field(default="", description="Human-readable summary of the analysis")
