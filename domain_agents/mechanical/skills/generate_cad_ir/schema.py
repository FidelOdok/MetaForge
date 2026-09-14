"""Input/output schemas for the generate_cad_ir skill."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Axis-aligned bounding box for generated geometry."""

    min_x: float = Field(default=0.0, description="Minimum X coordinate in mm")
    min_y: float = Field(default=0.0, description="Minimum Y coordinate in mm")
    min_z: float = Field(default=0.0, description="Minimum Z coordinate in mm")
    max_x: float = Field(default=0.0, description="Maximum X coordinate in mm")
    max_y: float = Field(default=0.0, description="Maximum Y coordinate in mm")
    max_z: float = Field(default=0.0, description="Maximum Z coordinate in mm")


class GenerateCadIrInput(BaseModel):
    """Input for the generate_cad_ir skill."""

    work_product_id: UUID | None = Field(
        default=None, description="Twin work_product ID (optional for new generation)"
    )
    entities: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "Design IR entities (requirements doc §6.2), e.g. "
            '[{"id": "body1", "op": "create_body"}, '
            '{"id": "sk1", "op": "sketch", "body_ref": "body1", ...}, ...]. '
            "Validated into a real DesignIR document before anything runs; "
            "malformed entities are rejected with a clear error, not a crash."
        ),
    )
    adapter: Literal["freecad", "cadquery"] = Field(
        default="freecad",
        description=(
            "Which Lowering Pass compiles this document: FreeCAD's session-based, "
            "one-call-per-entity pass (default, full v1 op coverage), or CadQuery's "
            "single-script compiler (domain_agents/shared/cadquery_lowering.py, "
            "narrower v1 op subset -- see its module docstring for exact coverage)."
        ),
    )
    material: str = Field(default="aluminum_6061", description="Material name for metadata")
    project_id: str | None = Field(
        default=None,
        description="Project UUID to link the resulting work product to, when committed",
    )
    commit: bool = Field(
        default=True,
        description=(
            "Persist the generated geometry into the Twin via twin.commit_geometry "
            "immediately (best-effort — failure is reported on the output, not raised)"
        ),
    )


class GenerateCadIrOutput(BaseModel):
    """Output from the generate_cad_ir skill."""

    work_product_id: UUID | None = Field(default=None, description="Twin work_product ID")
    cad_file: str = Field(..., description="Path to the exported STEP file")
    entity_count: int = Field(..., ge=0, description="Number of entities lowered")
    volume_mm3: float = Field(..., ge=0, description="Volume in cubic millimeters")
    surface_area_mm2: float = Field(..., ge=0, description="Surface area in square millimeters")
    bounding_box: BoundingBox = Field(
        default_factory=BoundingBox, description="Axis-aligned bounding box"
    )
    obj_id_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Design IR entity id -> the lowering pass's own per-entity handle, for "
            "diagnostics (FreeCAD obj_id, or CadQuery's generated script variable name)"
        ),
    )
    material: str = Field(..., description="Material used")
    committed: bool = Field(
        default=False,
        description="Whether the geometry was persisted into the Twin as a cad_model work product",
    )
    twin_node_id: str | None = Field(
        default=None, description="Twin node ID of the committed cad_model, when committed"
    )
    model_url: str | None = Field(
        default=None, description="Viewer URL of the committed cad_model, when committed"
    )
    commit_error: str | None = Field(
        default=None,
        description="Set when commit=True was requested but persistence was skipped or failed",
    )
