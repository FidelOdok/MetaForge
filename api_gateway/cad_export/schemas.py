"""Request/response schemas for exporting CAD geometry to robotics-sim
formats — URDF, SDF, USD, and ROS2 launch files (MET-719).

Every request references a CAD part by its Twin work-product node id, never a
raw filesystem path: the dashboard has no access to the adapter workspace, so
the route resolves each node id to a real STEP file via
``twin.stage_work_product_file`` before calling the underlying
``cadquery.export_*`` tool. Output is treated as a throwaway derived artifact
(mirrors ``/v1/convert``'s GLB) rather than a versioned Twin work product —
see MET-719's open design question.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MeshFormat = Literal["stl", "obj"]
JointType = Literal["fixed", "slider", "revolute", "cylindrical", "ball"]


class ExportFile(BaseModel):
    """A generated file, fetched via ``GET /v1/cad-export/download/{export_id}/{filename}``."""

    filename: str
    download_url: str


class JointSpec(BaseModel):
    """One joint between two parts — the same shape FreeCAD's
    ``add_assembly_joint``/``list_joints`` already produce, so a caller can
    pass through what MET-721 reads from a live session, or supply a saved
    joint list manually."""

    name: str = Field(..., min_length=1)
    type: JointType
    base: str = Field(..., description="Link name of the base part.")
    follower: str = Field(..., description="Link name of the follower part.")
    axis: list[float] = Field(..., min_length=3, max_length=3)
    anchor: list[float] = Field(..., min_length=3, max_length=3, description="Anchor point (mm).")
    limits: dict[str, float] | None = Field(
        default=None,
        description="Required for 'slider' joints — {lower, upper, effort?, velocity?}.",
    )


class PartRef(BaseModel):
    """One part of an assembly, referencing a Twin STEP work product."""

    node_id: str = Field(..., min_length=1, description="Twin work-product node id (STEP file).")
    link_name: str = Field(..., min_length=1)
    material: str | None = None
    density_kg_m3: float | None = None


# ---------------------------------------------------------------------------
# Single-part requests
# ---------------------------------------------------------------------------


class UrdfExportRequest(BaseModel):
    node_id: str = Field(..., min_length=1, description="Twin work-product node id (STEP file).")
    link_name: str = Field(default="base_link")
    material: str | None = None
    density_kg_m3: float | None = None
    mesh_format: MeshFormat = "stl"
    mesh_uri_prefix: str = ""
    xacro: bool = False


class SdfExportRequest(BaseModel):
    node_id: str = Field(..., min_length=1, description="Twin work-product node id (STEP file).")
    model_name: str = Field(default="model")
    link_name: str = Field(default="link")
    material: str | None = None
    density_kg_m3: float | None = None
    mesh_format: MeshFormat = "stl"
    static: bool = False
    world_name: str | None = None


class UsdExportRequest(BaseModel):
    node_id: str = Field(..., min_length=1, description="Twin work-product node id (STEP file).")
    prim_name: str = Field(default="model")
    material: str | None = None
    density_kg_m3: float | None = None


# ---------------------------------------------------------------------------
# Assembly requests
# ---------------------------------------------------------------------------


class UrdfAssemblyExportRequest(BaseModel):
    parts: list[PartRef] = Field(..., min_length=1)
    joints: list[JointSpec] = Field(default_factory=list)
    robot_name: str = "robot"
    mesh_format: MeshFormat = "stl"
    mesh_uri_prefix: str = ""
    xacro: bool = False


class SdfAssemblyExportRequest(BaseModel):
    parts: list[PartRef] = Field(..., min_length=1)
    joints: list[JointSpec] = Field(default_factory=list)
    model_name: str = "model"
    mesh_format: MeshFormat = "stl"
    static: bool = False
    world_name: str | None = None


class UsdAssemblyExportRequest(BaseModel):
    parts: list[PartRef] = Field(..., min_length=1)
    joints: list[JointSpec] = Field(default_factory=list)
    robot_name: str = "robot"


class Ros2LaunchRequest(BaseModel):
    robot_name: str = Field(..., min_length=1)
    default_urdf_path: str = Field(
        ...,
        min_length=1,
        description=(
            "Default 'urdf_path' launch argument — e.g. a just-exported URDF's download_url."
        ),
    )
    include_joint_state_publisher_gui: bool = True
    include_rviz: bool = True


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class UrdfExportResponse(BaseModel):
    output_file: ExportFile
    mesh_file: ExportFile
    link_name: str
    density_kg_m3: float
    mass_kg: float
    center_of_mass_m: dict[str, float]
    inertia_kgm2: dict[str, float]


class SdfExportResponse(BaseModel):
    output_file: ExportFile
    mesh_file: ExportFile
    model_name: str
    link_name: str
    density_kg_m3: float
    mass_kg: float
    center_of_mass_m: dict[str, float]
    inertia_kgm2: dict[str, float]


class UsdExportResponse(BaseModel):
    output_file: ExportFile
    mesh_file: ExportFile
    prim_name: str
    triangle_count: int
    density_kg_m3: float
    mass_kg: float
    center_of_mass_m: dict[str, float]
    inertia_kgm2: dict[str, float]


class UrdfAssemblyExportResponse(BaseModel):
    output_file: ExportFile
    mesh_files: list[ExportFile]
    robot_name: str
    link_names: list[str]
    joint_names: list[str]


class SdfAssemblyExportResponse(BaseModel):
    output_file: ExportFile
    mesh_files: list[ExportFile]
    model_name: str
    link_names: list[str]
    joint_names: list[str]


class UsdAssemblyExportResponse(BaseModel):
    output_file: ExportFile
    mesh_files: list[ExportFile]
    robot_name: str
    link_names: list[str]
    joint_names: list[str]


class Ros2LaunchResponse(BaseModel):
    output_file: ExportFile
    robot_name: str
    default_urdf_path: str


# ---------------------------------------------------------------------------
# Session introspection (MET-721) — read-only lookups against a LIVE FreeCAD
# authoring session, for a "reuse joints an agent already recorded via chat"
# picker. Joints are never persisted anywhere durable (unlike geometry, which
# survives a session closing via the committed STEP blob) — this only works
# while the session that recorded them is still open (default 30 min idle
# TTL, see tool_registry/tools/freecad/config.py). There is no lookup by
# Twin/assembly node id; the caller must already have the session_id.
# ---------------------------------------------------------------------------


class SessionObject(BaseModel):
    obj_id: str
    kind: str
    name: str
    order: int


class SessionSummary(BaseModel):
    session_id: str
    name: str
    object_count: int
    objects: list[SessionObject]


class SessionJoint(BaseModel):
    name: str
    type: JointType
    base: str
    follower: str
    axis: list[float]
    anchor: list[float]


class SessionJointsResponse(BaseModel):
    joints: list[SessionJoint]
