"""Input/output schemas for the generate_parametric_feature skill (FORGE-269)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from domain_agents.mechanical.skills.generate_cad_ir.schema import BoundingBox


class BoltPatternParams(BaseModel):
    """A rectangular mounting plate with a circular bolt-hole pattern --
    see ``domain_agents.shared.design_ir_macros.bolt_pattern_entities``."""

    feature_type: Literal["bolt_pattern"] = "bolt_pattern"
    plate_length_mm: float = Field(gt=0)
    plate_width_mm: float = Field(gt=0)
    plate_thickness_mm: float = Field(gt=0)
    hole_diameter_mm: float = Field(gt=0)
    hole_count: int = Field(ge=2, description="Number of holes evenly spaced on the bolt circle")
    pattern_radius_mm: float = Field(gt=0, description="Radius of the bolt circle")


class RibParams(BaseModel):
    """A thin triangular gusset rib -- see
    ``domain_agents.shared.design_ir_macros.rib_entities``."""

    feature_type: Literal["rib"] = "rib"
    length_mm: float = Field(gt=0, description="Base length of the triangular profile")
    height_mm: float = Field(gt=0, description="Rise of the triangular profile")
    thickness_mm: float = Field(gt=0, description="Extrusion thickness")


FeatureParams = Annotated[
    BoltPatternParams | RibParams,
    Field(discriminator="feature_type"),
]


class GenerateParametricFeatureInput(BaseModel):
    """Input for the generate_parametric_feature skill."""

    work_product_id: UUID | None = Field(
        default=None, description="Twin work_product ID (optional for new generation)"
    )
    name: str = Field(
        ...,
        min_length=1,
        description="Name for the generated part (e.g. 'Motor mount bolt pattern').",
    )
    feature: FeatureParams = Field(
        ...,
        description=(
            "Which named feature to generate, and its typed parameters. Each "
            "feature_type picks its own required parameter set (a discriminated "
            "union) -- see BoltPatternParams/RibParams."
        ),
    )
    adapter: Literal["freecad", "cadquery"] = Field(
        default="freecad",
        description=(
            "Which Lowering Pass compiles the generated entities (same choice as "
            "generate_cad_ir). bolt_pattern uses polar_pattern, unsupported by the "
            "CadQuery Lowering Pass -- use 'freecad' (the default) for it."
        ),
    )
    material: str = Field(default="aluminum_6061", description="Material name for metadata")
    project_id: str | None = Field(
        default=None,
        description="Project UUID to link the resulting work product to, when committed",
    )
    commit: bool = Field(
        default=True,
        description="Persist the generated geometry into the Twin via twin.commit_geometry",
    )


class GenerateParametricFeatureOutput(BaseModel):
    """Output from the generate_parametric_feature skill."""

    feature_type: str = Field(..., description="Which named feature was generated")
    work_product_id: UUID | None = Field(default=None, description="Twin work_product ID")
    cad_file: str = Field(..., description="Path to the exported STEP file")
    entity_count: int = Field(..., ge=0, description="Number of Design IR entities lowered")
    volume_mm3: float = Field(..., ge=0, description="Volume in cubic millimeters")
    surface_area_mm2: float = Field(..., ge=0, description="Surface area in square millimeters")
    bounding_box: BoundingBox = Field(
        default_factory=BoundingBox, description="Axis-aligned bounding box"
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
    already_committed: bool = Field(
        default=False,
        description="True when an identical cad_model already existed -- nothing new created",
    )
