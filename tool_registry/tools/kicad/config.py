"""KiCad adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KicadConfig(BaseModel):
    """Configuration for the KiCad tool adapter."""

    kicad_cli: str = Field(
        default="kicad-cli", description="Path to kicad-cli binary"
    )
    work_dir: str = Field(
        default="/tmp/kicad", description="Working directory for KiCad operations"
    )
    max_operation_time: int = Field(
        default=120, ge=1, description="Max operation time in seconds"
    )
    max_memory_mb: int = Field(
        default=1024, ge=256, description="Max memory for KiCad operations"
    )
    supported_versions: list[str] = Field(
        default=["7", "8"], description="Supported KiCad versions"
    )
