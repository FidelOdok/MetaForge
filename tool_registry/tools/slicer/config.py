"""Slicer adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SlicerConfig(BaseModel):
    """Configuration for the PrusaSlicer tool adapter."""

    prusaslicer: str = Field(default="prusa-slicer", description="PrusaSlicer binary name or path")
    work_dir: str = Field(
        default="/workspace", description="Adapter workspace: models in, G-code out"
    )
    max_operation_time: int = Field(default=600, ge=1, description="Max slicing time, seconds")
    max_input_mb: int = Field(default=200, ge=1, description="Largest model file accepted, MB")
    max_concurrent: int = Field(default=2, ge=1, description="Slices run at the same time")
