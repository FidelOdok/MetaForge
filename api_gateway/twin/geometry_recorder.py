"""Authored-geometry recorder for twin.commit_geometry (MET-529).

Closes the MCP-authored-geometry persistence gap. The FreeCAD adapter authors a
solid headless and returns the STEP **bytes** (base64) — but the adapter lives in
``tool_registry`` (Layer 3) and must not import ``digital_twin`` / ``twin_core``,
and on the containerized path it cannot reach MinIO or the twin at all. So
persistence lives here, in the api_gateway layer, and is injected into the twin
MCP adapter as an opaque async callable — exactly like ``make_decision_recorder``
(MET-495). One call does all three persistence facets:

1. store the STEP blob in MinIO (graceful — node still created on failure),
2. create a validated CAD ``WorkProduct`` with ``content_hash`` +
   ``metadata["minio_object_key"]`` so ``GET /v1/twin/nodes/{id}/model`` resolves
   it (MinIO-first) and the OCCT converter renders it as GLB in the viewer,
3. link it to its project so it shows on the Projects page.

The resulting node is loadable by the existing viewer path with zero extra work.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.geometry_recorder")

_EXT_CONTENT_TYPE = {
    "step": "application/step",
    "stp": "application/step",
    "stl": "model/stl",
    "iges": "application/iges",
    "igs": "application/iges",
    "brep": "application/octet-stream",
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "geometry")[:60]


def make_geometry_recorder(twin: Any, project_backend: Any = None, git_registry: Any = None) -> Any:
    """Return an async ``record(...)`` bound to a twin + project backend.

    The returned callable is what ``twin.commit_geometry`` invokes; binding the
    dependencies here keeps the MCP adapter free of api_gateway/twin_core imports.

    Args:
        git_registry: Optional ``GitRepoRegistry`` (MET-630). When given and
            the caller supplies ``script_source``, the generation script is
            committed to that project's real git repo (for genuine diffing)
            and linked to the resulting CAD_MODEL node as its provenance.
    """

    async def record(
        *,
        step_base64: str,
        name: str,
        project_id: str | None = None,
        session_id: str | None = None,
        domain: str = "mechanical",
        fmt: str = "step",
        source_tool: str = "freecad.export_model",
        extra_metadata: dict[str, Any] | None = None,
        script_source: str | None = None,
        parameters: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("twin.commit_geometry: 'name' is required (non-empty string)")
        try:
            content = base64.b64decode(step_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("twin.commit_geometry: 'step_base64' is not valid base64") from exc
        if not content:
            raise ValueError("twin.commit_geometry: decoded geometry is empty")

        with tracer.start_as_current_span("twin.commit_geometry") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(content).hexdigest()
            ext = fmt.lower().lstrip(".") or "step"
            filename = f"{_slug(name)}.{ext}"
            span.set_attribute("geometry.name", name)
            span.set_attribute("geometry.size_bytes", len(content))

            # 1. blob → MinIO (graceful: keep the node even if storage is down).
            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id),
                    filename,
                    content,
                    content_type=_EXT_CONTENT_TYPE.get(ext, "application/octet-stream"),
                )
            except Exception as exc:  # noqa: BLE001 — degrade like /v1/twin/import
                logger.warning("geometry_blob_store_skipped", name=name, error=str(exc))

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "content_sha256": content_hash,
                "authored_by": source_tool,
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key
            if session_id:
                metadata["session_id"] = session_id
            if extra_metadata:
                metadata.update(extra_metadata)
            # MET-630: structured, queryable geometry semantics — separate
            # from the git-versioned script text below. Parameters are the
            # values that drove generation (e.g. pad length, hole diameter);
            # properties are derived measurements (volume, bounding box,
            # mass properties). Both live in the graph so twin_query_cypher
            # / constraint evaluation can reason over them directly.
            if parameters or properties:
                metadata["geometry_features"] = {
                    "parameters": parameters or {},
                    "properties": properties or {},
                }

            # MET-630: commit the real generation script to the project's
            # git repo — the working, diffable source of truth — as its own
            # CAD_SOURCE_SCRIPT node, linked to the STEP node it produced.
            # Best-effort: a script-commit failure must never block the
            # STEP node itself from being created.
            script_node_id: str | None = None
            git_commit_sha: str | None = None
            if script_source and git_registry is not None:
                try:
                    engine = git_registry.for_project(project_id)
                    try:
                        await engine.create_branch("main")
                    except ValueError:
                        pass  # branch already exists — fine
                    script_hash = hashlib.sha256(script_source.encode()).hexdigest()
                    script_path = f"mechanical/cad_src/{_slug(name)}.py"
                    script_wp = WorkProduct(
                        name=f"{name} (script)",
                        type=WorkProductType.CAD_SOURCE_SCRIPT,
                        domain=domain,
                        file_path=script_path,
                        content_hash=script_hash,
                        format="py",
                        metadata={"source_tool": source_tool},
                        created_by=source_tool,
                        project_id=project_id,
                    )
                    created_script = await twin.create_work_product(script_wp)
                    script_id = getattr(created_script, "id", script_wp.id)
                    script_version = await engine.commit(
                        "main",
                        f"author {name}",
                        [script_id],
                        source_tool,
                        content={script_id: script_source.encode()},
                    )
                    script_node_id = str(script_id)
                    git_commit_sha = script_version.git_commit_sha
                    metadata["git_commit_sha"] = git_commit_sha
                    metadata["git_path"] = script_path
                    metadata["script_node_id"] = script_node_id
                except Exception as exc:  # noqa: BLE001 — script commit is best-effort
                    logger.warning("geometry_script_commit_failed", name=name, error=str(exc))

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.CAD_MODEL,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format=ext,
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=source_tool,
                project_id=project_id,  # pydantic coerces str → UUID
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            if script_node_id is not None:
                try:
                    script_id = getattr(created_script, "id")
                    await twin.add_edge(script_id, created.id, EdgeType.PARENT_OF)
                except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                    logger.warning("geometry_script_edge_failed", node_id=node_id, error=str(exc))

            # 2. project junction link so it shows on the Projects page.
            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, name, "cad_model")
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("geometry_project_link_failed", error=str(exc))

            logger.info(
                "geometry_committed",
                node_id=node_id,
                project_id=project_id,
                linked=linked,
                minio_object_key=minio_object_key,
                size_bytes=len(content),
                script_node_id=script_node_id,
                git_commit_sha=git_commit_sha,
            )
            out = {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "format": ext,
                "size_bytes": len(content),
                "project_linked": linked,
                "model_url": f"/v1/twin/nodes/{node_id}/model",
            }
            if script_node_id is not None:
                out["script_node_id"] = script_node_id
                out["git_commit_sha"] = git_commit_sha
            # MET-584: soft-warn (never block) when geometry lands in a project
            # with no recorded requirements — the model sees the warning in the
            # tool result and can course-correct; gates enforce, chat nudges.
            warning = await _unconstrained_warning(project_backend, project_id)
            if warning:
                out["warning"] = warning
            return out

    return record


async def _unconstrained_warning(project_backend: Any, project_id: str | None) -> str | None:
    """A warning string when the target project lacks prd/constraint_set.

    Best-effort: any lookup failure returns None — the commit already
    succeeded and this must never taint it.
    """
    if not project_id or project_backend is None:
        return None
    try:
        project = await project_backend.get_project(project_id)
    except Exception:  # noqa: BLE001 — advisory only
        return None
    if project is None:
        return None
    types = {str(getattr(wp.type, "value", wp.type)) for wp in project.work_products}
    if types & {"prd", "constraint_set"}:
        return None
    return (
        "This project has no recorded requirements or constraints (no prd or "
        "constraint_set work product) — the committed geometry cannot be "
        "validated against anything. Elicit the key quantified requirements "
        "from the user and record them with twin.record_constraint_set."
    )
