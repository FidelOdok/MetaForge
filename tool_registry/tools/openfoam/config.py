"""OpenFOAM adapter configuration (FORGE-557)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OpenFoamConfig(BaseModel):
    """Configuration for the OpenFOAM tool adapter."""

    bashrc: str = Field(
        default="/opt/openfoam11/etc/bashrc",
        description="OpenFOAM environment file, sourced before every solver command",
    )
    work_dir: str = Field(
        default="/workspace",
        description="Adapter workspace: STL surfaces in, case directories out",
    )
    max_operation_time: int = Field(
        default=3600, ge=1, description="Wall-clock budget for one run (mesh + solve), seconds"
    )
    max_cells: int = Field(
        default=2_000_000,
        ge=1000,
        description="Hard cap on mesh cells; a run asking for more is refused, not truncated",
    )
    max_procs: int = Field(default=8, ge=1, description="Most MPI ranks one run may use")
