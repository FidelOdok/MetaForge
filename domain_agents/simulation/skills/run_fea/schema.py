"""Input/output schemas for the run_fea skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RunFeaInput(BaseModel):
    """Input for the run_fea skill."""

    artifact_id: UUID = Field(..., description="Twin artifact ID for the mechanical design")
    mesh_file: str = Field(..., min_length=1, description="Path to the FEA mesh file (.inp/.unv)")
    load_cases: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Load case definitions: [{'name': '...', 'force_n': N, 'direction': '...'}]",
    )
    analysis_type: str = Field(
        default="static", description="Analysis type: static, modal, thermal"
    )
    material: str = Field(
        default="steel_1018", description="Material identifier for properties lookup"
    )


class RunFeaOutput(BaseModel):
    """Output from the run_fea skill."""

    artifact_id: UUID = Field(..., description="Twin artifact ID")
    max_stress_mpa: float = Field(..., ge=0, description="Maximum von Mises stress in MPa")
    max_displacement_mm: float = Field(..., ge=0, description="Maximum displacement in mm")
    safety_factor: float = Field(
        ..., ge=0, description="Minimum safety factor across all load cases"
    )
    solver_time_s: float = Field(default=0.0, ge=0, description="Solver wall-clock time in seconds")
