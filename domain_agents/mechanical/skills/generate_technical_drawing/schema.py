"""Input/output schemas for the generate_technical_drawing skill."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class DrawingDimension(BaseModel):
    """A single toleranced dimension."""

    feature: str = Field(..., min_length=1)
    nominal_mm: float = Field(..., description="Nominal value in mm")
    tolerance_plus_mm: float = Field(default=0.0)
    tolerance_minus_mm: float = Field(default=0.0)


class GdtCallout(BaseModel):
    """A single GD&T callout (ASME Y14.5 style)."""

    feature: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1, description="e.g. flatness, position, concentricity")
    tolerance_value_mm: float = Field(...)
    datum_refs: list[str] = Field(default_factory=list)


class SurfaceFinish(BaseModel):
    """A surface-finish requirement for one feature."""

    feature: str = Field(..., min_length=1)
    ra_um: float = Field(..., gt=0, description="Roughness average in micrometers")


class GenerateTechnicalDrawingInput(BaseModel):
    """Input for the technical-drawing skill."""

    work_product_id: UUID = Field(
        ..., description="Source CAD_MODEL node id this drawing documents"
    )
    part_name: str = Field(..., min_length=1)
    dimensions: list[DrawingDimension] = Field(..., min_length=1)
    gdt_callouts: list[GdtCallout] = Field(default_factory=list)
    surface_finishes: list[SurfaceFinish] = Field(default_factory=list)
    inspection_requirements: list[str] = Field(default_factory=list)
    project_id: str | None = Field(default=None)


class GenerateTechnicalDrawingOutput(BaseModel):
    """Output from the technical-drawing skill."""

    node_id: str = Field(..., description="TECHNICAL_DRAWING work-product node id")
    dimension_count: int = Field(..., ge=0)
    gdt_callout_count: int = Field(..., ge=0)
    surface_finish_count: int = Field(..., ge=0)
