"""Request/response schemas for the CAD assembly API (MET-10)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_Kind = Literal["box", "cylinder", "cone", "sphere"]


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
