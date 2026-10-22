"""Input/output schemas for the form_factor_envelope skill (FORGE-633)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Axis = Literal["x", "y", "z"]


class Component(BaseModel):
    """One thing the product has to hold, as an axis-aligned box.

    ``basis`` says how its size is known: ``measured`` (from a model in the
    twin, e.g. its bounding box), ``datasheet`` (the part's datasheet) or
    ``stated``. ``position_mm`` is its minimum corner in the product's
    frame; give it for every component or for none (then they are stacked).
    """

    name: str = Field(..., min_length=1)
    dims_mm: list[float] = Field(..., min_length=3, max_length=3)
    position_mm: list[float] | None = Field(default=None, min_length=3, max_length=3)
    basis: Literal["measured", "datasheet", "stated"] = "stated"


class SourcedLength(BaseModel):
    value_mm: float = Field(..., ge=0)
    source_ref: str = Field(
        ..., min_length=1, description="design_value id, constraint or citation"
    )


class SourcedEnvelope(BaseModel):
    dims_mm: list[float] = Field(..., min_length=3, max_length=3)
    source_ref: str = Field(..., min_length=1)


class FormFactorEnvelopeInput(BaseModel):
    components: list[Component] = Field(..., min_length=1)
    stack_axis: Axis | None = Field(
        default=None, description="Stack unpositioned components along this axis."
    )
    gap_mm: float = Field(
        default=0.0, ge=0, description="Gap between stacked components (stacking only)."
    )
    clearance: SourcedLength = Field(..., description="Between the contents and the inner wall.")
    wall_thickness: SourcedLength = Field(..., description="Usually the process's minimum wall.")
    max_envelope: SourcedEnvelope | None = Field(
        default=None, description="The largest the product may be (a requirement)."
    )


class FormFactorEnvelopeOutput(BaseModel):
    layout: Literal["positioned", "stacked"]
    contents_dims_mm: list[float]
    inner_dims_mm: list[float]
    outer_dims_mm: list[float]
    outer_volume_cm3: float
    inner_volume_cm3: float
    component_volume_cm3: float
    fill_ratio: float = Field(..., description="Component volume over inner volume.")
    fits: bool | None = Field(
        default=None, description="Against max_envelope in any orientation; None if none given."
    )
    margin_mm: list[float] | None = Field(
        default=None, description="Sorted max minus sorted outer, smallest axis first."
    )
    overlaps: list[str] = Field(
        default_factory=list, description="Positioned components that collide."
    )
    basis: list[str] = Field(default_factory=list, description="How each size is known.")
    sources: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
