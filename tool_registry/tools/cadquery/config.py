"""CadQuery adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CadqueryConfig(BaseModel):
    """Configuration for the CadQuery tool adapter."""

    work_dir: str = Field(
        default="/tmp/cadquery", description="Working directory for CAD operations"
    )
    max_operation_time: int = Field(default=300, ge=1, description="Max operation time in seconds")
    max_memory_mb: int = Field(
        default=2048, ge=256, description="Max memory for CadQuery operations"
    )
    max_script_lines: int = Field(
        default=200, ge=10, description="Maximum allowed lines in an execute_script call"
    )
    sandbox_enabled: bool = Field(
        default=True, description="Enable sandbox restrictions for script execution"
    )
    supported_export_formats: list[str] = Field(
        default=["step", "stl", "obj", "brep", "amf", "svg"],
        description="Supported CAD export formats",
    )
    supported_import_formats: list[str] = Field(
        default=["step", "stp", "brep"],
        description="Supported CAD import formats",
    )
