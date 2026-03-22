"""Import service — extracts metadata from uploaded design files.

Dispatches to the appropriate adapter (OCCT, KiCad) based on file
extension, with graceful fallback to basic metadata when containers
are unavailable.
"""

from __future__ import annotations

import os
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.import_service")

ALLOWED_EXTENSIONS = {
    ".step",
    ".stp",
    ".iges",
    ".igs",
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_pro",
    ".fcstd",
}

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

_OCCT_URL = os.getenv("OCCT_CONVERTER_URL", "http://localhost:8100")
_KICAD_URL = os.getenv("METAFORGE_ADAPTER_KICAD_URL", "http://localhost:8102")

# Extension → domain mapping
_EXT_TO_DOMAIN: dict[str, str] = {
    ".step": "mechanical",
    ".stp": "mechanical",
    ".iges": "mechanical",
    ".igs": "mechanical",
    ".fcstd": "mechanical",
    ".kicad_sch": "electronics",
    ".kicad_pcb": "electronics",
    ".kicad_pro": "electronics",
}

# Extension → WorkProductType mapping
_EXT_TO_WP_TYPE: dict[str, WorkProductType] = {
    ".step": WorkProductType.CAD_MODEL,
    ".stp": WorkProductType.CAD_MODEL,
    ".iges": WorkProductType.CAD_MODEL,
    ".igs": WorkProductType.CAD_MODEL,
    ".fcstd": WorkProductType.CAD_MODEL,
    ".kicad_sch": WorkProductType.SCHEMATIC,
    ".kicad_pcb": WorkProductType.PCB_LAYOUT,
    ".kicad_pro": WorkProductType.DOCUMENTATION,
}


def get_extension(filename: str) -> str:
    """Return the lowercased file extension, handling double extensions.

    KiCad files have double extensions like ``.kicad_sch``, so we check
    for known double-dot patterns first.
    """
    name_lower = filename.lower()
    for ext in (".kicad_sch", ".kicad_pcb", ".kicad_pro"):
        if name_lower.endswith(ext):
            return ext
    return Path(filename).suffix.lower()


def infer_domain(ext: str) -> str:
    """Infer domain from file extension."""
    return _EXT_TO_DOMAIN.get(ext, "mechanical")


def infer_wp_type(ext: str) -> WorkProductType:
    """Infer WorkProductType from file extension."""
    return _EXT_TO_WP_TYPE.get(ext, WorkProductType.CAD_MODEL)


class ImportService:
    """Extracts metadata from uploaded design files."""

    def __init__(
        self,
        occt_url: str | None = None,
        kicad_url: str | None = None,
    ) -> None:
        self.occt_url = occt_url or _OCCT_URL
        self.kicad_url = kicad_url or _KICAD_URL

    async def extract_metadata(self, content: bytes, filename: str) -> dict[str, Any]:
        """Extract metadata from file content based on extension.

        Returns a dict of metadata that will be stored in
        WorkProduct.metadata.
        """
        with tracer.start_as_current_span("import.extract_metadata") as span:
            ext = get_extension(filename)
            span.set_attribute("import.extension", ext)
            span.set_attribute("import.filename", filename)
            span.set_attribute("import.size", len(content))

            if ext in (".step", ".stp", ".iges", ".igs"):
                return await self._extract_cad_metadata(content, filename)
            elif ext == ".kicad_sch":
                return await self._extract_kicad_sch_metadata(content, filename)
            elif ext == ".kicad_pcb":
                return await self._extract_kicad_pcb_metadata(content, filename)
            elif ext == ".fcstd":
                return self._extract_fcstd_metadata(content, filename)
            else:
                return self._basic_metadata(content, filename)

    async def _extract_cad_metadata(self, content: bytes, filename: str) -> dict[str, Any]:
        """Extract CAD metadata via OCCT converter."""
        url = f"{self.occt_url}/convert?quality=preview"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    url,
                    files={"file": (filename, content, "application/octet-stream")},
                )
            if resp.status_code == 200:
                result = resp.json()
                meta = result.get("metadata", {})
                stats = meta.get("stats", {})
                parts = meta.get("parts", [])

                # Extract bounding box from first part
                bbox = {}
                if parts and "bounding_box" in parts[0]:
                    bbox = parts[0]["bounding_box"]

                return {
                    "source": "occt_converter",
                    "part_count": len(parts),
                    "part_names": [p.get("name", "") for p in parts],
                    "triangle_count": stats.get("triangle_count", 0),
                    "vertex_count": stats.get("vertex_count", 0),
                    "bounding_box": bbox,
                    "file_size": len(content),
                }
            else:
                logger.warning(
                    "occt_extraction_failed",
                    status=resp.status_code,
                    filename=filename,
                )
        except httpx.ConnectError:
            logger.warning(
                "occt_unavailable_for_import",
                url=url,
                filename=filename,
            )

        return self._basic_metadata(content, filename)

    async def _extract_kicad_sch_metadata(self, content: bytes, filename: str) -> dict[str, Any]:
        """Extract schematic metadata — parse S-expression for component count."""
        meta = self._basic_metadata(content, filename)
        meta["source"] = "kicad_parser"

        # Parse KiCad 7+ S-expression schematic for basic stats
        try:
            text = content.decode("utf-8", errors="replace")
            meta["component_count"] = text.count("(symbol (lib_id")
            meta["wire_count"] = text.count("(wire (pts")
            meta["label_count"] = text.count("(label ")
            meta["net_count"] = text.count("(net_name ")
        except Exception as exc:
            logger.warning(
                "kicad_sch_parse_failed",
                filename=filename,
                error=str(exc),
            )

        return meta

    async def _extract_kicad_pcb_metadata(self, content: bytes, filename: str) -> dict[str, Any]:
        """Extract PCB metadata — parse S-expression for layer/track info."""
        meta = self._basic_metadata(content, filename)
        meta["source"] = "kicad_parser"

        try:
            text = content.decode("utf-8", errors="replace")
            meta["footprint_count"] = text.count("(footprint ")
            meta["track_count"] = text.count("(segment ") + text.count("(arc (start")
            meta["via_count"] = text.count("(via ")
            meta["zone_count"] = text.count("(zone ")
            # Extract board dimensions from general section
            meta["layer_count"] = text.count("(layer ") - text.count("(layers ")
        except Exception as exc:
            logger.warning(
                "kicad_pcb_parse_failed",
                filename=filename,
                error=str(exc),
            )

        return meta

    def _extract_fcstd_metadata(self, content: bytes, filename: str) -> dict[str, Any]:
        """Extract FreeCAD metadata — list entries in the ZIP archive."""
        meta = self._basic_metadata(content, filename)
        meta["source"] = "fcstd_parser"

        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                entries = zf.namelist()
                meta["archive_entries"] = entries
                meta["entry_count"] = len(entries)
                meta["has_gui_document"] = "GuiDocument.xml" in entries
                meta["has_document"] = "Document.xml" in entries
        except (zipfile.BadZipFile, Exception) as exc:
            logger.warning(
                "fcstd_parse_failed",
                filename=filename,
                error=str(exc),
            )

        return meta

    @staticmethod
    def _basic_metadata(content: bytes, filename: str) -> dict[str, Any]:
        """Return basic metadata for any file."""
        from shared.storage import FileStorageService

        return {
            "source": "basic",
            "file_size": len(content),
            "content_hash": FileStorageService.content_hash(content),
            "original_filename": filename,
        }
