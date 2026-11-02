"""Turn one file into a twin work product: the body of every import.

``POST /v1/twin/import`` (a person in the dashboard) and an approved upload
(``api_gateway.uploads``: a plugin client, a signed link, the local plugin)
both end here, so the formats, the size limit, the metadata extraction, the
blob store and the project link are one body of code and cannot drift apart.

Validation raises :class:`ImportRejected` with the HTTP status a route should
answer with; the caller decides whether that is a 4xx or one line in an
upload's per-file report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from api_gateway.twin.import_schemas import ImportWorkProductResponse
from api_gateway.twin.import_service import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    ImportService,
    get_extension,
    infer_domain,
    infer_wp_type,
)
from api_gateway.twin.version_service import VersionService
from observability.tracing import get_tracer
from shared.storage import default_storage
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.import_commit")


class ImportRejected(ValueError):
    """A file the import refuses, with the status a route answers with."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def check_importable(filename: str, size: int) -> str:
    """The file's extension if it may be imported, else :class:`ImportRejected`."""
    ext = get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ImportRejected(
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if size <= 0:
        raise ImportRejected("Uploaded file is empty")
    if size > MAX_FILE_SIZE:
        raise ImportRejected(f"File too large ({size} bytes). Max: {MAX_FILE_SIZE}", 413)
    return ext


def resolve_wp_type(wp_type: str | None, ext: str) -> WorkProductType:
    """The requested type, or the one the extension implies."""
    if wp_type is None or not wp_type.strip():
        return infer_wp_type(ext)
    try:
        return WorkProductType(wp_type)
    except ValueError as exc:
        valid = [t.value for t in WorkProductType]
        raise ImportRejected(f"Invalid wp_type '{wp_type}'. Valid: {valid}") from exc


async def commit_import(
    twin: Any,
    content: bytes,
    filename: str,
    *,
    project_id: str | None = None,
    domain: str | None = None,
    wp_type: str | None = None,
    description: str = "",
    created_by: str = "import-api",
    extra_metadata: dict[str, Any] | None = None,
) -> ImportWorkProductResponse:
    """Validate, extract, store and record ``content`` as a work product."""
    from api_gateway.twin.routes import _content_type_for  # noqa: PLC0415 - avoids a cycle

    with tracer.start_as_current_span("twin.commit_import") as span:
        ext = check_importable(filename, len(content))
        span.set_attribute("import.filename", filename)
        span.set_attribute("import.extension", ext)
        resolved_domain = domain or infer_domain(ext)
        resolved_type = resolve_wp_type(wp_type, ext)

        metadata = await ImportService().extract_metadata(content, filename)

        # Store file locally (kept as a cache / fallback even when MinIO
        # is the source of truth — download prefers the object key).
        content_hash = default_storage.content_hash(content)
        session_id = f"import-{uuid4()}"
        stored_path = default_storage.save(session_id, filename, content)

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
            **(extra_metadata or {}),
        }
        if minio_object_key:
            wp_metadata["minio_object_key"] = minio_object_key
            span.set_attribute("import.minio_object_key", minio_object_key)

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
            created_by=created_by,
            # MET-491: stamp project_id on the node itself so the twin's
            # project-scoped read path (list_work_products(project_id=...)) finds
            # it. The Postgres project junction below is separate and only feeds
            # the Projects page; without this the /twin project filter returns 0
            # for imported nodes (decision/geometry-recorder nodes already set it).
            project_id=project_id or None,
        )

        created_wp = await twin.create_work_product(wp)

        revision = VersionService.build_revision(created_wp, "Initial import")
        updated_metadata = VersionService.append_to_metadata(created_wp.metadata, revision)
        await twin.update_work_product(created_wp.id, {"metadata": updated_metadata})

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
            created_by=created_by,
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
