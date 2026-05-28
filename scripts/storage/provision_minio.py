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
    DEFAULT_BUCKET_NAME,
    KBPrefix,
    MinIODependencyError,
    MinIOKBStorage,
    MinIOSettings,
)


async def _provision(bucket: str | None) -> int:
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
    args = parser.parse_args(argv)
    return asyncio.run(_provision(args.bucket))


if __name__ == "__main__":
    raise SystemExit(main())
