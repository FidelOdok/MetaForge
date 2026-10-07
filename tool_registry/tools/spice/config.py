"""SPICE adapter configuration (FORGE-542)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SpiceConfig(BaseModel):
    """Configuration for the ngspice tool adapter."""

    ngspice: str = Field(default="ngspice", description="ngspice binary name or path")
    work_dir: str = Field(
        default="/workspace", description="Adapter workspace: decks, rawfiles, netlist paths"
    )
    max_operation_time: int = Field(default=180, ge=1, description="Max simulation time, seconds")
