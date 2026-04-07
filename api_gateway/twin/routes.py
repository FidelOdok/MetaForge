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
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

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
from observability.tracing import get_tracer
from shared.storage import default_storage
from twin_core.api import InMemoryTwinAPI
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
) -> TwinNodeListResponse:
    """List all work-product nodes in the Digital Twin."""
    with tracer.start_as_current_span("twin.list_nodes") as span:
        if domain is not None:
            span.set_attribute("twin.filter.domain", domain)
        work_products = await _twin.list_work_products(domain=domain)
        nodes = [_wp_to_response(wp) for wp in work_products]
        logger.info("twin_nodes_listed", count=len(nodes), domain=domain)
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

        # Find the STEP file in the workspace
        file_path = wp.file_path
        if not file_path:
            # Try to find a STEP file matching the WP name pattern
            candidates = list(_WORKSPACE_DIR.glob("*.step"))
            if not candidates:
                raise HTTPException(
                    status_code=404,
                    detail="No STEP file found for this work product",
                )
            # Use the most recently modified STEP file
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            file_path = str(candidates[0])

        step_path = Path(file_path)
        if not step_path.is_absolute():
            step_path = _WORKSPACE_DIR / step_path

        if not step_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"STEP file not found: {step_path.name}",
            )

        span.set_attribute("step.file", str(step_path))
        file_bytes = step_path.read_bytes()

        service = ConversionService()
        result = service.convert(file_bytes, step_path.name, quality)

        logger.info(
            "node_model_converted",
            node_id=node_id,
            step_file=step_path.name,
            cached=result.get("cached", False),
        )
        return result


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

        # Store file
        content_hash = default_storage.content_hash(content)
        session_id = f"import-{uuid4()}"
        stored_path = default_storage.save(session_id, filename, content)

        # Build name from description or filename
        name = description.strip()[:60] if description.strip() else Path(filename).stem

        # Create WorkProduct
        now = datetime.now(UTC)
        wp = WorkProduct(
            id=uuid4(),
            name=name,
            type=resolved_type,
            domain=resolved_domain,
            file_path=stored_path,
            content_hash=content_hash,
            format=ext.lstrip("."),
            metadata={
                "imported": True,
                "original_filename": filename,
                "session_id": session_id,
                "timestamp": now.isoformat(),
                **metadata,
            },
            created_at=now,
            updated_at=now,
            created_by="import-api",
        )

        created_wp = await _twin.create_work_product(wp)

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
            metadata=metadata,
            project_id=project_id,
            created_at=now.isoformat(),
        )


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
async def list_file_links() -> list[FileLinkResponse]:
    """List all file links with live sync status."""
    links = link_store.list_all()
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
