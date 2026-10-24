"""KiCad adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KicadConfig(BaseModel):
    """Configuration for the KiCad tool adapter."""

    kicad_cli: str = Field(default="kicad-cli", description="Path to kicad-cli binary")
    work_dir: str = Field(
        default="/tmp/kicad", description="Working directory for KiCad operations"
    )
    max_operation_time: int = Field(default=120, ge=1, description="Max operation time in seconds")
    max_memory_mb: int = Field(default=1024, ge=256, description="Max memory for KiCad operations")
    supported_versions: list[str] = Field(
        default=["8", "9"], description="Supported KiCad versions"
    )
    # FORGE-683: Phase 2 generation.
    symbol_dir: str = Field(
        default="/usr/share/kicad/symbols", description="KiCad symbol libraries (*.kicad_sym)"
    )
    footprint_dir: str = Field(
        default="/usr/share/kicad/footprints", description="KiCad footprint libraries (*.pretty)"
    )
    pcbnew_python: str = Field(
        default="/usr/bin/python3", description="The Python that imports KiCad's pcbnew"
    )
    router_cmd: list[str] = Field(
        default=["java", "-jar", "/opt/freerouting/freerouting.jar"],
        description="Freerouting command (Specctra DSN in, session out)",
    )
    max_route_time: int = Field(default=600, ge=10, description="Max autoroute time, seconds")
