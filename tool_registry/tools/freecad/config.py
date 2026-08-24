"""FreeCAD adapter configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FreecadConfig(BaseModel):
    """Configuration for the FreeCAD tool adapter."""

    freecad_binary: str = Field(default="freecadcmd", description="Path to headless FreeCAD binary")
    work_dir: str = Field(
        default="/tmp/freecad", description="Working directory for CAD operations"
    )
    max_operation_time: int = Field(default=300, ge=1, description="Max operation time in seconds")
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
    default_mesh_algorithm: str = Field(default="netgen", description="Default meshing algorithm")
    session_ttl_seconds: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "Idle-eviction window for stateful FreeCAD sessions (MET-644). "
            "Env-overridable (FREECAD_SESSION_TTL_SECONDS) so ops can tune "
            "without a code change; the 1800s default was not itself "
            "confirmed too short during the MET-642 eval -- the eviction "
            "observed there most likely swept a stale session from an "
            "earlier run, not the active one, since lazy eviction only "
            "fires when open_session()/get() next runs on the store."
        ),
    )
    max_sessions: int = Field(
        default=32,
        ge=1,
        description=(
            "Max concurrent live FreeCAD documents held in memory before the "
            "least-recently-used session is evicted (LRU capacity eviction). "
            "Env-overridable (FREECAD_MAX_SESSIONS). Each live document has a "
            "non-trivial memory footprint -- see MET-643 (adapter container "
            "restart under memory pressure, root cause unconfirmed)."
        ),
    )
