"""FreeCAD adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FreecadConfig(BaseModel):
    """Configuration for the FreeCAD tool adapter."""

    freecad_binary: str = Field(
        default="freecadcmd", description="Path to headless FreeCAD binary"
    )
    work_dir: str = Field(
        default="/tmp/freecad", description="Working directory for CAD operations"
    )
    max_operation_time: int = Field(
        default=300, ge=1, description="Max operation time in seconds"
    )
    max_memory_mb: int = Field(
        default=2048, ge=256, description="Max memory for FreeCAD operations"
    )
    supported_import_formats: list[str] = Field(
        default=["step", "stp", "stl", "iges", "igs", "brep"],
        description="Supported CAD import formats",
    )
    supported_export_formats: list[str] = Field(
        default=["step", "stp", "stl", "obj", "brep"],
        description="Supported CAD export formats",
    )
    default_mesh_algorithm: str = Field(
        default="netgen", description="Default meshing algorithm"
    )
