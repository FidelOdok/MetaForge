"""File link service — tracks connections between work products and external files.

A *file link* connects a Twin WorkProduct node to a source file on the
local filesystem (e.g., a KiCad schematic or FreeCAD model).  The link
records the file's content hash at sync time so we can detect when the
external file has changed.

Sync statuses:
- synced:       hash matches — file unchanged since last sync
- changed:      hash differs — file was modified externally
- disconnected: source file no longer exists
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from api_gateway.twin.import_service import ImportService
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.file_link")


class FileLink(BaseModel):
    """Persistent record linking a work product to an external file."""

    work_product_id: str
    source_path: str
    tool: str = ""  # kicad | freecad | cadquery | ""
    watch: bool = True
    source_hash: str = ""
    sync_status: str = "synced"  # synced | changed | disconnected
    last_synced_at: str = ""
    created_at: str = ""


class FileLinkResponse(BaseModel):
    """API response for a file link."""

    work_product_id: str
    source_path: str
    tool: str
    watch: bool
    sync_status: str
    source_hash: str
    last_synced_at: str
    created_at: str


class FileLinkCreateRequest(BaseModel):
    """Request body for creating a file link."""

    source_path: str = Field(..., description="Absolute path to the source file")
    tool: str = Field("", description="Tool identifier (kicad, freecad, cadquery)")
    watch: bool = Field(True, description="Enable file watching")


class FileLinkStore:
    """In-memory store for file links.

    Keyed by work_product_id (one link per work product).
    """

    def __init__(self) -> None:
        self._links: dict[str, FileLink] = {}

    def get(self, wp_id: str) -> FileLink | None:
        return self._links.get(wp_id)

    def list_all(self) -> list[FileLink]:
        return list(self._links.values())

    def create(self, link: FileLink) -> FileLink:
        self._links[link.work_product_id] = link
        return link

    def delete(self, wp_id: str) -> bool:
        return self._links.pop(wp_id, None) is not None

    def update(self, wp_id: str, **kwargs: Any) -> FileLink | None:
        link = self._links.get(wp_id)
        if link is None:
            return None
        for key, val in kwargs.items():
            if hasattr(link, key):
                setattr(link, key, val)
        return link


# Module-level singleton
link_store = FileLinkStore()


def _file_hash(path: str) -> str:
    """Compute SHA-256 of a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_sync_status(link: FileLink) -> str:
    """Check current sync status of a linked file.

    Returns 'synced', 'changed', or 'disconnected'.
    """
    p = Path(link.source_path)
    if not p.exists():
        return "disconnected"
    current_hash = _file_hash(link.source_path)
    if current_hash != link.source_hash:
        return "changed"
    return "synced"


async def sync_linked_file(
    link: FileLink,
    twin: Any,
) -> dict[str, Any]:
    """Re-import a linked file and update the work product.

    Returns a dict with sync results.
    """
    with tracer.start_as_current_span("file_link.sync") as span:
        span.set_attribute("link.wp_id", link.work_product_id)
        span.set_attribute("link.source_path", link.source_path)

        p = Path(link.source_path)
        if not p.exists():
            link.sync_status = "disconnected"
            link_store.update(link.work_product_id, sync_status="disconnected")
            return {"status": "disconnected", "error": "Source file not found"}

        content = p.read_bytes()
        new_hash = _file_hash(link.source_path)

        if new_hash == link.source_hash:
            return {"status": "synced", "message": "No changes detected"}

        # Extract new metadata
        service = ImportService()
        filename = p.name
        metadata = await service.extract_metadata(content, filename)

        # Fetch existing work product for metadata merge + revision recording
        from uuid import UUID

        from api_gateway.twin.version_service import VersionService

        now = datetime.now(UTC)
        try:
            wp_id = UUID(link.work_product_id)
            existing_wp = await twin.get_work_product(wp_id)
        except Exception:
            existing_wp = None

        existing_meta: dict[str, Any] = existing_wp.metadata if existing_wp else {}

        # Build and record a revision from the current state before overwriting
        if existing_wp is not None:
            change_desc = f"File sync from {link.source_path}"
            revision = VersionService.build_revision(existing_wp, change_desc)
        else:
            revision = None

        sync_fields: dict[str, Any] = {
            "synced_from": link.source_path,
            "sync_tool": link.tool,
            "last_synced_at": now.isoformat(),
            "source_hash": new_hash,
            **metadata,
        }
        merged_meta: dict[str, Any] = {**existing_meta, **sync_fields}
        if revision is not None:
            merged_meta = VersionService.append_to_metadata(merged_meta, revision)

        updates: dict[str, Any] = {
            "metadata": merged_meta,
            "updated_at": now,
            "content_hash": new_hash,
        }

        try:
            await twin.update_work_product(wp_id, updates)
        except Exception as exc:
            span.record_exception(exc)
            logger.warning(
                "sync_twin_update_failed",
                wp_id=link.work_product_id,
                error=str(exc),
            )
            return {"status": "error", "error": str(exc)}

        # Update link state
        link_store.update(
            link.work_product_id,
            source_hash=new_hash,
            sync_status="synced",
            last_synced_at=now.isoformat(),
        )

        logger.info(
            "file_link_synced",
            wp_id=link.work_product_id,
            source_path=link.source_path,
            new_hash=new_hash[:12],
        )

        return {
            "status": "synced",
            "previous_hash": link.source_hash[:12],
            "new_hash": new_hash[:12],
            "metadata": metadata,
        }
