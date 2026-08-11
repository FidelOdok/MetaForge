"""Pydantic response schemas for the Digital Twin viewer endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TwinNodeResponse(BaseModel):
    """Single node in the Digital Twin graph, shaped for the dashboard."""

    id: str
    name: str
    type: str
    domain: str
    status: str
    properties: dict[str, str | int | float | bool]
    updatedAt: str  # noqa: N815


class TwinNodeListResponse(BaseModel):
    """Paginated list of twin nodes."""

    nodes: list[TwinNodeResponse]
    total: int


class TwinRelationshipResponse(BaseModel):
    """A single directed edge in the Digital Twin graph."""

    id: str
    sourceId: str  # noqa: N815
    targetId: str  # noqa: N815
    type: str
    label: str


class TwinRelationshipListResponse(BaseModel):
    """List of edges for the Digital Twin graph."""

    relationships: list[TwinRelationshipResponse]
    total: int


class BooleanCutRequest(BaseModel):
    """Real boolean CSG operation between two committed CAD work products (MET-612)."""

    target_node_id: str = Field(min_length=1)
    cutter_node_id: str = Field(min_length=1)
    operation: Literal["subtract", "union", "intersect"] = "subtract"
    result_name: str | None = None


class BooleanCutResponse(BaseModel):
    """The newly-committed result node of a boolean-cut operation."""

    node: TwinNodeResponse
    operation: str
    result_volume_mm3: float
    result_area_mm2: float
