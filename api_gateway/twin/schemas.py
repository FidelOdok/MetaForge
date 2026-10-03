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
    # FORGE-248: the node's own project scope (MET-491's project_id, None for
    # an unscoped legacy node) -- without this, a client that already listed
    # every node (no ?project_id= filter, the default) has no way to tell
    # which project any given node belongs to, and `forge twin list --json`
    # had nothing to filter or display on.
    projectId: str | None = None  # noqa: N815
    # MET-630: structured geometry parameters/properties (pad_length_mm,
    # volume_mm3, ...) — kept separate from `properties` above, which is
    # scalar-only and shared by every node type. None when the node has
    # no geometry_features metadata (e.g. imported geometry, non-CAD nodes).
    geometryParameters: dict[str, Any] | None = None  # noqa: N815
    # MET-630: whether this node's authoring script is git-versioned and
    # retrievable via GET /nodes/{id}/script (a CAD_SOURCE_SCRIPT node
    # exists and is linked via metadata.script_node_id).
    hasScript: bool = False  # noqa: N815
    # MET-740: a robot_description node's {parts, joints} — the exact shape
    # cadquery.export_urdf_assembly accepts/returns. Kept separate from
    # `properties` (scalar-only) so the dashboard can reconstruct the full
    # Assembly export form from an already-fetched node list, with no extra
    # round trip and no live FreeCAD session required. None for every other
    # node type.
    assembly: dict[str, Any] | None = None
    # FORGE-250: a robot_description node's saved named poses
    # (metadata.poses -- {pose_name: {joint_name: value}}), written via the
    # generic POST /nodes/{id}/iterate revision endpoint's metadata_updates.
    # None for every other node type, or a robot_description with no saved
    # poses yet.
    poses: dict[str, dict[str, float]] | None = None
    # FORGE-305: a simulation_result node's mesh_stats ({num_nodes,
    # num_elements, ...}). The scalar-only `properties` loop above drops it
    # silently, like MET-630's geometry_features before it -- so the one piece
    # of an FEA result that says how much to trust it was the one piece the
    # inspector could not show. None for every other node type, or a result
    # recorded without mesh stats.
    meshStats: dict[str, Any] | None = None  # noqa: N815
    # FORGE-511: an assembly cad_model's part list ([{node_id, name, material,
    # position_bbox_mm}]), from metadata.parts. None for every other node.
    assemblyParts: list[dict[str, Any]] | None = None  # noqa: N815
    # FORGE-293: a technical_drawing node's own structured dimensions/GD&T/
    # surface-finish/inspection data and approval state (same "real data,
    # scalar-only properties loop drops it" shape as meshStats above).
    # None for every other node type.
    technicalDrawing: dict[str, Any] | None = None  # noqa: N815


class AssemblyJoint(BaseModel):
    """One mate/joint between two committed parts (FORGE-271).

    Same shape as ``AssemblyDescription['joints'][n]`` (``types/twin.ts``) /
    the export panel's own ``JointRow`` -- a joint authored here and one
    authored via the URDF/SDF/USD export form are structurally identical.
    """

    name: str = Field(min_length=1)
    type: str
    base: str = Field(min_length=1)
    follower: str = Field(min_length=1)
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    anchor: tuple[float, float, float] = (0.0, 0.0, 0.0)
    limits: dict[str, float] | None = None


class UpdateAssemblyJointsRequest(BaseModel):
    """Body for ``PATCH /nodes/{node_id}/assembly-joints`` -- whole-list replace."""

    joints: list[AssemblyJoint]


class UpdateAssemblyJointsResponse(BaseModel):
    nodeId: str  # noqa: N815 — dashboard contract is camelCase
    assembly: dict[str, Any]


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


class ApproveSketchRequest(BaseModel):
    """Human sign-off on a design_sketch work product (follow-up to MET-740/747)."""

    approved_by: str | None = Field(
        default=None, description="Identifier of the human approving this sketch."
    )


class ApproveSketchResponse(BaseModel):
    """Result of approving a design_sketch — the gate's new state."""

    node_id: str
    approved: bool
    approved_at: str


class ApproveTechnicalDrawingRequest(BaseModel):
    """Human sign-off on a technical_drawing work product (FORGE-293)."""

    approved_by: str | None = Field(
        default=None, description="Identifier of the human approving this drawing."
    )


class ApproveTechnicalDrawingResponse(BaseModel):
    """Result of approving a technical_drawing — the gate's new state."""

    node_id: str
    approved: bool
    approved_at: str


class TechnicalDrawingDimension(BaseModel):
    """One toleranced dimension on a technical_drawing (FORGE-293)."""

    feature: str
    nominal_mm: float
    tolerance_plus_mm: float = 0.0
    tolerance_minus_mm: float = 0.0


class TechnicalDrawingGdtCallout(BaseModel):
    """One GD&T callout (ASME Y14.5 style) on a technical_drawing (FORGE-293)."""

    feature: str
    symbol: str
    tolerance_value_mm: float
    datum_refs: list[str] = Field(default_factory=list)


class TechnicalDrawingSurfaceFinish(BaseModel):
    """One surface-finish requirement on a technical_drawing (FORGE-293)."""

    feature: str
    ra_um: float


class TechnicalDrawingSummary(BaseModel):
    """A real recorded technical_drawing work product, listed for a part."""

    node_id: str
    created_at: str
    name: str
    part_name: str
    dimensions: list[TechnicalDrawingDimension]
    gdt_callouts: list[TechnicalDrawingGdtCallout]
    surface_finishes: list[TechnicalDrawingSurfaceFinish]
    inspection_requirements: list[str]
    approved: bool
    approved_at: str | None = None
    approved_by: str | None = None


class TechnicalDrawingListResponse(BaseModel):
    """A part's real recorded technical drawings, oldest first (FORGE-293)."""

    drawings: list[TechnicalDrawingSummary]
