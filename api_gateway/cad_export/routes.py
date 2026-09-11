"""Gateway routes for exporting CAD geometry to robotics-sim formats
(URDF/SDF/USD/ROS2 launch) — MET-719.

The dashboard has no path to invoke an MCP tool with structured params today
(the chat harness is the only existing route). This module fills that gap for
exactly the 7 ``cadquery.export_*``/``generate_ros2_launch`` tools needed by
the export panels (MET-720/721) — it is deliberately NOT a generic "call any
MCP tool" runner (that's a separate, unscoped v2 idea per
``docs/dashboard-tour.md``).

Output files are written under a per-export directory in the shared adapter
workspace (same volume every adapter container mounts — see
``api_gateway/twin/blob_stager.py`` for the sibling pattern) and served back
via a plain download route, mirroring how ``/v1/convert`` serves its GLB.

MET-740: the three assembly-export routes (urdf/sdf/usd) ALSO commit their
output as a real, versioned ``robot_description`` Twin work product by
default (``persist=true``) — resolving MET-719's original "open design
question" in favor of persisting. The single-part export routes and
``generate_ros2_launch`` remain throwaway-only; a robot description is
specifically the multi-part case this was built to stop losing.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api_gateway.cad_export.schemas import (
    ExportFile,
    JointSpec,
    PartRef,
    Ros2LaunchRequest,
    Ros2LaunchResponse,
    SdfAssemblyExportRequest,
    SdfAssemblyExportResponse,
    SdfExportRequest,
    SdfExportResponse,
    SessionJointsResponse,
    SessionSummary,
    UrdfAssemblyExportRequest,
    UrdfAssemblyExportResponse,
    UrdfExportRequest,
    UrdfExportResponse,
    UsdAssemblyExportRequest,
    UsdAssemblyExportResponse,
    UsdExportRequest,
    UsdExportResponse,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge, McpToolError

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.cad_export")

router = APIRouter(prefix="/v1/cad-export", tags=["cad-export"])

_EXPORTS_SUBDIR = "_cad_exports"

# MET-740: injected async commit(...)/update(...) callables (built in
# server.py over the same TwinAPI + project backend geometry_recorder uses).
# None until init_robot_description_recorder() runs at startup — every
# assembly-export route treats that as "persistence unavailable", degrading
# to the pre-MET-740 throwaway-only behavior rather than failing the export.
_robot_description_commit: Any = None
_robot_description_update: Any = None


def init_robot_description_recorder(commit_fn: Any, update_fn: Any) -> None:
    """Wire the robot-description persistence callables (called from server.py)."""
    global _robot_description_commit, _robot_description_update  # noqa: PLW0603
    _robot_description_commit = commit_fn
    _robot_description_update = update_fn


def _workspace_root() -> Path:
    return Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))


def _export_dir(export_id: str) -> Path:
    return _workspace_root() / _EXPORTS_SUBDIR / export_id


def _make_export_dir(export_id: str) -> Path:
    """Create (and return) a fresh export directory that the *adapter*
    container can write into, not just this gateway process.

    ``adapter-workspace`` is a Docker volume shared between the gateway
    (which creates this directory) and the cadquery adapter (which writes
    the actual export files into it) — two different containers running as
    different, unrelated UIDs (no shared group). ``Path.mkdir`` alone
    leaves the directory owned by whichever UID the gateway runs as with
    mode narrowed by umask, which the adapter's UID then can't write into
    (``PermissionError``). ``chmod`` isn't subject to umask, so it's used
    here to force the directory world-writable after creation — acceptable
    because this is a throwaway per-export scratch directory, not
    security-sensitive content (see this module's docstring).
    """
    out_dir = _export_dir(export_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o777)
    return out_dir


def _export_file(export_id: str, path: str) -> ExportFile:
    filename = Path(path).name
    download_url = f"/v1/cad-export/download/{export_id}/{filename}"
    return ExportFile(filename=filename, download_url=download_url)


def _unwrap(envelope: Any, tool_id: str) -> dict[str, Any]:
    """Unwrap an MCP result envelope, raising a clean 502 on a tool-side error.

    The real bridge already returns the tool's inner data dict directly; test
    doubles typically wrap it as ``{"status": "ok"/"error", "data": {...}}``.
    Handles both, matching ``api_gateway/cad/builder.py``'s ``_data()``.
    """
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        err = envelope.get("error") or envelope
        raise HTTPException(status_code=502, detail=f"{tool_id} failed: {err}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


async def _invoke(bridge: McpBridge, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
    with tracer.start_as_current_span("cad_export.invoke") as span:
        span.set_attribute("cad_export.tool_id", tool_id)
        try:
            envelope = await bridge.invoke(tool_id, params)
        except McpToolError as exc:
            logger.warning("cad_export_tool_failed", tool_id=tool_id, error=exc.details)
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"{tool_id} failed: {exc.details}") from exc
        except Exception as exc:  # noqa: BLE001 — surface a clean 502 with the cause
            logger.warning("cad_export_tool_failed", tool_id=tool_id, error=str(exc))
            span.record_exception(exc)
            raise HTTPException(status_code=502, detail=f"{tool_id} failed: {exc}") from exc
        data = _unwrap(envelope, tool_id)
        logger.info("cad_export_tool_succeeded", tool_id=tool_id)
        return data


async def _stage_part(bridge: McpBridge, node_id: str) -> str:
    """Resolve a Twin work-product node id to a real STEP file path."""
    data = await _invoke(bridge, "twin.stage_work_product_file", {"node_id": node_id})
    file_path = data.get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail=f"work product {node_id} has no stored file")
    return str(file_path)


async def _stage_parts(bridge: McpBridge, parts: list[PartRef]) -> list[dict[str, Any]]:
    staged: list[dict[str, Any]] = []
    for part in parts:
        file_path = await _stage_part(bridge, part.node_id)
        entry: dict[str, Any] = {"input_file": file_path, "link_name": part.link_name}
        if part.material:
            entry["material"] = part.material
        if part.density_kg_m3 is not None:
            entry["density_kg_m3"] = part.density_kg_m3
        staged.append(entry)
    return staged


def _joint_dicts(joints: list[JointSpec]) -> list[dict[str, Any]]:
    return [j.model_dump(exclude_none=True) for j in joints]


async def _export_single(
    bridge: McpBridge, tool_id: str, node_id: str, ext: str, **params: Any
) -> tuple[str, dict[str, Any]]:
    """Stage a single part and call a single-part cadquery export tool."""
    file_path = await _stage_part(bridge, node_id)
    export_id = uuid4().hex
    out_dir = _make_export_dir(export_id)
    output_path = str(out_dir / f"model.{ext}")
    args = {"input_file": file_path, "output_path": output_path, **params}
    data = await _invoke(bridge, tool_id, args)
    return export_id, data


async def _export_assembly(
    bridge: McpBridge,
    tool_id: str,
    parts: list[PartRef],
    joints: list[JointSpec],
    ext: str,
    **params: Any,
) -> tuple[str, dict[str, Any]]:
    """Stage every part and call an assembly cadquery export tool."""
    staged_parts = await _stage_parts(bridge, parts)
    export_id = uuid4().hex
    out_dir = _make_export_dir(export_id)
    output_path = str(out_dir / f"model.{ext}")
    args = {
        "parts": staged_parts,
        "joints": _joint_dicts(joints),
        "output_path": output_path,
        **params,
    }
    data = await _invoke(bridge, tool_id, args)
    return export_id, data


async def _persist_robot_description(
    *,
    ext: str,
    default_name: str,
    robot_name: str,
    parts: list[PartRef],
    joints: list[JointSpec],
    output_path: str,
    mesh_paths: list[str],
    persist: bool,
    persist_name: str | None,
    update_node_id: str | None,
    project_id: str | None,
    source_tool: str,
) -> str | None:
    """Commit (or update) a just-exported assembly as a robot_description
    Twin work product (MET-740). Best-effort: a persistence failure logs a
    warning and returns None — it must never fail the export itself, since
    the caller already has real, useful files on disk regardless.
    """
    if not persist or _robot_description_commit is None:
        return None
    try:
        description_text = Path(output_path).read_text(encoding="utf-8")
        mesh_files = {Path(p).name: Path(p).read_bytes() for p in mesh_paths}
        parts_meta = [{"node_id": p.node_id, "link_name": p.link_name} for p in parts]
        joints_meta = _joint_dicts(joints)
        name = persist_name or f"{robot_name} robot description"
        if update_node_id:
            result = await _robot_description_update(
                update_node_id,
                description_text=description_text,
                fmt=ext,
                robot_name=robot_name,
                parts=parts_meta,
                joints=joints_meta,
                mesh_files=mesh_files,
            )
        else:
            result = await _robot_description_commit(
                name=name,
                description_text=description_text,
                fmt=ext,
                robot_name=robot_name,
                parts=parts_meta,
                joints=joints_meta,
                mesh_files=mesh_files,
                source_part_node_ids=[p.node_id for p in parts],
                project_id=project_id,
                source_tool=source_tool,
            )
        return str(result["node_id"])
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.warning(
            "robot_description_persist_failed",
            default_name=default_name,
            update_node_id=update_node_id,
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# Single-part export
# ---------------------------------------------------------------------------


@router.post("/urdf", response_model=UrdfExportResponse, status_code=201)
async def export_urdf(body: UrdfExportRequest) -> UrdfExportResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    ext = "xacro" if body.xacro else "urdf"
    export_id, data = await _export_single(
        bridge,
        "cadquery.export_urdf",
        body.node_id,
        ext,
        link_name=body.link_name,
        material=body.material or "",
        density_kg_m3=body.density_kg_m3,
        mesh_format=body.mesh_format,
        mesh_uri_prefix=body.mesh_uri_prefix,
        xacro=body.xacro,
    )
    return UrdfExportResponse(
        output_file=_export_file(export_id, data["output_file"]),
        mesh_file=_export_file(export_id, data["mesh_file"]),
        link_name=data["link_name"],
        density_kg_m3=data["density_kg_m3"],
        mass_kg=data["mass_kg"],
        center_of_mass_m=data["center_of_mass_m"],
        inertia_kgm2=data["inertia_kgm2"],
    )


@router.post("/sdf", response_model=SdfExportResponse, status_code=201)
async def export_sdf(body: SdfExportRequest) -> SdfExportResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    export_id, data = await _export_single(
        bridge,
        "cadquery.export_sdf",
        body.node_id,
        "sdf" if not body.world_name else "world",
        model_name=body.model_name,
        link_name=body.link_name,
        material=body.material or "",
        density_kg_m3=body.density_kg_m3,
        mesh_format=body.mesh_format,
        static=body.static,
        world_name=body.world_name or "",
    )
    return SdfExportResponse(
        output_file=_export_file(export_id, data["output_file"]),
        mesh_file=_export_file(export_id, data["mesh_file"]),
        model_name=data["model_name"],
        link_name=data["link_name"],
        density_kg_m3=data["density_kg_m3"],
        mass_kg=data["mass_kg"],
        center_of_mass_m=data["center_of_mass_m"],
        inertia_kgm2=data["inertia_kgm2"],
    )


@router.post("/usd", response_model=UsdExportResponse, status_code=201)
async def export_usd(body: UsdExportRequest) -> UsdExportResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    export_id, data = await _export_single(
        bridge,
        "cadquery.export_usd",
        body.node_id,
        "usda",
        prim_name=body.prim_name,
        material=body.material or "",
        density_kg_m3=body.density_kg_m3,
    )
    return UsdExportResponse(
        output_file=_export_file(export_id, data["output_file"]),
        mesh_file=_export_file(export_id, data["mesh_file"]),
        prim_name=data["prim_name"],
        triangle_count=data["triangle_count"],
        density_kg_m3=data["density_kg_m3"],
        mass_kg=data["mass_kg"],
        center_of_mass_m=data["center_of_mass_m"],
        inertia_kgm2=data["inertia_kgm2"],
    )


# ---------------------------------------------------------------------------
# Assembly export
# ---------------------------------------------------------------------------


@router.post("/urdf-assembly", response_model=UrdfAssemblyExportResponse, status_code=201)
async def export_urdf_assembly(body: UrdfAssemblyExportRequest) -> UrdfAssemblyExportResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    ext = "xacro" if body.xacro else "urdf"
    export_id, data = await _export_assembly(
        bridge,
        "cadquery.export_urdf_assembly",
        body.parts,
        body.joints,
        ext,
        robot_name=body.robot_name,
        mesh_format=body.mesh_format,
        mesh_uri_prefix=body.mesh_uri_prefix,
        xacro=body.xacro,
    )
    node_id = await _persist_robot_description(
        ext=ext,
        default_name=body.robot_name,
        robot_name=data["robot_name"],
        parts=body.parts,
        joints=body.joints,
        output_path=data["output_file"],
        mesh_paths=data["mesh_files"],
        persist=body.persist,
        persist_name=body.persist_name,
        update_node_id=body.update_node_id,
        project_id=body.project_id,
        source_tool="cadquery.export_urdf_assembly",
    )
    return UrdfAssemblyExportResponse(
        output_file=_export_file(export_id, data["output_file"]),
        mesh_files=[_export_file(export_id, p) for p in data["mesh_files"]],
        robot_name=data["robot_name"],
        link_names=data["link_names"],
        joint_names=data["joint_names"],
        robot_description_node_id=node_id,
    )


@router.post("/sdf-assembly", response_model=SdfAssemblyExportResponse, status_code=201)
async def export_sdf_assembly(body: SdfAssemblyExportRequest) -> SdfAssemblyExportResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    ext = "sdf" if not body.world_name else "world"
    export_id, data = await _export_assembly(
        bridge,
        "cadquery.export_sdf_assembly",
        body.parts,
        body.joints,
        ext,
        model_name=body.model_name,
        mesh_format=body.mesh_format,
        static=body.static,
        world_name=body.world_name or "",
    )
    node_id = await _persist_robot_description(
        ext="sdf",
        default_name=body.model_name,
        robot_name=data["model_name"],
        parts=body.parts,
        joints=body.joints,
        output_path=data["output_file"],
        mesh_paths=data["mesh_files"],
        persist=body.persist,
        persist_name=body.persist_name,
        update_node_id=body.update_node_id,
        project_id=body.project_id,
        source_tool="cadquery.export_sdf_assembly",
    )
    return SdfAssemblyExportResponse(
        output_file=_export_file(export_id, data["output_file"]),
        mesh_files=[_export_file(export_id, p) for p in data["mesh_files"]],
        model_name=data["model_name"],
        link_names=data["link_names"],
        joint_names=data["joint_names"],
        robot_description_node_id=node_id,
    )


@router.post("/usd-assembly", response_model=UsdAssemblyExportResponse, status_code=201)
async def export_usd_assembly(body: UsdAssemblyExportRequest) -> UsdAssemblyExportResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    export_id, data = await _export_assembly(
        bridge,
        "cadquery.export_usd_assembly",
        body.parts,
        body.joints,
        "usda",
        robot_name=body.robot_name,
    )
    node_id = await _persist_robot_description(
        ext="usda",
        default_name=body.robot_name,
        robot_name=data["robot_name"],
        parts=body.parts,
        joints=body.joints,
        output_path=data["output_file"],
        mesh_paths=data["mesh_files"],
        persist=body.persist,
        persist_name=body.persist_name,
        update_node_id=body.update_node_id,
        project_id=body.project_id,
        source_tool="cadquery.export_usd_assembly",
    )
    return UsdAssemblyExportResponse(
        output_file=_export_file(export_id, data["output_file"]),
        mesh_files=[_export_file(export_id, p) for p in data["mesh_files"]],
        robot_name=data["robot_name"],
        link_names=data["link_names"],
        joint_names=data["joint_names"],
        robot_description_node_id=node_id,
    )


# ---------------------------------------------------------------------------
# ROS2 launch (text-only — no geometry/staging involved)
# ---------------------------------------------------------------------------


@router.post("/ros2-launch", response_model=Ros2LaunchResponse, status_code=201)
async def generate_ros2_launch(body: Ros2LaunchRequest) -> Ros2LaunchResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    export_id = uuid4().hex
    out_dir = _make_export_dir(export_id)
    output_path = str(out_dir / f"{body.robot_name}.launch.py")
    data = await _invoke(
        bridge,
        "cadquery.generate_ros2_launch",
        {
            "robot_name": body.robot_name,
            "default_urdf_path": body.default_urdf_path,
            "output_path": output_path,
            "include_joint_state_publisher_gui": body.include_joint_state_publisher_gui,
            "include_rviz": body.include_rviz,
        },
    )
    return Ros2LaunchResponse(
        output_file=_export_file(export_id, data["output_file"]),
        robot_name=data["robot_name"],
        default_urdf_path=data["default_urdf_path"],
    )


# ---------------------------------------------------------------------------
# Session introspection (MET-721) — read-only, for a "reuse joints an agent
# already recorded via chat" picker. Only works while the session that
# recorded them is still open (default 30 min idle TTL); there is no lookup
# by Twin/assembly node id, and no persistence for joints at all -- the
# caller must already have the session_id (e.g. echoed by a chat turn).
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}", response_model=SessionSummary)
async def get_session_summary(session_id: str) -> SessionSummary:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    data = await _invoke(bridge, "freecad.describe_session", {"session_id": session_id})
    return SessionSummary(**data)


@router.get("/sessions/{session_id}/joints", response_model=SessionJointsResponse)
async def get_session_joints(session_id: str) -> SessionJointsResponse:
    from api_gateway.chat.routes import get_mcp_bridge

    bridge = get_mcp_bridge()
    data = await _invoke(bridge, "freecad.list_joints", {"session_id": session_id})
    return SessionJointsResponse(**data)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


@router.get("/download/{export_id}/{filename}")
async def download_export_file(export_id: str, filename: str) -> FileResponse:
    try:
        uuid.UUID(hex=export_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid export id") from exc
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid filename")

    file_path = _export_dir(export_id) / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="export file not found")
    return FileResponse(path=str(file_path), filename=filename)
