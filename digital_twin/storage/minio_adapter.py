"""Production MinIO/S3 adapter for the KB object store (MET-476).

Wraps the synchronous ``minio.Minio`` client behind the
:class:`~digital_twin.storage.kb_storage.KBObjectStorage` protocol. The
``minio`` package is an optional ``[knowledge]`` extra — importing this
module never fails, but instantiating :class:`MinIOKBStorage` in an
environment without ``minio`` installed raises :class:`MinIODependencyError`
with a clear remediation hint, mirroring how ``pdfplumber`` is handled
in ``digital_twin/datasheets/parser.py``.

All Minio calls are wrapped with :func:`asyncio.to_thread` so this
adapter satisfies the async :class:`KBObjectStorage` protocol without
blocking the event loop.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import structlog

from digital_twin.storage.kb_storage import (
    DEFAULT_BUCKET_NAME,
    KBPrefix,
    KBStorageError,
    ObjectListing,
    object_key,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.storage.minio_adapter")

_PREFIX_MARKER = ".keep"


class MinIODependencyError(RuntimeError):
    """Raised when the ``minio`` package is not installed.

    The dependency lives in the ``[knowledge]`` extra of pyproject.toml.
    Production deployments install it; lightweight unit-test environments
    skip it and use ``InMemoryKBStorage`` instead.
    """


@dataclass(frozen=True)
class MinIOSettings:
    """Connection + bucket configuration loaded from env vars.

    The provisioning script and the gateway both build a settings object
    from environment variables so credentials never live in code:
    ``MINIO_ENDPOINT``, ``MINIO_ACCESS_KEY``, ``MINIO_SECRET_KEY``,
    ``MINIO_BUCKET`` (default ``metaforge-kb``), ``MINIO_SECURE``
    (default ``true``).
    """

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str = DEFAULT_BUCKET_NAME
    secure: bool = True
    region: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MinIOSettings:
        src = os.environ if env is None else env
        try:
            endpoint = src["MINIO_ENDPOINT"]
            access_key = src["MINIO_ACCESS_KEY"]
            secret_key = src["MINIO_SECRET_KEY"]
        except KeyError as exc:  # pragma: no cover — exercised in tests
            raise KBStorageError(f"MinIO settings missing required env var: {exc.args[0]}") from exc
        secure_raw = src.get("MINIO_SECURE", "true").strip().lower()
        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=src.get("MINIO_BUCKET", DEFAULT_BUCKET_NAME),
            secure=secure_raw not in {"0", "false", "no", "off"},
            region=src.get("MINIO_REGION") or None,
        )


def _resolve_minio_module() -> Any:
    """Import ``minio`` lazily; raise a clear error if absent.

    Held behind a helper so unit tests can monkey-patch this symbol to
    return a fake module without ``minio`` ever being installed.
    """
    try:
        import minio  # noqa: PLC0415 — lazy by design
    except ImportError as exc:
        raise MinIODependencyError(
            "The 'minio' package is required for MinIOKBStorage. Install with "
            "`pip install -e .[knowledge]` (or `pip install minio>=7.2`)."
        ) from exc
    return minio


class MinIOKBStorage:
    """MinIO/S3 implementation of :class:`KBObjectStorage`.

    Idempotent ``initialize()``: ensures the bucket exists, enables
    versioning so datasheet history is immutable per MET-476's contract,
    and writes a zero-byte marker into each :class:`KBPrefix` so the
    folder structure shows up in administrative tools.
    """

    def __init__(
        self,
        settings: MinIOSettings,
        *,
        client: Any = None,
    ) -> None:
        self._settings = settings
        if client is not None:
            # Tests pass an already-built fake client so the lazy ``minio``
            # import never fires.
            self._client = client
        else:
            minio_mod = _resolve_minio_module()
            self._client = minio_mod.Minio(
                endpoint=settings.endpoint,
                access_key=settings.access_key,
                secret_key=settings.secret_key,
                secure=settings.secure,
                region=settings.region,
            )
        self._initialized = False

    @property
    def bucket(self) -> str:
        return self._settings.bucket

    async def initialize(self) -> None:
        if self._initialized:
            return
        with tracer.start_as_current_span("kb_storage.minio.initialize") as span:
            span.set_attribute("storage.bucket", self._settings.bucket)
            await asyncio.to_thread(self._ensure_bucket)
            await asyncio.to_thread(self._enable_versioning)
            for prefix in KBPrefix:
                key = object_key(prefix, _PREFIX_MARKER)
                if not await asyncio.to_thread(self._object_exists, key):
                    await asyncio.to_thread(self._put_bytes, key, b"", None, None)
            self._initialized = True
            logger.info(
                "kb_storage_minio_initialized",
                bucket=self._settings.bucket,
                endpoint=self._settings.endpoint,
                secure=self._settings.secure,
            )

    async def put_object(
        self,
        prefix: KBPrefix,
        name: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        if not isinstance(data, bytes | bytearray):
            raise TypeError("data must be bytes-like")
        key = object_key(prefix, name)
        with tracer.start_as_current_span("kb_storage.minio.put") as span:
            span.set_attribute("storage.key", key)
            span.set_attribute("storage.size", len(data))
            await asyncio.to_thread(
                self._put_bytes, key, bytes(data), content_type, dict(metadata or {})
            )
            logger.info("kb_object_put", bucket=self._settings.bucket, key=key, size=len(data))
            return key

    async def get_object(self, prefix: KBPrefix, name: str) -> bytes:
        key = object_key(prefix, name)
        with tracer.start_as_current_span("kb_storage.minio.get") as span:
            span.set_attribute("storage.key", key)
            try:
                payload = await asyncio.to_thread(self._read_bytes, key)
            except Exception as exc:
                raise KBStorageError(f"object not found: {key}") from exc
            span.set_attribute("storage.size", len(payload))
            return payload

    async def exists(self, prefix: KBPrefix, name: str) -> bool:
        return await asyncio.to_thread(self._object_exists, object_key(prefix, name))

    async def list_objects(self, prefix: KBPrefix) -> list[ObjectListing]:
        marker = object_key(prefix, _PREFIX_MARKER)
        with tracer.start_as_current_span("kb_storage.minio.list") as span:
            span.set_attribute("storage.prefix", prefix.value)
            raw = await asyncio.to_thread(self._list, prefix.value + "/")
            return [
                ObjectListing(key=item["key"], size=int(item.get("size", 0)), etag=item.get("etag"))
                for item in raw
                if item["key"] != marker
            ]

    async def delete_object(self, prefix: KBPrefix, name: str) -> bool:
        key = object_key(prefix, name)
        with tracer.start_as_current_span("kb_storage.minio.delete") as span:
            span.set_attribute("storage.key", key)
            removed = await asyncio.to_thread(self._delete, key)
            if removed:
                logger.info("kb_object_deleted", bucket=self._settings.bucket, key=key)
            return removed

    # ------------------------------------------------------------------
    # Sync helpers (executed on a worker thread via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._settings.bucket):
            self._client.make_bucket(self._settings.bucket, location=self._settings.region)
            logger.info("kb_bucket_created", bucket=self._settings.bucket)

    def _enable_versioning(self) -> None:
        # The minio SDK exposes ``set_bucket_versioning`` on newer releases;
        # call it via the ``VersioningConfig`` helper when present, else fall
        # back to a no-op so older test fakes don't have to implement it.
        try:
            minio_mod = _resolve_minio_module()
        except MinIODependencyError:
            return
        cfg_cls = getattr(getattr(minio_mod, "commonconfig", object()), "ENABLED", None)
        setter = getattr(self._client, "set_bucket_versioning", None)
        if cfg_cls is None or setter is None:
            return
        try:
            from minio.versioningconfig import VersioningConfig  # noqa: PLC0415

            setter(self._settings.bucket, VersioningConfig(cfg_cls))
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning("kb_versioning_setup_failed", error=str(exc))

    def _object_exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._settings.bucket, key)
            return True
        except Exception:
            return False

    def _put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None,
        metadata: dict[str, str] | None,
    ) -> None:
        self._client.put_object(
            self._settings.bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
            metadata=metadata or None,
        )

    def _read_bytes(self, key: str) -> bytes:
        response = self._client.get_object(self._settings.bucket, key)
        try:
            return response.read()
        finally:
            close = getattr(response, "close", None)
            release = getattr(response, "release_conn", None)
            if close is not None:
                close()
            if release is not None:
                release()

    def _list(self, prefix: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for obj in self._client.list_objects(self._settings.bucket, prefix=prefix, recursive=True):
            out.append(
                {
                    "key": getattr(obj, "object_name", "") or "",
                    "size": getattr(obj, "size", 0) or 0,
                    "etag": getattr(obj, "etag", None),
                }
            )
        return out

    def _delete(self, key: str) -> bool:
        if not self._object_exists(key):
            return False
        self._client.remove_object(self._settings.bucket, key)
        return True
