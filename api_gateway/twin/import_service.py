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

#: G-code a printer runs; ``api_gateway.library.jobs`` sends these.
GCODE_EXTENSIONS = (".gcode", ".gco", ".bgcode")
#: Pictures: reference photos, sketches, screenshots, marked-up drawings.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")

ALLOWED_EXTENSIONS = {
    # CAD / EDA source (parsed for metadata)
    ".step",
    ".stp",
    ".iges",
    ".igs",
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_pro",
    ".fcstd",
    # MET-483: the rest of the work-product modalities (data-modalities.md).
    # These ride the basic-metadata path — no parser needed — so the full
    # project (PRD, constraints, BOM, firmware, manufacturing, docs) can be
    # imported as typed, downloadable work products.
    ".md",
    ".txt",
    ".pdf",
    ".json",
    ".csv",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".hex",
    ".map",
    ".gbr",
    ".drl",
    ".xln",
    ".stl",
    ".glb",
    # Plugin file transfer: the rest of what a person brings to a design.
    # Meshes and print projects from a slicer or a generator, G-code for a
    # printer, and pictures (a reference photo, a sketch, a screenshot).
    ".gltf",
    ".obj",
    ".3mf",
    ".ply",
    *GCODE_EXTENSIONS,
    *IMAGE_EXTENSIONS,
}

#: A folder arrives as a zip, or as several files in one upload; a zip is
#: opened into its files, each checked against ALLOWED_EXTENSIONS.
ARCHIVE_EXTENSIONS = (".zip",)

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
    # MET-483 modalities
    ".gbr": "electronics",
    ".drl": "electronics",
    ".xln": "electronics",
    ".csv": "electronics",
    ".c": "firmware",
    ".h": "firmware",
    ".cpp": "firmware",
    ".hpp": "firmware",
    ".hex": "firmware",
    ".map": "firmware",
    ".stl": "mechanical",
    ".glb": "mechanical",
    ".gltf": "mechanical",
    ".obj": "mechanical",
    ".3mf": "manufacturing",
    ".ply": "mechanical",
    **dict.fromkeys(GCODE_EXTENSIONS, "manufacturing"),
    **dict.fromkeys(IMAGE_EXTENSIONS, "systems"),
    ".md": "systems",
    ".txt": "systems",
    ".pdf": "systems",
    ".json": "systems",
}

# Extension → WorkProductType mapping. These are sensible defaults; the
# import API's ``wp_type`` field overrides per file (e.g. a ``.json`` that
# is really a BOM or pinmap, a ``.md`` that is the PRD).
_EXT_TO_WP_TYPE: dict[str, WorkProductType] = {
    ".step": WorkProductType.CAD_MODEL,
    ".stp": WorkProductType.CAD_MODEL,
    ".iges": WorkProductType.CAD_MODEL,
    ".igs": WorkProductType.CAD_MODEL,
    ".fcstd": WorkProductType.CAD_MODEL,
    ".kicad_sch": WorkProductType.SCHEMATIC,
    ".kicad_pcb": WorkProductType.PCB_LAYOUT,
    ".kicad_pro": WorkProductType.DOCUMENTATION,
    # MET-483 modalities
    ".c": WorkProductType.FIRMWARE_SOURCE,
    ".h": WorkProductType.FIRMWARE_SOURCE,
    ".cpp": WorkProductType.FIRMWARE_SOURCE,
    ".hpp": WorkProductType.FIRMWARE_SOURCE,
    ".hex": WorkProductType.FIRMWARE_SOURCE,
    ".map": WorkProductType.FIRMWARE_SOURCE,
    ".csv": WorkProductType.BOM,
    ".gbr": WorkProductType.GERBER,
    ".drl": WorkProductType.MANUFACTURING_FILE,
    ".xln": WorkProductType.MANUFACTURING_FILE,
    ".stl": WorkProductType.MANUFACTURING_FILE,
    ".glb": WorkProductType.CAD_MODEL,
    ".gltf": WorkProductType.CAD_MODEL,
    ".obj": WorkProductType.CAD_MODEL,
    ".ply": WorkProductType.CAD_MODEL,
    ".3mf": WorkProductType.MANUFACTURING_FILE,
    **dict.fromkeys(GCODE_EXTENSIONS, WorkProductType.GCODE),
    **dict.fromkeys(IMAGE_EXTENSIONS, WorkProductType.IMAGE),
    ".md": WorkProductType.DOCUMENTATION,
    ".txt": WorkProductType.DOCUMENTATION,
    ".pdf": WorkProductType.DOCUMENTATION,
    ".json": WorkProductType.DOCUMENTATION,
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


#: JPEG start-of-frame markers, which carry the image's height and width.
_JPEG_FRAME_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def image_size(content: bytes) -> tuple[int, int] | None:
    """Pixel width and height of a PNG, GIF, JPEG or WebP, read from its header.

    Stdlib only: the gateway has no imaging library, and the size is all the
    twin records. ``None`` for SVG, a truncated file or anything unrecognised.
    """
    import struct

    try:
        if content[:8] == b"\x89PNG\r\n\x1a\n" and content[12:16] == b"IHDR":
            w, h = struct.unpack(">II", content[16:24])
            return int(w), int(h)
        if content[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", content[6:10])
            return int(w), int(h)
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            chunk = content[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(content[24:27], "little") + 1
                h = int.from_bytes(content[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", content[26:30])
                return int(w) & 0x3FFF, int(h) & 0x3FFF
            if chunk == b"VP8L":
                bits = int.from_bytes(content[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if content[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(content):
                if content[i] != 0xFF:
                    i += 1
                    continue
                marker = content[i + 1]
                if marker in _JPEG_FRAME_MARKERS:
                    h, w = struct.unpack(">HH", content[i + 5 : i + 9])
                    return int(w), int(h)
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + int.from_bytes(content[i + 2 : i + 4], "big")
    except (struct.error, IndexError):
        return None
    return None


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
                return await self._extract_fcstd_metadata(content, filename)
            elif ext in IMAGE_EXTENSIONS:
                meta = self._basic_metadata(content, filename)
                size = image_size(content)
                if size is not None:
                    meta["width_px"], meta["height_px"] = size
                return meta
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
        except httpx.RequestError as exc:
            # MET-705: only ConnectError was caught, so a converter that is
            # *reachable but broken* -- accepts then drops (ReadError), hangs
            # (ReadTimeout, on a 120s budget), or breaks protocol -- escaped
            # this method and failed the whole import. Both the non-200 branch
            # above and the fallback below make clear extraction is meant to be
            # best-effort, so a narrow except was the bug, not the intent.
            logger.warning(
                "occt_request_failed",
                url=url,
                filename=filename,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        except ValueError as exc:
            # A 200 carrying a non-JSON body raises out of resp.json().
            logger.warning(
                "occt_bad_payload",
                url=url,
                filename=filename,
                error=str(exc),
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

    async def _extract_fcstd_metadata(self, content: bytes, filename: str) -> dict[str, Any]:
        """Extract geometry metadata from a .FCStd file.

        .FCStd is a ZIP archive containing .brep files (OpenCASCADE BRep format).
        Extracts the largest .brep and sends it to the OCCT converter for geometry
        analysis.  Falls back to size-only metadata if ZIP parsing fails or OCCT
        is unavailable.
        """
        base: dict[str, Any] = {"format": "fcstd", "file_size": len(content)}

        # Step 1: unzip and find .brep files
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                brep_names = [n for n in zf.namelist() if n.lower().endswith(".brep")]
                if not brep_names:
                    logger.debug(
                        "fcstd_no_brep_found",
                        filename=filename,
                    )
                    return base
                base["part_count"] = len(brep_names)
                # Use the largest .brep as the representative body
                largest = max(brep_names, key=lambda n: zf.getinfo(n).file_size)
                brep_bytes = zf.read(largest)
        except Exception as exc:
            logger.warning(
                "fcstd_zip_parse_failed",
                filename=filename,
                error=str(exc),
            )
            return base

        # Step 2: send to OCCT converter (same pattern as _extract_cad_metadata)
        # base fields (format, file_size, part_count) take priority over OCCT fields
        # so that FCStd-level metadata is not overwritten by the BRep sub-file stats.
        try:
            occt_meta = await self._extract_cad_metadata(brep_bytes, "model.brep")
            return {**occt_meta, **base}
        except Exception as exc:
            logger.warning(
                "fcstd_occt_failed",
                filename=filename,
                error=str(exc),
            )
            return base

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
