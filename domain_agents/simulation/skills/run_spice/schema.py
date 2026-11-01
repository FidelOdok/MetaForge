"""Input/output schemas for the run_spice skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class RunSpiceInput(BaseModel):
    """Input for the run_spice skill (the spice.run_simulation contract, FORGE-542)."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the circuit design")
    netlist_path: str | None = Field(
        default=None,
        description="SPICE netlist on the adapter workspace, e.g. from kicad.export_netlist",
    )
    netlist: str | None = Field(default=None, description="The circuit as SPICE text")
    analysis_type: str = Field(default="dc", description="Analysis type: op, dc, ac or transient")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "dc: source, start, stop, step. ac: variation, points, fstart, fstop. "
            "transient: step, stop, start, max_step. Omit to use the netlist's own card"
        ),
    )
    probes: list[str] = Field(
        default_factory=list, description="Vectors to return, e.g. v(out); default all"
    )

    @model_validator(mode="after")
    def _one_netlist(self) -> RunSpiceInput:
        if bool(self.netlist) == bool(self.netlist_path):
            raise ValueError("give exactly one of netlist or netlist_path")
        return self


class RunSpiceOutput(BaseModel):
    """Output from the run_spice skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    results: dict[str, Any] = Field(
        default_factory=dict,
        description="Per vector: final/min/max (AC: mag_db_* and phase_deg_final), with unit",
    )
    waveform_data: dict[str, Any] = Field(
        default_factory=dict, description="Decimated waveform per vector, keyed with the scale"
    )
    scale: str | None = Field(default=None, description="Sweep variable (time, frequency, ...)")
    waveforms: list[str] = Field(
        default_factory=list, description="Paths of the ngspice rawfiles on the adapter"
    )
    convergence: bool = Field(..., description="Whether the simulation converged")
    sim_time_s: float = Field(default=0.0, ge=0, description="Simulation wall-clock time in sec")
    log: str = Field(default="", description="ngspice errors when it did not converge")
