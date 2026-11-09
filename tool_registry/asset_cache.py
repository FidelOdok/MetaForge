"""Verified immutable asset cache shared by source adapters.

Transport is injected. No provider credentials, engine imports or catalog state
live here. Files are copied to jobs so an engine cannot mutate the cached object.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import weakref
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_LOCKS: weakref.WeakKeyDictionary[Any, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


class AssetIntegrityError(ValueError):
    """A source or local cache did not match the immutable asset contract."""


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(65536), b""):
            digest.update(data)
    return digest.hexdigest()


class VerifiedAssetCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def fetch_to(
        self, *, url: str, sha256: str, size: int, target: Path, fetcher: Any, offline: bool = False
    ) -> bool:
        """Verify and materialize one object. Return True for a cache hit."""
        if not re.fullmatch("[0-9a-f]{64}", sha256):
            raise AssetIntegrityError("asset needs a SHA-256 digest")
        if type(size) is not int or size < 0:
            raise AssetIntegrityError("asset needs a non-negative integer size")
        path = self.root / sha256[:2] / sha256
        locks = _LOCKS.setdefault(asyncio.get_running_loop(), {})
        lock = locks.setdefault(str(path.resolve()), asyncio.Lock())
        async with lock:
            valid = path.is_file() and path.stat().st_size == size and file_digest(path) == sha256
            if not valid:
                if offline:
                    raise AssetIntegrityError("verified asset is not cached for offline use")
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=".fetch-", dir=path.parent)
                try:
                    count = 0
                    digest = hashlib.sha256()
                    with os.fdopen(fd, "wb") as output:
                        stream = getattr(fetcher, "iter_bytes", None)
                        if stream is None:
                            data = await fetcher.get(url)
                            if len(data) != size:
                                raise AssetIntegrityError("asset does not match its declared size")
                            digest.update(data)
                            output.write(data)
                            count = len(data)
                        else:
                            async for chunk in stream(url):
                                count += len(chunk)
                                if count > size:
                                    raise AssetIntegrityError(
                                        "asset exceeds declared size or byte limit"
                                    )
                                digest.update(chunk)
                                output.write(chunk)
                    if count != size or digest.hexdigest() != sha256:
                        raise AssetIntegrityError("asset does not match its size or sha256")
                    os.replace(name, path)
                finally:
                    Path(name).unlink(missing_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".materialize-", dir=target.parent)
            os.close(fd)
            try:
                shutil.copyfile(path, name)
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
            logger.info("asset_materialized", sha256=sha256, bytes=size, cache_hit=valid)
            return valid
