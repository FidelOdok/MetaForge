"""Work-product blob storage in MinIO — gateway-facing re-export (MET-483).

The implementation was lifted to ``digital_twin.storage.work_product_blobs``
in MET-495 so non-gateway callers (the ``twin.record_decision`` recorder used
by the MCP sidecar) can store blobs without importing the gateway. This module
re-exports the same names so existing gateway imports
(``from api_gateway.twin.blob_store import ...``) keep working unchanged.
"""

from __future__ import annotations

from digital_twin.storage.work_product_blobs import (
    WORK_PRODUCT_PREFIX,
    delete_work_product_blob,
    fetch_work_product_blob,
    store_work_product_blob,
    work_product_object_key,
)

__all__ = [
    "WORK_PRODUCT_PREFIX",
    "delete_work_product_blob",
    "fetch_work_product_blob",
    "store_work_product_blob",
    "work_product_object_key",
]
