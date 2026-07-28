"""Request/response schemas for the CAD assembly API (MET-10)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_Kind = Literal["box", "cylinder", "cone", "sphere"]


class Hole(BaseModel):
    """A mounting/fastener hole drilled into a part (local part frame, mm)."""

    x: float = Field(..., description="Hole centre X in the part's local frame (mm).")
    y: float = Field(..., description="Hole centre Y in the part's local frame (mm).")
    diameter: float = Field(..., gt=0, description="Hole diameter (mm).")
    depth: float | None = Field(
        default=None, description="Depth from the top face (mm); through-hole if omitted."
    )


class AssemblyPart(BaseModel):
    """One primitive component of an assembly."""

    name: str = Field(..., min_length=1, description="Human name of the part (STEP PRODUCT).")
    kind: _Kind = Field(..., description="Primitive kind.")
    parameters: dict[str, float] = Field(
        default_factory=dict, description="Dimensions in mm (box: width/length/height; …)."
    )
    position: list[float] | None = Field(
        default=None, description="Optional [x, y, z] placement in mm."
    )
    holes: list[Hole] = Field(
        default_factory=list, description="Mounting/fastener holes drilled into the part."
    )
    fillet: float | None = Field(
        default=None,
        gt=0,
        description=(
            "If set, round every edge of the part by this radius (mm), applied before holes. "
            "Must be smaller than half the part's thinnest dimension or the fillet fails."
        ),
    )


class CreateAssemblyRequest(BaseModel):
    """Author a multi-part assembly deterministically (no LLM) and commit it."""

    name: str = Field(..., min_length=1, description="Assembly name.")
    parts: list[AssemblyPart] = Field(..., min_length=1, description="Parts to author + assemble.")
    project_id: str | None = Field(default=None, description="Project to scope the cad_model to.")


class AssemblyResponse(BaseModel):
    node_id: str
    model_url: str
    minio_object_key: str | None = None
    content_hash: str | None = None
    part_count: int
