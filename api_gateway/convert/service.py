"""Conversion service — orchestrates OCCT converter calls and caching."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.convert.service")

# Default cache location for development; override via env var or config.
_DEFAULT_CACHE_DIR = Path(os.getenv("CONVERT_CACHE_DIR", "/tmp/metaforge-convert-cache"))

# OCCT converter service URL (runs as sidecar in docker-compose)
_OCCT_URL = os.getenv("OCCT_CONVERTER_URL", "http://localhost:8100")


class ConversionError(RuntimeError):
    """The OCCT converter rejected the file (bad request, not a server crash).

    MET-652: previously a bare ``RuntimeError``, which the route handler
    never caught -- every rejection (e.g. "Can't export empty scenes!" for a
    STEP with no exportable solids) surfaced as an unhandled 500 with a full
    stack trace, even though it's a client-facing "this content can't be
    converted" case, not a server fault.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"OCCT converter returned {status_code}: {body}")


class ConversionService:
    """Manages STEP/IGES → GLB conversion with content-hash caching.

    The service computes a SHA-256 hash of the uploaded file content,
    checks for a cached result, and if not found calls the OCCT
    converter microservice over HTTP.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        occt_url: str | None = None,
    ) -> None:
        self.cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.occt_url = occt_url or _OCCT_URL

    @staticmethod
    def content_hash(data: bytes) -> str:
        """Compute SHA-256 hex digest of file content."""
        return hashlib.sha256(data).hexdigest()

    def _cache_path(self, file_hash: str, quality: str) -> Path:
        """Return the cache directory for a given hash + quality tier."""
        return self.cache_dir / file_hash / quality

    def get_cached(self, file_hash: str, quality: str) -> dict[str, Any] | None:
        """Return cached conversion result or None."""
        cache = self._cache_path(file_hash, quality)
        glb = cache / "model.glb"
        meta = cache / "metadata.json"
        if glb.exists() and meta.exists():
            return json.loads(meta.read_text())
        return None

    def get_glb_path(self, file_hash: str, quality: str) -> Path | None:
        """Return path to cached GLB file, or None."""
        glb = self._cache_path(file_hash, quality) / "model.glb"
        return glb if glb.exists() else None

    def get_metadata(self, file_hash: str, quality: str) -> dict[str, Any] | None:
        """Return cached metadata dict, or None."""
        meta = self._cache_path(file_hash, quality) / "metadata.json"
        if meta.exists():
            return json.loads(meta.read_text())
        return None

    def convert(
        self, file_bytes: bytes, filename: str, quality: str = "standard"
    ) -> dict[str, Any]:
        """Convert a CAD file to GLB.

        Returns a dict with keys: hash, glb_url, metadata, cached.
        """
        with tracer.start_as_current_span("convert_cad_file") as span:
            file_hash = self.content_hash(file_bytes)
            span.set_attribute("file.hash", file_hash)
            span.set_attribute("file.size", len(file_bytes))
            span.set_attribute("quality", quality)

            # Check cache
            cached_meta = self.get_cached(file_hash, quality)
            if cached_meta is not None:
                logger.info("conversion_cache_hit", hash=file_hash, quality=quality)
                return {
                    "hash": file_hash,
                    "glb_url": f"/v1/convert/{file_hash}/glb?quality={quality}",
                    "metadata": cached_meta,
                    "cached": True,
                }

            cache = self._cache_path(file_hash, quality)
            cache.mkdir(parents=True, exist_ok=True)

            try:
                self._call_occt_service(file_bytes, filename, quality, cache)
            except Exception as exc:
                span.record_exception(exc)
                shutil.rmtree(cache, ignore_errors=True)
                raise

            metadata = json.loads((cache / "metadata.json").read_text())
            logger.info(
                "conversion_complete",
                hash=file_hash,
                quality=quality,
                parts=len(metadata.get("parts", [])),
            )

            return {
                "hash": file_hash,
                "glb_url": f"/v1/convert/{file_hash}/glb?quality={quality}",
                "metadata": metadata,
                "cached": False,
            }

    def _call_occt_service(
        self, file_bytes: bytes, filename: str, quality: str, cache: Path
    ) -> None:
        """POST the file to the OCCT converter microservice and save results.

        Falls back to a stub metadata + empty GLB when the OCCT container
        is unreachable so the API stays functional during development.
        """
        url = f"{self.occt_url}/convert?quality={quality}"
        logger.info("occt_service_call", url=url, filename=filename, size=len(file_bytes))

        try:
            resp = httpx.post(
                url,
                files={"file": (filename, file_bytes, "application/octet-stream")},
                timeout=120.0,
            )
        except httpx.ConnectError:
            logger.warning(
                "occt_service_unavailable",
                url=url,
                fallback="stub_metadata",
            )
            self._write_fallback(cache, filename)
            return

        if resp.status_code != 200:
            raise ConversionError(resp.status_code, resp.text)

        result = resp.json()

        # Write metadata
        metadata = result["metadata"]
        (cache / "metadata.json").write_text(json.dumps(metadata, indent=2))

        # Decode and write GLB
        glb_bytes = base64.b64decode(result["glb_base64"])
        (cache / "model.glb").write_bytes(glb_bytes)

    @staticmethod
    def _write_fallback(cache: Path, filename: str) -> None:
        """Write stub metadata and an empty GLB when the converter is offline."""
        fallback_metadata: dict[str, Any] = {
            "parts": [
                {
                    "name": Path(filename).stem,
                    "mesh_name": "",
                    "children": [],
                    "bounding_box": {},
                }
            ],
            "materials": [],
            "stats": {
                "triangle_count": 0,
                "file_size": 0,
                "vertex_count": 0,
            },
            "converter_unavailable": True,
        }
        (cache / "metadata.json").write_text(json.dumps(fallback_metadata, indent=2))
        (cache / "model.glb").write_bytes(b"")
