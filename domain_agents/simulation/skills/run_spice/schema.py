"""Input/output schemas for the run_spice skill."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RunSpiceInput(BaseModel):
    """Input for the run_spice skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID for the circuit design")
    netlist_path: str = Field(
        ..., min_length=1, description="Path to the SPICE netlist file (.cir/.spice)"
    )
    analysis_type: str = Field(default="dc", description="Analysis type: dc, ac, or transient")
    params: dict[str, Any] = Field(default_factory=dict, description="Analysis-specific parameters")


class RunSpiceOutput(BaseModel):
    """Output from the run_spice skill."""

    work_product_id: UUID = Field(..., description="Twin work_product ID")
    results: dict[str, Any] = Field(
        default_factory=dict, description="Simulation results keyed by node/signal name"
    )
    waveforms: list[str] = Field(
        default_factory=list, description="Paths to generated waveform files"
    )
    convergence: bool = Field(..., description="Whether the simulation converged")
    sim_time_s: float = Field(default=0.0, ge=0, description="Simulation wall-clock time in sec")
