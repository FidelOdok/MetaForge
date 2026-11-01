"""Input/output schemas for the validate_stress skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class StressConstraint(BaseModel):
    """A stress limit and the safety factor to hold it with.

    FORGE-554: ``max_von_mises_mpa`` is the material's *limit* stress (yield,
    or ultimate where that is the criterion), before any safety factor; the
    allowable is ``max_von_mises_mpa / safety_factor``. It used to be
    described as "maximum allowable", so a caller passing an already derated
    allowable had the factor applied twice. A caller who has the allowable
    itself passes ``allowable_mpa`` instead, which is used as given.
    """

    max_von_mises_mpa: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Limit stress in MPa (e.g. yield strength) BEFORE the safety factor; the "
            "allowable is this divided by safety_factor"
        ),
    )
    allowable_mpa: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Allowable stress in MPa with the safety factor ALREADY applied; used as given. "
            "Pass this or max_von_mises_mpa, not both"
        ),
    )
    safety_factor: float = Field(default=1.5, ge=1.0, description="Required safety factor")
    material: str = Field(..., description="Material name for property lookup")

    @model_validator(mode="after")
    def _one_limit(self) -> StressConstraint:
        if (self.max_von_mises_mpa is None) == (self.allowable_mpa is None):
            raise ValueError(
                "give exactly one of max_von_mises_mpa (limit stress, divided by the safety "
                "factor) or allowable_mpa (already derated, used as given)"
            )
        return self

    @property
    def allowable(self) -> float:
        """The stress the result must stay at or below."""
        if self.allowable_mpa is not None:
            return self.allowable_mpa
        assert self.max_von_mises_mpa is not None  # noqa: S101 - guaranteed by _one_limit
        return self.max_von_mises_mpa / self.safety_factor

    @property
    def limit(self) -> float:
        """The limit stress the achieved safety factor is measured against."""
        if self.max_von_mises_mpa is not None:
            return self.max_von_mises_mpa
        assert self.allowable_mpa is not None  # noqa: S101
        return self.allowable_mpa * self.safety_factor


class ValidateStressInput(BaseModel):
    """Input for stress validation skill."""

    work_product_id: UUID = Field(..., description="ID of the CAD model work_product in the Twin")
    mesh_file_path: str = Field(..., min_length=1, description="Path to the mesh file (.inp)")
    load_case: str = Field(..., min_length=1, description="Load case identifier")
    constraints: list[StressConstraint] = Field(
        ..., min_length=1, description="Stress constraints to check"
    )
    # FORGE-234: calculix.run_fea now builds a complete, solvable deck around
    # the mesh instead of invoking it directly -- it has no default material
    # or boundary condition/load to build that deck around, so this skill
    # must supply them too. material_name is a single top-level property (not
    # per-StressConstraint) because only one FEA solve backs every constraint
    # check below.
    material_name: str = Field(
        ..., min_length=1, description="Material name for FEA elastic properties (e.g. 'steel')"
    )
    fixed_node_set: str = Field(
        ..., min_length=1, description="Mesh element set name to fully constrain (fixed support)"
    )
    load_node_set: str = Field(
        ..., min_length=1, description="Mesh element set name to apply load_force_n to"
    )
    load_force_n: tuple[float, float, float] = Field(
        ..., description="[Fx, Fy, Fz] total applied force in Newtons"
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
