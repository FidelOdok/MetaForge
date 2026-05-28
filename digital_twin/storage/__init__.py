"""KB object-storage layer (MET-476).

MinIO/S3 is the authoritative source of truth for L1-L3 knowledge-base
data — datasheets, extracted properties, design decisions, BOM specs.
Neo4j and pgvector are regenerable indices/caches that can be rebuilt
from object storage at any time.

This package exposes:

* :class:`KBPrefix` — the four canonical folder prefixes the bucket holds.
* :data:`DEFAULT_BUCKET_NAME` — the bucket name MET-476 standardises on.
* :class:`KBObjectStorage` — provider-agnostic put / get / list / exists
  / delete protocol the rest of the platform depends on.
* :class:`InMemoryKBStorage` — dict-backed test/dev backend.
* :class:`MinIOKBStorage` — production adapter; lazy-imports ``minio`` so
  the package stays importable in environments without the optional
  dependency installed.
"""

from digital_twin.storage.kb_storage import (
    DEFAULT_BUCKET_NAME,
    InMemoryKBStorage,
    KBObjectStorage,
    KBPrefix,
    KBStorageError,
    ObjectListing,
)
from digital_twin.storage.minio_adapter import (
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_ARCHIVE_STORAGE_CLASS,
    LIFECYCLE_RULE_ID,
    MinIODependencyError,
    MinIOKBStorage,
    MinIOSettings,
)

__all__ = [
    "DEFAULT_ARCHIVE_AFTER_DAYS",
    "DEFAULT_ARCHIVE_STORAGE_CLASS",
    "DEFAULT_BUCKET_NAME",
    "InMemoryKBStorage",
    "KBObjectStorage",
    "KBPrefix",
    "KBStorageError",
    "LIFECYCLE_RULE_ID",
    "MinIODependencyError",
    "MinIOKBStorage",
    "MinIOSettings",
    "ObjectListing",
]
