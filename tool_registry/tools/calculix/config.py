"""CalculiX adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CalculixConfig(BaseModel):
    """Configuration for the CalculiX tool adapter."""

    ccx_binary: str = Field(default="ccx", description="Path to CalculiX binary")
    work_dir: str = Field(
        default="/tmp/calculix", description="Working directory for solver files"
    )
    max_solve_time: int = Field(default=600, ge=1, description="Max solver time in seconds")
    max_memory_mb: int = Field(default=2048, ge=256, description="Max memory for solver")
    supported_analysis_types: list[str] = Field(
        default=["static_stress", "thermal", "modal"],
        description="Analysis types this adapter supports",
    )
