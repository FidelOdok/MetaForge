"""Work-product blob storage in MinIO — gateway-facing re-export (MET-483).

The implementation was lifted to ``digital_twin.storage.work_product_blobs``
in MET-495 so non-gateway callers (the ``twin.record_decision`` recorder used
by the MCP sidecar) can store blobs without importing the gateway. This module
re-exports the same names so existing gateway imports
(``from api_gateway.twin.blob_store import ...``) keep working unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from digital_twin.storage.work_product_blobs import (
    WORK_PRODUCT_PREFIX,
    delete_work_product_blob,
    fetch_work_product_blob,
    store_work_product_blob,
    work_product_object_key,
)

if TYPE_CHECKING:
    from twin_core.models.work_product import WorkProduct

__all__ = [
    "WORK_PRODUCT_PREFIX",
    "delete_work_product_blob",
    "fetch_work_product_blob",
    "resolve_work_product_blob",
    "store_work_product_blob",
    "work_product_object_key",
]

_WORKSPACE_DIR = Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))


def resolve_work_product_blob(wp: WorkProduct) -> tuple[bytes, str]:
    """Return ``(content, filename)`` for a work product's stored blob.

    Resolution order matches the storage layering (MET-612, promoted from
    ``api_gateway.twin.routes._resolve_blob`` so non-route callers, e.g. the
    boolean-cut endpoint, can resolve a blob without importing the router):

    1. **MinIO object key** — if the WP records one in metadata
       (``minio_object_key``), fetch the blob from object storage. This
       is the architecture's source of truth for work-product blobs
       (Planner data-modalities.md).
    2. **Local file path** — the import path stores blobs on disk via
       ``shared.storage`` and records the absolute path in ``file_path``;
       workspace-relative paths resolve against the adapter workspace.

    Raises ``HTTPException(404)`` when no retrievable blob exists — which
    is exactly the "work product has no file behind it" case.
    """
    filename = str(wp.metadata.get("original_filename") or "") or (
        f"{wp.name}.{wp.format}" if wp.format else wp.name
    )

    object_key = wp.metadata.get("minio_object_key")
    if isinstance(object_key, str) and object_key:
        try:
            return fetch_work_product_blob(object_key), filename
        except HTTPException:
            raise
        except Exception as exc:  # storage misconfigured / object gone
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
