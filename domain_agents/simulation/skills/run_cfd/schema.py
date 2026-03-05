"""Input/output schemas for the run_cfd skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunCfdInput(BaseModel):
    """Input for the run_cfd skill."""

    artifact_id: str = Field(..., description="Twin artifact ID for the mechanical design")
    geometry_file: str = Field(
        ..., min_length=1, description="Path to the geometry file (STEP/STL)"
    )
    fluid_properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Fluid properties: {'density_kg_m3': N, 'viscosity_pa_s': N}",
    )
    boundary_conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Boundary conditions: {'inlet_velocity_ms': N, 'outlet_pressure_pa': N}",
    )
    mesh_resolution: str = Field(
        default="medium", description="Mesh resolution: coarse, medium, fine"
    )


class RunCfdOutput(BaseModel):
    """Output from the run_cfd skill."""

    artifact_id: str = Field(..., description="Twin artifact ID")
    max_velocity_ms: float = Field(
        ..., ge=0, description="Maximum fluid velocity in m/s"
    )
    pressure_drop_pa: float = Field(
        ..., ge=0, description="Pressure drop across the domain in Pa"
    )
    max_temperature_c: float = Field(
        default=0.0, description="Maximum temperature in degrees Celsius"
    )
    convergence_residual: float = Field(
        ..., ge=0, description="Final convergence residual (lower is better)"
    )
