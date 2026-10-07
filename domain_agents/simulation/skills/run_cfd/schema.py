"""Input/output schemas for the run_cfd skill."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ConductionCase(BaseModel):
    """Steady conduction to a fixed-temperature sink: what CalculiX can solve.

    These are ``calculix.run_thermal``'s own arguments. The handler used to
    send it geometry_file / fluid_properties / boundary_conditions, which it
    does not take, so every call failed (FORGE-543).
    """

    mesh_file: str = Field(..., min_length=1, description="Volume mesh, e.g. from generate_mesh")
    material: dict[str, Any] = Field(
        ...,
        description="{'name': 'aluminium_6061'} or {'thermal_conductivity_w_mk': N}",
    )
    heat_source_node_set: str = Field(..., min_length=1)
    power_dissipation_w: float = Field(..., gt=0)
    sink_node_set: str = Field(..., min_length=1)
    sink_temp_c: float


class RunCfdInput(BaseModel):
    """Input for the run_cfd skill.

    MetaForge has no flow solver (FORGE-543). Velocity, pressure and
    convection need one; asking for them is refused rather than answered
    with zeros. ``conduction`` runs the part of a thermal question that
    CalculiX can answer, and the result says it is conduction only.
    """

    work_product_id: UUID = Field(..., description="Twin work_product ID for the mechanical design")
    geometry_file: str | None = Field(
        default=None, description="Geometry the case came from (STEP/STL), for the record"
    )
    fluid_properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Flow input; refused while there is no flow solver",
    )
    boundary_conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Flow boundary conditions; refused while there is no flow solver",
    )
    conduction: ConductionCase | None = Field(
        default=None, description="Steady conduction case, solved with calculix.run_thermal"
    )


class RunCfdOutput(BaseModel):
    """Output from the run_cfd skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    analysis: Literal["conduction_only"] = Field(
        default="conduction_only",
        description="What was solved; no flow field is computed",
    )
    max_temperature_c: float = Field(..., description="Peak temperature in degrees Celsius")
    min_temperature_c: float | None = Field(default=None)
    max_velocity_ms: float | None = Field(
        default=None, description="Not computed: there is no flow solver"
    )
    pressure_drop_pa: float | None = Field(
        default=None, description="Not computed: there is no flow solver"
    )
    solver: str = Field(default="calculix.run_thermal")
    warnings: list[str] = Field(default_factory=list)
