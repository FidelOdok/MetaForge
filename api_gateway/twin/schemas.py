"""Pydantic response schemas for the Digital Twin viewer endpoints."""

from __future__ import annotations

from typing import Any, Literal

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
    # MET-630: structured geometry parameters/properties (pad_length_mm,
    # volume_mm3, ...) — kept separate from `properties` above, which is
    # scalar-only and shared by every node type. None when the node has
    # no geometry_features metadata (e.g. imported geometry, non-CAD nodes).
    geometryParameters: dict[str, Any] | None = None  # noqa: N815
    # MET-630: whether this node's authoring script is git-versioned and
    # retrievable via GET /nodes/{id}/script (a CAD_SOURCE_SCRIPT node
    # exists and is linked via metadata.script_node_id).
    hasScript: bool = False  # noqa: N815


class TwinNodeScriptResponse(BaseModel):
    """The current generation script text for a CAD_MODEL node (MET-630)."""

    node_id: str
    script_node_id: str
    script_source: str
    git_commit_sha: str | None = None
    git_path: str | None = None


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
