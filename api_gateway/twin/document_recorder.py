"""Generic text/document work-product recorder (MET-10).

The geometry recorder persists STEP blobs and the BOM recorder persists CSV; the
firmware phase needs the same loadable-artifact treatment for a pinmap and a
firmware source scaffold. Rather than a third bespoke recorder, this one persists
*any* text artifact as a loadable work product of a caller-chosen type, mirroring
:func:`make_geometry_recorder`:

1. store the text blob in MinIO (graceful — node still created on failure),
2. create a validated WorkProduct with ``content_hash`` +
   ``metadata["minio_object_key"]`` so it is loadable/viewable like any artifact,
3. link it to its project so it shows on the Projects page.
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
tracer = get_tracer("api_gateway.twin.document_recorder")

_CONTENT_TYPE = {
    "csv": "text/csv",
    "md": "text/markdown",
    "txt": "text/plain",
    "c": "text/x-csrc",
    "h": "text/x-chdr",
    "json": "application/json",
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "document")[:60]


def make_document_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` that persists a loadable text work product."""

    async def record(
        *,
        content: str,
        name: str,
        wp_type: Any,
        domain: str,
        fmt: str,
        link_type: str,
        source_tool: str,
        session_id: str | None = None,
        project_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.work_product import WorkProduct

        if not name or not isinstance(name, str):
            raise ValueError("document recorder: 'name' is required (non-empty string)")
        if not content:
            raise ValueError("document recorder: 'content' is required (non-empty)")

        blob = content.encode("utf-8")
        ext = fmt.lower().lstrip(".") or "txt"
        with tracer.start_as_current_span("twin.record_document") as span:
            wp_id = uuid4()
            content_hash = hashlib.sha256(blob).hexdigest()
            filename = f"{_slug(name)}.{ext}"
            span.set_attribute("document.name", name)
            span.set_attribute("document.type", str(getattr(wp_type, "value", wp_type)))
            span.set_attribute("document.size_bytes", len(blob))

            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id),
                    filename,
                    blob,
                    content_type=_CONTENT_TYPE.get(ext, "application/octet-stream"),
                )
            except Exception as exc:  # noqa: BLE001 — degrade like the other recorders
                logger.warning("document_blob_store_skipped", name=name, error=str(exc))

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

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=name,
                type=wp_type,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format=ext,
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=source_tool,
                project_id=project_id,
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, name, link_type)
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("document_project_link_failed", error=str(exc))

            logger.info(
                "document_recorded",
                node_id=node_id,
                wp_type=str(getattr(wp_type, "value", wp_type)),
                project_id=project_id,
                linked=linked,
                size_bytes=len(blob),
            )
            return {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "size_bytes": len(blob),
                "project_linked": linked,
            }

    return record
