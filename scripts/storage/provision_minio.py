"""Idempotent MinIO KB-bucket provisioning runner (MET-476).

Reads ``MINIO_ENDPOINT`` / ``MINIO_ACCESS_KEY`` / ``MINIO_SECRET_KEY`` /
``MINIO_BUCKET`` (default ``metaforge-kb``) / ``MINIO_SECURE`` from the
environment, then ensures:

1. The KB bucket exists.
2. Versioning is enabled (datasheet history is immutable).
3. The four canonical prefix folders are present
   (``datasheets``, ``extracted-properties``, ``design-decisions``,
   ``bom-specs``).

Safe to run on every deploy — every step is idempotent.

Usage::

    pip install -e .[knowledge]
    MINIO_ENDPOINT=play.min.io:443 MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... \
        python scripts/storage/provision_minio.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from digital_twin.storage import (
    DEFAULT_ARCHIVE_AFTER_DAYS,
    DEFAULT_ARCHIVE_STORAGE_CLASS,
    DEFAULT_BUCKET_NAME,
    KBPrefix,
    MinIODependencyError,
    MinIOKBStorage,
    MinIOSettings,
)


async def _provision(
    bucket: str | None,
    *,
    archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
    storage_class: str = DEFAULT_ARCHIVE_STORAGE_CLASS,
) -> int:
    try:
        settings = MinIOSettings.from_env()
    except Exception as exc:  # noqa: BLE001 — surface env / config issues to the user
        print(f"[provision_minio] config error: {exc}", file=sys.stderr)
        return 2
    if bucket:
        settings = MinIOSettings(
            endpoint=settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            bucket=bucket,
            secure=settings.secure,
            region=settings.region,
        )
    try:
        storage = MinIOKBStorage(settings)
    except MinIODependencyError as exc:
        print(f"[provision_minio] {exc}", file=sys.stderr)
        return 3
    print(
        f"[provision_minio] endpoint={settings.endpoint} bucket={settings.bucket} "
        f"secure={settings.secure}"
    )
    await storage.initialize()
    for prefix in KBPrefix:
        objects = await storage.list_objects(prefix)
        print(f"  {prefix.value}/: {len(objects)} object(s)")
    try:
        await storage.apply_kb_lifecycle(
            archive_after_days=archive_after_days,
            storage_class=storage_class,
        )
        print(f"  lifecycle: transition to {storage_class} after {archive_after_days} days")
    except Exception as exc:  # noqa: BLE001 — surface the error but don't crash other steps
        print(f"[provision_minio] lifecycle apply failed: {exc}", file=sys.stderr)
        return 4
    print("[provision_minio] OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision the KB MinIO bucket (MET-476).")
    parser.add_argument(
        "--bucket",
        default=None,
        help=(
            f"Override MINIO_BUCKET / default {DEFAULT_BUCKET_NAME}. "
            "Useful for staging vs prod runs."
        ),
    )
    parser.add_argument(
        "--archive-after-days",
        type=int,
        default=DEFAULT_ARCHIVE_AFTER_DAYS,
        help=(
            f"Days before transition (default {DEFAULT_ARCHIVE_AFTER_DAYS}). "
            "Set to a smaller value for staging."
        ),
    )
    parser.add_argument(
        "--storage-class",
        default=DEFAULT_ARCHIVE_STORAGE_CLASS,
        help=(
            f"Lifecycle target storage class (default {DEFAULT_ARCHIVE_STORAGE_CLASS}). "
            "Requires the tier to be configured in MinIO; otherwise the rule is a no-op."
        ),
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        _provision(
            args.bucket,
            archive_after_days=args.archive_after_days,
            storage_class=args.storage_class,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
