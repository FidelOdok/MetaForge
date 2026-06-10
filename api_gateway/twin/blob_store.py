"""Work-product blob storage in MinIO (MET-483).

The KB object store (``digital_twin.storage``) is prefix-locked to the
four knowledge categories, so it can't hold arbitrary work-product blobs.
Work products (schematics, PCBs, gerbers, BOMs, PDFs, CAD) are stored
here under a ``work-products/{node_id}/{filename}`` key in the same MinIO
bucket — the architecture's source of truth for work-product blobs
(Planner ``data-modalities.md``: *Git / MinIO — work product blobs, keyed
by file path / object key*).

Lazy + injectable: importing this module never requires ``minio``; the
client is built on first use from the ``MINIO_*`` environment. Tests pass
a fake ``client``/``settings`` so no real ``minio`` install or server is
needed. Gateway callers wrap these so a missing/misconfigured backend
degrades to a clear HTTP error rather than a crash.
"""

from __future__ import annotations

import io
from typing import Any

import structlog

from digital_twin.storage.minio_adapter import MinIOSettings
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.blob_store")

WORK_PRODUCT_PREFIX = "work-products"


def work_product_object_key(node_id: str, filename: str) -> str:
    """Canonical object key for a work product's blob.

    ``work-products/{node_id}/{filename}`` — node-scoped so every blob for
    a work product (and its revisions, by distinct filename) groups under
    one prefix in the bucket browser.
    """
    safe = (filename or "file").replace("/", "_").lstrip(".") or "file"
    return f"{WORK_PRODUCT_PREFIX}/{node_id}/{safe}"


def _build_client(settings: MinIOSettings) -> Any:
    from minio import Minio  # noqa: PLC0415 — lazy so the import path stays light

    return Minio(
        endpoint=settings.endpoint,
        access_key=settings.access_key,
        secret_key=settings.secret_key,
        secure=settings.secure,
        region=settings.region,
    )


def store_work_product_blob(
    node_id: str,
    filename: str,
    content: bytes,
    *,
    content_type: str | None = None,
    client: Any = None,
    settings: MinIOSettings | None = None,
) -> str:
    """Upload a work-product blob to MinIO; return its object key.

    Idempotent on the bucket (creates it if absent). ``client`` /
    ``settings`` are injectable for tests.
    """
    resolved = settings or MinIOSettings.from_env()
    cl = client or _build_client(resolved)
    key = work_product_object_key(node_id, filename)
    with tracer.start_as_current_span("blob_store.put") as span:
        span.set_attribute("storage.key", key)
        span.set_attribute("storage.size", len(content))
        if not cl.bucket_exists(resolved.bucket):
            cl.make_bucket(resolved.bucket)
        cl.put_object(
            resolved.bucket,
            key,
            io.BytesIO(content),
            length=len(content),
            content_type=content_type or "application/octet-stream",
        )
        logger.info("wp_blob_stored", key=key, bucket=resolved.bucket, size=len(content))
        return key


def fetch_work_product_blob(
    object_key: str,
    *,
    client: Any = None,
    settings: MinIOSettings | None = None,
) -> bytes:
    """Read a work-product blob from MinIO by object key."""
    resolved = settings or MinIOSettings.from_env()
    cl = client or _build_client(resolved)
    with tracer.start_as_current_span("blob_store.get") as span:
        span.set_attribute("storage.key", object_key)
        response = cl.get_object(resolved.bucket, object_key)
        try:
            data: bytes = response.read()
        finally:
            response.close()
            response.release_conn()
        span.set_attribute("storage.size", len(data))
        return data


def delete_work_product_blob(
    object_key: str,
    *,
    client: Any = None,
    settings: MinIOSettings | None = None,
) -> None:
    """Remove a work-product blob from MinIO by object key (idempotent)."""
    resolved = settings or MinIOSettings.from_env()
    cl = client or _build_client(resolved)
    with tracer.start_as_current_span("blob_store.delete") as span:
        span.set_attribute("storage.key", object_key)
        cl.remove_object(resolved.bucket, object_key)
        logger.info("wp_blob_deleted", key=object_key, bucket=resolved.bucket)
