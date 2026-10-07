"""Input/output schemas for the run_fea skill."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class RunFeaInput(BaseModel):
    """Input for the run_fea skill: ``calculix.run_fea``'s own arguments (FORGE-561).

    The skill used to send ``load_cases``, ``analysis_type="static"`` and a
    material *name*, none of which the tool takes, so every call failed.
    """

    work_product_id: UUID = Field(..., description="Twin work_product ID for the mechanical design")
    mesh_file: str = Field(..., min_length=1, description="Volume mesh (.inp), e.g. generate_mesh")
    load_case: str = Field(..., min_length=1, description="Name of the load case")
    analysis_type: Literal["static_stress", "modal"] = Field(
        default="static_stress", description="static_stress or modal"
    )
    material: dict[str, Any] = Field(
        ...,
        description=(
            "{'name': 'aluminium_6061'} or explicit youngs_modulus_mpa/poissons_ratio; "
            "modal needs the name (density is looked up by name)"
        ),
    )
    fixed_node_set: str = Field(..., min_length=1, description="Face held fixed")
    load_node_set: str | None = Field(default=None, description="Loaded face (static_stress)")
    load_force_n: list[float] | None = Field(
        default=None, description="[Fx, Fy, Fz] in N, total on the load face (static_stress)"
    )
    num_modes: int = Field(default=3, ge=1, description="Modes to extract (modal)")
    yield_strength_mpa: float | None = Field(
        default=None,
        gt=0,
        description="Material yield, cited; gives safety_factor. The tool has no yield data",
    )

    @field_validator("analysis_type", mode="before")
    @classmethod
    def _alias(cls, value: Any) -> Any:
        return "static_stress" if value == "static" else value

    @model_validator(mode="after")
    def _static_needs_a_load(self) -> RunFeaInput:
        if self.analysis_type == "static_stress":
            if not self.load_node_set or not self.load_force_n or len(self.load_force_n) != 3:
                raise ValueError("static_stress needs load_node_set and load_force_n [Fx, Fy, Fz]")
        return self


class RunFeaOutput(BaseModel):
    """Output from the run_fea skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    analysis_type: str
    max_stress_mpa: float | None = Field(
        default=None, description="Peak von Mises stress (static_stress)"
    )
    max_displacement_mm: float | None = Field(default=None)
    safety_factor: float | None = Field(
        default=None,
        description="yield_strength_mpa / max_stress_mpa; None when no yield was given",
    )
    frequencies_hz: list[float] = Field(default_factory=list, description="Modal frequencies")
    frd_path: str = Field(default="", description="CalculiX result file, for extract_results")
    solver_time_s: float = Field(default=0.0, ge=0, description="Solver wall-clock time in seconds")
