"""KB object-storage protocol + in-memory backend (MET-476).

Defines the canonical bucket layout (four prefixes the KB pipeline writes
under), the read/write protocol the rest of the platform programs against,
and a dict-backed implementation used in tests and local development.

The production adapter (``MinIOKBStorage``) lives in
:mod:`digital_twin.storage.minio_adapter` so this module stays free of the
optional ``minio`` dependency and is always importable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.storage.kb_storage")


DEFAULT_BUCKET_NAME = "metaforge-kb"
"""The single KB bucket MET-476 standardises on. One bucket per environment;
the four :class:`KBPrefix` values partition its contents."""


class KBPrefix(StrEnum):
    """Top-level object prefixes within the KB bucket.

    Aligns with MET-476's required folder structure. Using a closed enum
    rather than free-form strings keeps writers from drifting and lets
    readers exhaustively enumerate the layout (e.g. for provisioning).
    """

    DATASHEETS = "datasheets"
    EXTRACTED_PROPERTIES = "extracted-properties"
    DESIGN_DECISIONS = "design-decisions"
    BOM_SPECS = "bom-specs"


class KBStorageError(RuntimeError):
    """Raised when an object-storage operation fails in a recoverable way."""


@dataclass(frozen=True)
class ObjectListing:
    """One entry returned by :meth:`KBObjectStorage.list_objects`."""

    key: str
    """Full object key (``"<prefix>/<name>"``)."""
    size: int = 0
    """Object size in bytes (``0`` when unknown)."""
    etag: str | None = None
    """Object ETag when the backend exposes one."""


def object_key(prefix: KBPrefix, name: str) -> str:
    """Compose the canonical ``<prefix>/<name>`` object key.

    Rejects empty names + names that try to walk above the prefix —
    callers occasionally feed in untrusted MPNs / filenames.
    """
    if not name or not isinstance(name, str):
        raise ValueError("object name must be a non-empty string")
    cleaned = name.lstrip("/")
    if not cleaned or cleaned.startswith("..") or "/../" in cleaned or cleaned.endswith("/.."):
        raise ValueError(f"invalid object name: {name!r}")
    return f"{prefix.value}/{cleaned}"


@runtime_checkable
class KBObjectStorage(Protocol):
    """Provider-agnostic read/write contract for the KB bucket."""

    async def initialize(self) -> None:
        """Ensure the bucket exists with the expected layout.

        Idempotent: callers (gateway boot, provisioning script) invoke this
        on every start. Implementations must also create / verify the four
        prefix markers so administrative tools see the folders.
        """
        ...

    async def put_object(
        self,
        prefix: KBPrefix,
        name: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """Store ``data`` at ``<prefix>/<name>``; return the object key."""
        ...

    async def get_object(self, prefix: KBPrefix, name: str) -> bytes:
        """Read the object stored at ``<prefix>/<name>``."""
        ...

    async def exists(self, prefix: KBPrefix, name: str) -> bool:
        """``True`` when the object is present."""
        ...

    async def list_objects(self, prefix: KBPrefix) -> list[ObjectListing]:
        """List every object under ``prefix`` (excluding the prefix marker)."""
        ...

    async def delete_object(self, prefix: KBPrefix, name: str) -> bool:
        """Remove the object; return ``True`` when it existed."""
        ...


@dataclass
class _StoredObject:
    data: bytes
    content_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


_PREFIX_MARKER = ".keep"
"""Zero-byte sentinel object name written to each prefix during
``initialize`` so administrative tools display the folder even when it
holds no real data yet."""


class InMemoryKBStorage:
    """Dict-backed ``KBObjectStorage`` for tests + local development."""

    def __init__(self, bucket: str = DEFAULT_BUCKET_NAME) -> None:
        self._bucket = bucket
        self._objects: dict[str, _StoredObject] = {}
        self._initialized = False

    @property
    def bucket(self) -> str:
        return self._bucket

    async def initialize(self) -> None:
        if self._initialized:
            return
        with tracer.start_as_current_span("kb_storage.initialize") as span:
            span.set_attribute("storage.bucket", self._bucket)
            for prefix in KBPrefix:
                key = object_key(prefix, _PREFIX_MARKER)
                self._objects.setdefault(key, _StoredObject(data=b""))
            self._initialized = True
            logger.info(
                "kb_storage_initialized",
                bucket=self._bucket,
                prefixes=[p.value for p in KBPrefix],
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
        self._objects[key] = _StoredObject(
            data=bytes(data),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        logger.info("kb_object_put", bucket=self._bucket, key=key, size=len(data))
        return key

    async def get_object(self, prefix: KBPrefix, name: str) -> bytes:
        key = object_key(prefix, name)
        obj = self._objects.get(key)
        if obj is None:
            raise KBStorageError(f"object not found: {key}")
        return obj.data

    async def exists(self, prefix: KBPrefix, name: str) -> bool:
        return object_key(prefix, name) in self._objects

    async def list_objects(self, prefix: KBPrefix) -> list[ObjectListing]:
        wanted = prefix.value + "/"
        marker = object_key(prefix, _PREFIX_MARKER)
        listings: list[ObjectListing] = []
        for key, obj in sorted(self._objects.items()):
            if not key.startswith(wanted) or key == marker:
                continue
            listings.append(ObjectListing(key=key, size=len(obj.data)))
        return listings

    async def delete_object(self, prefix: KBPrefix, name: str) -> bool:
        key = object_key(prefix, name)
        existed = self._objects.pop(key, None) is not None
        if existed:
            logger.info("kb_object_deleted", bucket=self._bucket, key=key)
        return existed
