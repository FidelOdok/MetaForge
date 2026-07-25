"""Digital Twin viewer REST endpoints for the MetaForge Gateway.

Exposes the Twin's work-product graph to the dashboard frontend.
Endpoints live under ``/v1/twin``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile

from api_gateway.convert.service import ConversionService
from api_gateway.twin.file_link import (
    FileLink,
    FileLinkCreateRequest,
    FileLinkResponse,
    _file_hash,
    check_sync_status,
    link_store,
    sync_linked_file,
)
from api_gateway.twin.import_schemas import ImportWorkProductResponse
from api_gateway.twin.import_service import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    ImportService,
    get_extension,
    infer_domain,
    infer_wp_type,
)
from api_gateway.twin.schemas import (
    TwinNodeListResponse,
    TwinNodeResponse,
    TwinRelationshipListResponse,
    TwinRelationshipResponse,
)
from api_gateway.twin.version_schemas import (
    IterateRequest,
    RevisionDiff,
    WorkProductRevision,
    WorkProductVersionHistory,
)
from api_gateway.twin.version_service import VersionService
from observability.tracing import get_tracer
from shared.storage import default_storage
from twin_core.api import InMemoryTwinAPI, OrphanWouldBeCreatedError
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin")

# ---------------------------------------------------------------------------
# Twin integration (initialised by server lifespan)
# ---------------------------------------------------------------------------

_twin: InMemoryTwinAPI = InMemoryTwinAPI.create()


def init_twin(twin: object) -> None:
    """Replace the default InMemoryTwinAPI with the orchestrator's twin."""
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("twin_viewer_twin_initialized", twin_type=type(twin).__name__)


def get_twin() -> object:
    """The active twin API (used by the design-flow mechanical handlers)."""
    return _twin


router = APIRouter(prefix="/v1/twin", tags=["twin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wp_to_response(wp: WorkProduct) -> TwinNodeResponse:
    """Map a WorkProduct to a TwinNodeResponse."""
    properties: dict[str, str | int | float | bool] = {
        "wp_type": wp.type.value,
        "format": wp.format,
        "file_path": wp.file_path,
        "created_by": wp.created_by,
    }
    # Merge metadata, keeping only JSON-primitive values
    for key, value in wp.metadata.items():
        if isinstance(value, (str, int, float, bool)):
            properties[key] = value

    return TwinNodeResponse(
        id=str(wp.id),
        name=wp.name,
        type="work_product",
        domain=wp.domain,
        status="valid",
        properties=properties,
        updatedAt=wp.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/nodes", response_model=TwinNodeListResponse)
async def list_twin_nodes(
    domain: str | None = None,
    project_id: str | None = None,
) -> TwinNodeListResponse:
    """List work-product nodes in the Digital Twin.

    ``project_id`` scopes the view to a single project (MET-491). Omitted
    or empty returns every node (including unscoped legacy nodes) —
    preserving the prior global behaviour. A specific ``project_id``
    returns only that project's nodes; unscoped nodes are excluded.
    """
    with tracer.start_as_current_span("twin.list_nodes") as span:
        if domain is not None:
            span.set_attribute("twin.filter.domain", domain)
        scoped_project: UUID | None = None
        if project_id:
            try:
                scoped_project = UUID(project_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid project_id format")
            span.set_attribute("twin.filter.project_id", project_id)
        work_products = await _twin.list_work_products(domain=domain, project_id=scoped_project)
        nodes = [_wp_to_response(wp) for wp in work_products]
        logger.info("twin_nodes_listed", count=len(nodes), domain=domain, project_id=project_id)
        return TwinNodeListResponse(nodes=nodes, total=len(nodes))


@router.get("/relationships", response_model=TwinRelationshipListResponse)
async def list_twin_relationships() -> TwinRelationshipListResponse:
    """List all edges in the Digital Twin graph."""
    with tracer.start_as_current_span("twin.list_relationships") as span:
        work_products = await _twin.list_work_products()
        edges = []
        seen: set[str] = set()
        for wp in work_products:
            try:
                wp_edges = await _twin.get_edges(wp.id)
            except Exception:
                continue
            for edge in wp_edges:
                key = f"{edge.source_id}:{edge.target_id}:{edge.edge_type}"
                if key in seen:
                    continue
                seen.add(key)
                label = str(edge.edge_type).replace("_", " ")
                edges.append(
                    TwinRelationshipResponse(
                        id=key,
                        sourceId=str(edge.source_id),
                        targetId=str(edge.target_id),
                        type=str(edge.edge_type),
                        label=label,
                    )
                )
        span.set_attribute("twin.relationships_count", len(edges))
        logger.info("twin_relationships_listed", count=len(edges))
        return TwinRelationshipListResponse(relationships=edges, total=len(edges))


@router.get("/nodes/{node_id}", response_model=TwinNodeResponse)
async def get_twin_node(node_id: str) -> TwinNodeResponse:
    """Get a single work-product node by ID."""
    with tracer.start_as_current_span("twin.get_node") as span:
        span.set_attribute("twin.node_id", node_id)
        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")
        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Node not found")
        logger.info("twin_node_retrieved", node_id=node_id)
        return _wp_to_response(wp)


_WORKSPACE_DIR = Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))


@router.get("/nodes/{node_id}/model")
async def get_node_model(
    node_id: str,
    quality: str = Query("standard", pattern="^(preview|standard|fine)$"),
) -> dict[str, object]:
    """Convert a CAD work-product's STEP file to GLB and return the URL.

    Reads the STEP file from the shared adapter workspace, converts it
    via the OCCT converter, and returns the GLB URL + metadata.
    """
    with tracer.start_as_current_span("twin.get_node_model") as span:
        span.set_attribute("twin.node_id", node_id)

        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")

        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Node not found")

        # Resolve the source blob durably: MinIO first (the architecture's
        # source of truth, survives gateway container recreation), then the
        # local/workspace file. Previously this read only `wp.file_path`, so a
        # recreated gateway that lost its ephemeral import storage produced a
        # 404 / empty model even though the blob was safe in MinIO (MET-522/489).
        content, filename = _resolve_blob(wp)
        span.set_attribute("model.filename", filename)

        result = ConversionService().convert(content, filename, quality)
        logger.info(
            "node_model_converted",
            node_id=node_id,
            filename=filename,
            cached=result.get("cached", False),
        )
        return result


# ---------------------------------------------------------------------------
# Work-product file download / open / preview (MET-483)
# ---------------------------------------------------------------------------

# Content types for inline preview in the browser. Anything not listed
# falls back to application/octet-stream (forces a download rather than a
# broken inline render). Keyed by the WorkProduct.format (extension sans
# dot) or the filename suffix.
_PREVIEW_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
    "gif": "image/gif",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "log": "text/plain; charset=utf-8",
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "step": "application/step",
    "stp": "application/step",
    # Tool-native text formats preview fine as plain text.
    "kicad_sch": "text/plain; charset=utf-8",
    "kicad_pcb": "text/plain; charset=utf-8",
    "net": "text/plain; charset=utf-8",
    "gbr": "text/plain; charset=utf-8",
    "c": "text/plain; charset=utf-8",
    "h": "text/plain; charset=utf-8",
}


def _content_type_for(fmt: str, filename: str) -> str:
    """Best-effort MIME type for inline preview from format or filename."""
    key = (fmt or "").lower().lstrip(".")
    if key in _PREVIEW_CONTENT_TYPES:
        return _PREVIEW_CONTENT_TYPES[key]
    suffix = Path(filename).suffix.lower().lstrip(".")
    return _PREVIEW_CONTENT_TYPES.get(suffix, "application/octet-stream")


def _resolve_blob(wp: WorkProduct) -> tuple[bytes, str]:
    """Return ``(content, filename)`` for a work product's stored blob.

    Resolution order matches the storage layering:

    1. **MinIO object key** — if the WP records one in metadata
       (``minio_object_key``), fetch the blob from object storage. This
       is the architecture's source of truth for work-product blobs
       (Planner data-modalities.md).
    2. **Local file path** — the import path stores blobs on disk via
       ``shared.storage`` and records the absolute path in ``file_path``;
       workspace-relative paths resolve against the adapter workspace
       (mirrors ``get_node_model``).

    Raises ``HTTPException(404)`` when no retrievable blob exists — which
    is exactly the "work product has no file behind it" case.
    """
    filename = str(wp.metadata.get("original_filename") or "") or (
        f"{wp.name}.{wp.format}" if wp.format else wp.name
    )

    object_key = wp.metadata.get("minio_object_key")
    if isinstance(object_key, str) and object_key:
        try:
            from api_gateway.twin.blob_store import fetch_work_product_blob

            return fetch_work_product_blob(object_key), filename
        except HTTPException:
            raise
        except Exception as exc:  # storage misconfigured / object gone
            logger.warning("wp_blob_minio_fetch_failed", key=object_key, error=str(exc))
            raise HTTPException(
                status_code=502, detail="Work product blob could not be read from storage"
            ) from exc

    file_path = wp.file_path
    if not file_path:
        raise HTTPException(
            status_code=404,
            detail="Work product has no stored file (empty file_path and no object key)",
        )
    path = Path(file_path)
    if not path.is_absolute():
        path = _WORKSPACE_DIR / path
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Stored file not found: {path.name}")
    return path.read_bytes(), (filename or path.name)


@router.get("/nodes/{node_id}/file")
async def download_node_file(
    node_id: str,
    download: bool = Query(False, description="Force attachment download vs inline preview"),
) -> Response:
    """Stream a work product's stored file for download / open / preview.

    ``download=false`` (default) returns the blob inline with a preview
    content-type so the dashboard can render PDFs, images, text, and BOMs
    in place; ``download=true`` forces a ``Content-Disposition: attachment``.
    """
    with tracer.start_as_current_span("twin.download_node_file") as span:
        span.set_attribute("twin.node_id", node_id)
        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")

        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Node not found")

        content, filename = _resolve_blob(wp)
        media_type = _content_type_for(wp.format, filename)
        disposition = "attachment" if download else "inline"
        span.set_attribute("file.media_type", media_type)
        span.set_attribute("file.size", len(content))
        logger.info(
            "node_file_served",
            node_id=node_id,
            wp_type=wp.type.value,
            filename=filename,
            media_type=media_type,
            disposition=disposition,
            size=len(content),
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
        )


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(
    node_id: str,
    cascade: bool = Query(False, description="Also delete dependents that would orphan"),
) -> None:
    """Delete a work-product node, its project links, and its MinIO blob (MET-484).

    Without ``cascade`` a delete that would orphan dependents returns 409.
    Best-effort on the blob (a storage failure doesn't block the delete).
    """
    with tracer.start_as_current_span("twin.delete_node") as span:
        span.set_attribute("twin.node_id", node_id)
        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")

        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Node not found")

        # Best-effort: remove the MinIO blob so deleting the node doesn't
        # orphan its object. A storage hiccup must not block the delete.
        object_key = wp.metadata.get("minio_object_key")
        if isinstance(object_key, str) and object_key:
            try:
                from api_gateway.twin.blob_store import delete_work_product_blob

                delete_work_product_blob(object_key)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning("wp_blob_delete_failed", key=object_key, error=str(exc))

        try:
            await _twin.delete_work_product(uid, cascade=cascade)
        except OrphanWouldBeCreatedError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Delete would orphan dependents; retry with ?cascade=true ({exc})",
            ) from exc

        from api_gateway.projects.routes import unlink_work_product_from_all_projects

        links_removed = await unlink_work_product_from_all_projects(str(uid))
        logger.info(
            "twin_node_deleted",
            node_id=node_id,
            cascade=cascade,
            links_removed=links_removed,
        )


# ---------------------------------------------------------------------------
# Import endpoint
# ---------------------------------------------------------------------------


@router.post("/import", response_model=ImportWorkProductResponse, status_code=201)
async def import_work_product(
    file: UploadFile = File(..., description="Design file to import"),
    project_id: str | None = Form(None, description="Project to link to"),
    domain: str | None = Form(None, description="Domain (mechanical, electronics)"),
    wp_type: str | None = Form(None, description="Work product type"),
    description: str = Form("", description="Work product description"),
) -> ImportWorkProductResponse:
    """Upload a design file and register it as a work product in the Twin.

    Accepts STEP, IGES, KiCad (.kicad_sch, .kicad_pcb), and FreeCAD
    (.FCStd) files. Metadata is extracted automatically based on file type.
    """
    with tracer.start_as_current_span("twin.import_work_product") as span:
        filename = file.filename or "unknown"
        ext = get_extension(filename)
        span.set_attribute("import.filename", filename)
        span.set_attribute("import.extension", ext)

        # Validate extension
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file type '{ext}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                ),
            )

        # Read and validate content
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({len(content)} bytes). Max: {MAX_FILE_SIZE}",
            )

        # Resolve domain and type
        resolved_domain = domain or infer_domain(ext)
        if wp_type is not None:
            try:
                resolved_type = WorkProductType(wp_type)
            except ValueError:
                valid = [t.value for t in WorkProductType]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid wp_type '{wp_type}'. Valid: {valid}",
                )
        else:
            resolved_type = infer_wp_type(ext)

        # Extract metadata
        import_service = ImportService()
        metadata = await import_service.extract_metadata(content, filename)

        # Store file locally (kept as a cache / fallback even when MinIO
        # is the source of truth — download prefers the object key).
        content_hash = default_storage.content_hash(content)
        session_id = f"import-{uuid4()}"
        stored_path = default_storage.save(session_id, filename, content)

        # Build name from description or filename
        name = description.strip()[:60] if description.strip() else Path(filename).stem

        now = datetime.now(UTC)
        wp_id = uuid4()

        # MET-483: upload the blob to MinIO — the architecture's source of
        # truth for work-product blobs (data-modalities.md). Graceful: if
        # the minio client is missing or the server is unreachable, keep
        # the local file_path and skip the object key so download falls
        # back to disk rather than failing the import.
        minio_object_key: str | None = None
        try:
            from api_gateway.twin.blob_store import store_work_product_blob

            minio_object_key = store_work_product_blob(
                str(wp_id),
                filename,
                content,
                content_type=_content_type_for(ext.lstrip("."), filename),
            )
        except Exception as exc:  # minio absent / bucket unreachable / etc.
            logger.warning("wp_blob_minio_upload_skipped", filename=filename, error=str(exc))

        wp_metadata: dict[str, object] = {
            "imported": True,
            "original_filename": filename,
            "session_id": session_id,
            "timestamp": now.isoformat(),
            **metadata,
        }
        if minio_object_key:
            wp_metadata["minio_object_key"] = minio_object_key
            span.set_attribute("import.minio_object_key", minio_object_key)

        # Create WorkProduct
        wp = WorkProduct(
            id=wp_id,
            name=name,
            type=resolved_type,
            domain=resolved_domain,
            file_path=stored_path,
            content_hash=content_hash,
            format=ext.lstrip("."),
            metadata=wp_metadata,
            created_at=now,
            updated_at=now,
            created_by="import-api",
            # MET-491: stamp project_id on the node itself so the twin's
            # project-scoped read path (list_work_products(project_id=...)) finds
            # it. The Postgres project junction below is separate and only feeds
            # the Projects page; without this the /twin project filter returns 0
            # for imported nodes (decision/geometry-recorder nodes already set it).
            project_id=project_id or None,
        )

        created_wp = await _twin.create_work_product(wp)

        # Record initial revision
        revision = VersionService.build_revision(created_wp, "Initial import")
        updated_metadata = VersionService.append_to_metadata(created_wp.metadata, revision)
        await _twin.update_work_product(created_wp.id, {"metadata": updated_metadata})

        # Link to project if requested
        if project_id:
            try:
                from api_gateway.projects.routes import link_work_product_to_project

                await link_work_product_to_project(
                    project_id,
                    str(created_wp.id),
                    created_wp.name,
                    created_wp.type.value,
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.warning(
                    "import_project_link_failed",
                    project_id=project_id,
                    error=str(exc),
                )

        logger.info(
            "work_product_imported",
            wp_id=str(created_wp.id),
            filename=filename,
            domain=resolved_domain,
            wp_type=resolved_type.value,
            project_id=project_id,
        )

        return ImportWorkProductResponse(
            id=str(created_wp.id),
            name=created_wp.name,
            domain=resolved_domain,
            wp_type=resolved_type.value,
            file_path=stored_path,
            content_hash=content_hash,
            format=ext.lstrip("."),
            # Return the stored metadata (incl. minio_object_key) so callers
            # see where the blob landed, not just the extracted fields.
            metadata=wp_metadata,
            project_id=project_id,
            created_at=now.isoformat(),
        )


# ---------------------------------------------------------------------------
# Version history endpoints
# ---------------------------------------------------------------------------


@router.get("/nodes/{node_id}/versions", response_model=WorkProductVersionHistory)
async def get_version_history(node_id: UUID) -> WorkProductVersionHistory:
    """Return the full revision history for a work product."""
    wp = await _twin.get_work_product(node_id)
    if wp is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return VersionService.get_history(wp)


@router.post("/nodes/{node_id}/iterate", response_model=WorkProductRevision, status_code=201)
async def iterate_work_product(node_id: UUID, body: IterateRequest) -> WorkProductRevision:
    """Record a new revision and apply metadata updates to a work product."""
    wp = await _twin.get_work_product(node_id)
    if wp is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    updated_meta = {**wp.metadata, **body.metadata_updates}
    revision = VersionService.build_revision(
        wp, body.change_description, snapshot_override=updated_meta
    )
    final_meta = VersionService.append_to_metadata(updated_meta, revision)
    await _twin.update_work_product(node_id, {"metadata": final_meta})
    return WorkProductRevision(**revision)


@router.get("/nodes/{node_id}/diff", response_model=RevisionDiff)
async def diff_versions(node_id: UUID, v1: int = Query(...), v2: int = Query(...)) -> RevisionDiff:
    """Return a metadata diff between two revisions (1-indexed)."""
    wp = await _twin.get_work_product(node_id)
    if wp is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    history = VersionService.get_history(wp)
    try:
        return VersionService.diff(history, v1, v2)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# File link endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/nodes/{node_id}/link",
    response_model=FileLinkResponse,
    status_code=201,
)
async def create_file_link(
    node_id: str,
    body: FileLinkCreateRequest,
) -> FileLinkResponse:
    """Link a work product to an external source file.

    The source file must exist on the gateway's filesystem. Once linked,
    you can call ``POST /sync`` to re-import changes, or enable ``watch``
    for automatic detection.
    """
    with tracer.start_as_current_span("twin.create_file_link") as span:
        span.set_attribute("twin.node_id", node_id)
        span.set_attribute("link.source_path", body.source_path)

        # Validate work product exists
        try:
            uid = UUID(node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid node ID format")
        wp = await _twin.get_work_product(uid)
        if wp is None:
            raise HTTPException(status_code=404, detail="Work product not found")

        # Validate source file exists
        source = Path(body.source_path)
        if not source.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Source file not found: {body.source_path}",
            )
        if not source.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"Source path is not a file: {body.source_path}",
            )

        now = datetime.now(UTC)
        source_hash = _file_hash(body.source_path)

        link = FileLink(
            work_product_id=node_id,
            source_path=body.source_path,
            tool=body.tool,
            watch=body.watch,
            source_hash=source_hash,
            sync_status="synced",
            last_synced_at=now.isoformat(),
            created_at=now.isoformat(),
        )
        link_store.create(link)

        logger.info(
            "file_link_created",
            wp_id=node_id,
            source_path=body.source_path,
            tool=body.tool,
        )

        return FileLinkResponse(**link.model_dump())


@router.get("/nodes/{node_id}/link", response_model=FileLinkResponse)
async def get_file_link(node_id: str) -> FileLinkResponse:
    """Get the file link for a work product, with live sync status."""
    link = link_store.get(node_id)
    if link is None:
        raise HTTPException(status_code=404, detail="No file link for this work product")

    # Check live status
    link.sync_status = check_sync_status(link)
    link_store.update(node_id, sync_status=link.sync_status)

    return FileLinkResponse(**link.model_dump())


@router.delete("/nodes/{node_id}/link", status_code=204)
async def delete_file_link(node_id: str) -> None:
    """Remove the file link for a work product."""
    if not link_store.delete(node_id):
        raise HTTPException(status_code=404, detail="No file link for this work product")
    logger.info("file_link_deleted", wp_id=node_id)


@router.get("/links", response_model=list[FileLinkResponse])
async def list_file_links(project_id: str | None = None) -> list[FileLinkResponse]:
    """List file links with live sync status, optionally scoped to a project.

    A link has no project of its own; scoping (MET-517) keeps only links whose
    linked work product belongs to ``project_id``.
    """
    links = link_store.list_all()
    if project_id:
        try:
            scoped = UUID(project_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid project_id format")
        wps = await _twin.list_work_products(project_id=scoped)
        allowed = {str(wp.id) for wp in wps}
        links = [link for link in links if link.work_product_id in allowed]
    results = []
    for link in links:
        link.sync_status = check_sync_status(link)
        link_store.update(link.work_product_id, sync_status=link.sync_status)
        results.append(FileLinkResponse(**link.model_dump()))
    return results


@router.post("/nodes/{node_id}/sync")
async def sync_file_link(node_id: str) -> dict[str, object]:
    """Manually trigger a sync for a linked work product.

    Re-reads the source file, extracts metadata, and updates the Twin
    node if the file has changed.
    """
    link = link_store.get(node_id)
    if link is None:
        raise HTTPException(status_code=404, detail="No file link for this work product")

    result = await sync_linked_file(link, _twin)
    return result
