"""Copy every bucket and object from one S3 store to another (FORGE-577).

Used once per existing host to move its data from the old MinIO into the
SeaweedFS object store. Runs anywhere the ``minio`` client is installed (the
gateway image has it) and both stores are reachable::

    SRC_ENDPOINT=minio-old:9000 SRC_ACCESS_KEY=... SRC_SECRET_KEY=... \\
    DST_ENDPOINT=object-store:9000 DST_ACCESS_KEY=... DST_SECRET_KEY=... \\
    python scripts/storage/copy_objects.py [--dry-run]

Safe to run again: an object already in the destination with the same size
is skipped. It never deletes anything. It ends by comparing object counts
and total bytes per bucket, and exits non-zero if they differ.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from minio import Minio
from minio.error import S3Error


@dataclass
class Totals:
    objects: int = 0
    size: int = 0


def _client(prefix: str) -> Minio:
    endpoint = os.environ[f"{prefix}_ENDPOINT"]
    return Minio(
        endpoint,
        access_key=os.environ[f"{prefix}_ACCESS_KEY"],
        secret_key=os.environ[f"{prefix}_SECRET_KEY"],
        secure=os.environ.get(f"{prefix}_SECURE", "false").lower() in ("1", "true", "yes"),
    )


def _existing_size(dst: Minio, bucket: str, name: str) -> int | None:
    try:
        return int(dst.stat_object(bucket, name).size or 0)
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
            return None
        raise


def _totals(client: Minio, bucket: str) -> Totals:
    totals = Totals()
    for obj in client.list_objects(bucket, recursive=True):
        totals.objects += 1
        totals.size += int(obj.size or 0)
    return totals


def copy_all(src: Minio, dst: Minio, *, dry_run: bool = False) -> bool:
    """Copy everything; return whether every bucket matches afterwards."""
    ok = True
    for bucket in src.list_buckets():
        name = bucket.name
        if not dry_run and not dst.bucket_exists(name):
            dst.make_bucket(name)
        copied = skipped = 0
        for obj in src.list_objects(name, recursive=True):
            key = obj.object_name
            size = int(obj.size or 0)
            if not dry_run and _existing_size(dst, name, key) == size:
                skipped += 1
                continue
            if dry_run:
                copied += 1
                continue
            stat = src.stat_object(name, key)
            response = src.get_object(name, key)
            try:
                dst.put_object(
                    name,
                    key,
                    response,
                    length=size,
                    content_type=stat.content_type or "application/octet-stream",
                    metadata={
                        k: v
                        for k, v in (stat.metadata or {}).items()
                        if k.lower().startswith("x-amz-meta-")
                    },
                )
            finally:
                response.close()
                response.release_conn()
            copied += 1
        before = _totals(src, name)
        after = _totals(dst, name) if not dry_run else before
        same = (before.objects, before.size) == (after.objects, after.size)
        ok = ok and same
        print(
            f"{name}: {'would copy' if dry_run else 'copied'} {copied}, skipped {skipped}; "
            f"source {before.objects} objects / {before.size} bytes, "
            f"destination {after.objects} / {after.size}" + ("" if same else "  MISMATCH")
        )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="list what would be copied")
    args = parser.parse_args()
    ok = copy_all(_client("SRC"), _client("DST"), dry_run=args.dry_run)
    if not ok:
        print("object counts or sizes differ; see MISMATCH above", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
