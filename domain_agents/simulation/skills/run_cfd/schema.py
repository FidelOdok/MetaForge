"""Input/output schemas for the run_cfd skill."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


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


# --- Internal flow (FORGE-557) -----------------------------------------------
#
# These mirror ``openfoam.run_internal_flow``'s arguments. This package may not
# import ``tool_registry`` (the layering rule: tools are reached only through
# MCP), so the shape is written out here, and a unit test fails if it drifts
# from the adapter's own model.


class FlowFluid(BaseModel):
    """A Newtonian, incompressible fluid. Both figures are required."""

    density_kg_m3: float = Field(gt=0)
    kinematic_viscosity_m2_s: float = Field(gt=0)


class FlowMesh(BaseModel):
    cell_size_m: float | None = Field(default=None, gt=0)
    surface_refinement_min: int = Field(default=1, ge=0, le=6)
    surface_refinement_max: int = Field(default=2, ge=0, le=6)


class FlowSolver(BaseModel):
    max_iterations: int = Field(default=1000, ge=1, le=100_000)
    residual_target: float = Field(default=1e-5, gt=0, lt=1)
    n_procs: int = Field(default=1, ge=1)


class InternalFlowCase(BaseModel):
    """Steady incompressible internal flow, solved with OpenFOAM.

    The fluid domain is given as STL surfaces on the adapter workspace: an
    inlet, an outlet and the walls, which together must close.
    """

    inlet_stl: str = Field(..., min_length=1)
    outlet_stl: str = Field(..., min_length=1)
    wall_stls: list[str] = Field(..., min_length=1)
    stl_units: Literal["m", "mm"]
    inside_point: tuple[float, float, float] = Field(
        ..., description="A point inside the fluid, in the STL units"
    )
    fluid: FlowFluid
    inlet_velocity_m_s: float = Field(..., gt=0, description="Uniform, normal to the inlet")
    turbulence: Literal["laminar", "kOmegaSST"]
    turbulence_intensity: float | None = Field(default=None, gt=0, le=1)
    turbulence_length_scale_m: float | None = Field(default=None, gt=0)
    mesh: FlowMesh = Field(default_factory=FlowMesh)
    solver: FlowSolver = Field(default_factory=FlowSolver)

    @model_validator(mode="after")
    def _turbulent_needs_inlet_values(self) -> InternalFlowCase:
        if self.turbulence == "kOmegaSST" and (
            self.turbulence_intensity is None or self.turbulence_length_scale_m is None
        ):
            raise ValueError(
                "kOmegaSST needs turbulence_intensity and turbulence_length_scale_m; "
                "nothing is assumed for them"
            )
        return self


class RunCfdInput(BaseModel):
    """Input for the run_cfd skill.

    Two analyses, one per call. ``conduction`` is steady conduction to a
    fixed-temperature sink (CalculiX). ``internal_flow`` is steady internal flow
    (OpenFOAM, FORGE-557): pressure drop and flow rate through a bounded domain.
    Convection, external flow, compressible flow and heat transfer to a moving
    fluid are not solved, and asking for them is refused rather than answered with
    zeros.
    """

    work_product_id: UUID = Field(..., description="Twin work_product ID for the mechanical design")
    geometry_file: str | None = Field(
        default=None, description="Geometry the case came from (STEP/STL), for the record"
    )
    fluid_properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form flow input; refused. Use internal_flow, which is typed",
    )
    boundary_conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form flow boundary conditions; refused. Use internal_flow",
    )
    conduction: ConductionCase | None = Field(
        default=None, description="Steady conduction case, solved with calculix.run_thermal"
    )
    internal_flow: InternalFlowCase | None = Field(
        default=None, description="Steady internal flow, solved with openfoam.run_internal_flow"
    )


class RunCfdOutput(BaseModel):
    """Output from the run_cfd skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    analysis: Literal["conduction_only", "internal_flow"] = Field(
        default="conduction_only",
        description="What was solved. conduction_only computes no flow field",
    )
    max_temperature_c: float | None = Field(
        default=None, description="Peak temperature in degrees Celsius (conduction)"
    )
    min_temperature_c: float | None = Field(default=None)
    max_velocity_ms: float | None = Field(
        default=None, description="Not computed by either analysis"
    )
    pressure_drop_pa: float | None = Field(
        default=None, description="Static pressure drop inlet to outlet (internal_flow)"
    )
    volumetric_flow_m3_s: float | None = Field(
        default=None, description="Flow rate through the inlet (internal_flow)"
    )
    converged: bool | None = Field(
        default=None, description="Whether the solver's residual controls were met (internal_flow)"
    )
    solution_stable: bool | None = Field(
        default=None, description="Whether the pressure drop had stopped moving (internal_flow)"
    )
    mesh_cells: int | None = Field(default=None, description="Cells in the solved mesh")
    case_dir: str | None = Field(
        default=None, description="Where the OpenFOAM case is kept on the adapter workspace"
    )
    solver: str = Field(default="calculix.run_thermal")
    warnings: list[str] = Field(default_factory=list)
