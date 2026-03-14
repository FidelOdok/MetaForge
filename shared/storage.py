"""Local filesystem storage for generated work products (MET-215).

Provides content-addressable file storage so that generated artifacts
(STEP files, meshes, FEA results, etc.) are persisted to disk and can
be referenced by path in WorkProduct metadata.

Usage::

    from shared.storage import default_storage

    path = default_storage.save("session-abc", "bracket.step", content_bytes)
    data = default_storage.get(path)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("shared.storage")

_DEFAULT_STORAGE_ROOT = os.path.join(os.path.expanduser("~"), ".metaforge", "work_products")


class FileStorageService:
    """Local filesystem storage for generated work products."""

    def __init__(self, storage_root: str | None = None) -> None:
        env_root = os.environ.get("METAFORGE_STORAGE_ROOT")
        resolved = storage_root or env_root or _DEFAULT_STORAGE_ROOT
        self._root = Path(resolved).resolve()
        logger.info(
            "file_storage_initialized",
            storage_root=str(self._root),
        )

    @property
    def root(self) -> Path:
        """Return the resolved storage root directory."""
        return self._root

    def save(self, session_id: str, filename: str, content: bytes) -> str:
        """Save content to disk under ``{root}/{session_id}/{hash}_{filename}``.

        Returns the absolute path of the saved file.
        """
        with tracer.start_as_current_span("storage.save") as span:
            span.set_attribute("storage.session_id", session_id)
            span.set_attribute("storage.filename", filename)
            span.set_attribute("storage.content_size", len(content))

            file_hash = self.content_hash(content)
            safe_name = f"{file_hash[:12]}_{filename}"
            session_dir = self._root / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            dest = session_dir / safe_name
            dest.write_bytes(content)

            abs_path = str(dest)
            span.set_attribute("storage.path", abs_path)
            logger.info(
                "file_saved",
                session_id=session_id,
                filename=filename,
                path=abs_path,
                content_hash=file_hash,
                size=len(content),
            )
            return abs_path

    def get(self, path: str) -> bytes:
        """Read and return file contents from *path*.

        Raises ``FileNotFoundError`` if the path does not exist.
        """
        with tracer.start_as_current_span("storage.get") as span:
            span.set_attribute("storage.path", path)
            p = Path(path)
            if not p.exists():
                logger.warning("file_not_found", path=path)
                raise FileNotFoundError(path)
            data = p.read_bytes()
            logger.debug("file_read", path=path, size=len(data))
            return data

    def list_files(self, session_id: str) -> list[str]:
        """Return absolute paths of all files stored for *session_id*."""
        with tracer.start_as_current_span("storage.list_files") as span:
            span.set_attribute("storage.session_id", session_id)
            session_dir = self._root / session_id
            if not session_dir.is_dir():
                logger.debug("session_dir_not_found", session_id=session_id)
                return []
            files = sorted(str(f) for f in session_dir.iterdir() if f.is_file())
            span.set_attribute("storage.file_count", len(files))
            logger.debug(
                "files_listed",
                session_id=session_id,
                count=len(files),
            )
            return files

    def delete(self, path: str) -> bool:
        """Delete the file at *path*. Returns ``True`` if deleted."""
        with tracer.start_as_current_span("storage.delete") as span:
            span.set_attribute("storage.path", path)
            p = Path(path)
            if not p.exists():
                logger.warning("delete_file_not_found", path=path)
                return False
            p.unlink()
            logger.info("file_deleted", path=path)
            return True

    @staticmethod
    def content_hash(content: bytes) -> str:
        """Return the SHA-256 hex digest of *content*."""
        return hashlib.sha256(content).hexdigest()


default_storage = FileStorageService()

__all__ = ["FileStorageService", "default_storage"]
