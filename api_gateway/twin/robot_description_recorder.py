"""Robot-description (URDF/SDF/USD) persistence for the CAD-export routes (MET-740).

Closes the "throwaway export" gap: every ``/v1/cad-export/*`` assembly route
used to return files that lived only in a scratch directory
(``_cad_exports/{export_id}/``) and vanished once that request's export_id
was forgotten. The only reusable intermediate state was a live FreeCAD
authoring session — which has a ~30-minute idle TTL, so once it expired the
parts/joints data was gone and had to be hand-retyped into the dashboard's
export form. This mirrors ``geometry_recorder.py``'s pattern (MinIO blob +
structured metadata + project link) for a robot description, with two
differences:

1. **Multi-blob**: a robot description always references one mesh file per
   link (URDF/SDF/USD structurally can't embed a sub-shape — every rigid
   body needs its own file), so this stores the primary description text
   plus every mesh as separate MinIO objects under the same work product,
   recorded in ``metadata["mesh_files"]`` (link_name -> object key). No
   existing "one node, many blobs" precedent exists elsewhere in the
   codebase to follow, so this establishes it directly rather than
   forcing N separate nodes.
2. **No MCP tool**: unlike ``twin.commit_geometry`` (called by a chat agent
   mid-session), robot-description persistence is triggered by a REST route
   the dashboard calls directly after a successful export — there's no
   agent-tool-calling step involved, so this is a plain function called
   straight from ``api_gateway/cad_export/routes.py``, not exposed over MCP.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.robot_description_recorder")

_FORMAT_CONTENT_TYPE = {
    "urdf": "application/xml",
    "xacro": "application/xml",
    "sdf": "application/xml",
    "usd": "application/octet-stream",
    "usda": "text/plain",
}
_MESH_CONTENT_TYPE = {
    "stl": "model/stl",
    "obj": "text/plain",
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "robot")[:60]


def _mesh_content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MESH_CONTENT_TYPE.get(ext, "application/octet-stream")


def _store_blobs(
    wp_id: str, filename: str, content: bytes, ext: str, mesh_files: dict[str, bytes]
) -> tuple[str | None, dict[str, str]]:
    """Store the primary description + every mesh blob; best-effort.

    Returns ``(primary_object_key, {link_name: mesh_object_key})``. A
    storage failure on any blob is logged and skipped — the node is still
    created (mirrors ``geometry_recorder``'s "degrade like /v1/twin/import"
    discipline) rather than losing the whole commit over MinIO being down.
    """
    minio_object_key: str | None = None
    mesh_object_keys: dict[str, str] = {}
    try:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        minio_object_key = store_work_product_blob(
            wp_id, filename, content, content_type=_FORMAT_CONTENT_TYPE.get(ext, "text/plain")
        )
        for link_name, mesh_bytes in mesh_files.items():
            key = store_work_product_blob(
                wp_id, link_name, mesh_bytes, content_type=_mesh_content_type(link_name)
            )
            mesh_object_keys[link_name] = key
    except Exception as exc:  # noqa: BLE001 — degrade like geometry_recorder
        logger.warning("robot_description_blob_store_skipped", wp_id=wp_id, error=str(exc))
    return minio_object_key, mesh_object_keys


def make_robot_description_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``commit(...)`` bound to a twin + project backend."""

    async def commit(
        *,
        name: str,
        description_text: str,
        fmt: str,
        robot_name: str,
        parts: list[dict[str, Any]],
        joints: list[dict[str, Any]],
        mesh_files: dict[str, bytes],
        source_part_node_ids: list[str] | None = None,
        project_id: str | None = None,
        domain: str = "mechanical",
        source_tool: str = "cadquery.export_urdf_assembly",
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("robot_description commit: 'name' is required (non-empty string)")
        content = description_text.encode("utf-8")
        if not content:
            raise ValueError("robot_description commit: 'description_text' is empty")

        with tracer.start_as_current_span("twin.commit_robot_description") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(content).hexdigest()
            ext = fmt.lower().lstrip(".") or "urdf"
            filename = f"{_slug(name)}.{ext}"
            span.set_attribute("robot_description.name", name)
            span.set_attribute("robot_description.format", ext)
            span.set_attribute("robot_description.link_count", len(parts))
            span.set_attribute("robot_description.joint_count", len(joints))

            minio_object_key, mesh_object_keys = _store_blobs(
                str(wp_id), filename, content, ext, mesh_files
            )

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "content_sha256": content_hash,
                "authored_by": source_tool,
                "robot_name": robot_name,
                # MET-740: the exact {parts, joints} shape
                # cadquery.export_urdf_assembly accepts/returns — lets the
                # dashboard reconstruct the full export form from this node
                # alone, no live FreeCAD session required.
                "assembly": {"parts": parts, "joints": joints},
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key
            if mesh_object_keys:
                metadata["mesh_files"] = mesh_object_keys

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.ROBOT_DESCRIPTION,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format=ext,
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=source_tool,
                project_id=project_id,  # pydantic coerces str -> UUID
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            # MET-740: PARENT_OF edges to every source CAD part this
            # description was built from — mirrors the boolean_ops.py
            # precedent (parts -> derived-result edges). Best-effort per
            # edge: one bad node_id must not block the whole commit.
            edge_failures = 0
            for source_id in source_part_node_ids or []:
                try:
                    await twin.add_edge(created.id, source_id, EdgeType.PARENT_OF)
                except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                    edge_failures += 1
                    logger.warning(
                        "robot_description_source_edge_failed",
                        node_id=node_id,
                        source_id=source_id,
                        error=str(exc),
                    )

            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, node_id, name, "robot_description"
                    )
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("robot_description_project_link_failed", error=str(exc))

            logger.info(
                "robot_description_committed",
                node_id=node_id,
                project_id=project_id,
                linked=linked,
                minio_object_key=minio_object_key,
                mesh_count=len(mesh_object_keys),
                source_part_count=len(source_part_node_ids or []),
                edge_failures=edge_failures,
            )
            return {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "format": ext,
                "mesh_files": mesh_object_keys,
                "project_linked": linked,
            }

    return commit


def make_robot_description_updater(twin: Any) -> Any:
    """Return an async ``update(node_id, ...)`` bound to a twin.

    ``/nodes/{id}/iterate`` (``version_service.py``) only versions
    **metadata** — it never touches the underlying blob or ``content_hash``.
    Editing a robot description's joints changes the actual URDF/SDF/USD
    text, so the blob has to be replaced *and* the metadata snapshot
    recorded together, which this pairs into one call using the same
    ``VersionService`` machinery ``/nodes/{id}/iterate`` uses.
    """

    async def update(
        node_id: str,
        *,
        description_text: str,
        fmt: str,
        robot_name: str,
        parts: list[dict[str, Any]],
        joints: list[dict[str, Any]],
        mesh_files: dict[str, bytes],
        change_description: str = "re-exported with edited assembly",
    ) -> dict[str, Any]:
        from uuid import UUID as _UUID

        from api_gateway.twin.version_service import VersionService

        with tracer.start_as_current_span("twin.update_robot_description") as span:
            span.set_attribute("robot_description.node_id", node_id)
            uid = _UUID(node_id)
            wp = await twin.get_work_product(uid)
            if wp is None:
                raise ValueError(f"robot_description update: node {node_id} not found")

            content = description_text.encode("utf-8")
            if not content:
                raise ValueError("robot_description update: 'description_text' is empty")
            content_hash = hashlib.sha256(content).hexdigest()
            ext = fmt.lower().lstrip(".") or "urdf"
            filename = str(wp.metadata.get("original_filename") or f"{_slug(wp.name)}.{ext}")

            minio_object_key, mesh_object_keys = _store_blobs(
                node_id, filename, content, ext, mesh_files
            )

            updated_meta = dict(wp.metadata)
            updated_meta["content_sha256"] = content_hash
            updated_meta["robot_name"] = robot_name
            updated_meta["assembly"] = {"parts": parts, "joints": joints}
            if minio_object_key:
                updated_meta["minio_object_key"] = minio_object_key
            if mesh_object_keys:
                # Merge rather than replace: an edit that drops a link's
                # mesh from this export shouldn't silently orphan its
                # still-referenced-elsewhere blob key.
                updated_meta["mesh_files"] = {
                    **(wp.metadata.get("mesh_files") or {}),
                    **mesh_object_keys,
                }

            # build_revision snapshots wp.content_hash, which at this point
            # is still the PRE-update hash (mirrors a pre-existing gap in
            # /nodes/{id}/iterate, which never changes the blob at all).
            # Since this update DOES replace the blob, correct the
            # revision's own content_hash to the new one so the version
            # history stays accurate for what a re-exported robot
            # description actually needs — a true blob+metadata revision.
            revision = VersionService.build_revision(
                wp, change_description, snapshot_override=updated_meta
            )
            revision["content_hash"] = content_hash
            final_meta = VersionService.append_to_metadata(updated_meta, revision)
            await twin.update_work_product(
                uid,
                {
                    "metadata": final_meta,
                    "content_hash": content_hash,
                    "updated_at": datetime.now(UTC),
                },
            )

            logger.info(
                "robot_description_updated",
                node_id=node_id,
                minio_object_key=minio_object_key,
                mesh_count=len(mesh_object_keys),
                revision_number=revision.get("revision"),
            )
            return {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "format": ext,
                "mesh_files": updated_meta.get("mesh_files", {}),
                "revision": revision,
            }

    return update
