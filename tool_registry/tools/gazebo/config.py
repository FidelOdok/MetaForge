"""Gazebo Sim adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GazeboConfig(BaseModel):
    """Configuration for the Gazebo Sim tool adapter."""

    gz_binary: str = Field(default="gz", description="Path to the Gazebo Sim CLI binary")
    work_dir: str = Field(
        default="/tmp/gazebo", description="Working directory for simulation runs"
    )
    max_sim_time: int = Field(
        default=300, ge=1, description="Max wall-clock seconds for a simulation run"
    )
    headless: bool = Field(
        default=True, description="Run gz sim in server-only mode (-s), no GUI/rendering"
    )
