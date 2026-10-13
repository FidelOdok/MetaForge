"""Vendors with no ordering API: hand the person the files and the page (FORGE-614).

Most mechanical services (Xometry, SendCutSend) and some PCB houses (AISLER)
offer no API a buyer can quote or order through, and Craftcloud's
integration is itself a link handoff. So the route and DFM phases still need
a concrete "send it here": which vendor takes this service, where to upload,
which of the part's files it accepts, and where to download each file.

That is all this does. It sends nothing anywhere and calls no vendor: the
catalogue below is data, and the person uploads the files themselves. The
entries were compiled from public information and have not been checked
against each vendor's live upload page, so the result says so and tells the
person to confirm the accepted formats when they upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.vendors.handoffs")

#: When the catalogue was compiled. Not a check of each vendor's live site.
CATALOGUE_CHECKED = "2026-10-13"

__all__ = ["CATALOGUE_CHECKED", "HANDOFFS", "Handoff", "handoffs_for"]


@dataclass(frozen=True)
class Handoff:
    id: str
    name: str
    url: str
    services: tuple[str, ...]
    #: File extensions the vendor's upload accepts, lower case, no dot.
    formats: tuple[str, ...]
    based_in: str
    note: str = ""


HANDOFFS: tuple[Handoff, ...] = (
    Handoff(
        id="aisler",
        name="AISLER",
        url="https://aisler.net",
        services=("pcb_fabrication", "pcb_assembly", "stencil"),
        formats=("kicad_pcb", "zip"),
        based_in="Germany (EU)",
        note="Takes a KiCad board file directly, or a zipped Gerber set.",
    ),
    Handoff(
        id="craftcloud",
        name="Craftcloud",
        url="https://craftcloud3d.com",
        services=("additive",),
        formats=("stl", "obj", "3mf"),
        based_in="Germany (EU); a marketplace of print services",
        note="Compares print services' prices for the uploaded mesh.",
    ),
    Handoff(
        id="xometry",
        name="Xometry",
        url="https://www.xometry.com",
        services=("cnc", "sheet_metal", "additive", "moulding", "laser_cutting"),
        formats=("step", "stp", "iges", "igs", "stl", "x_t", "sldprt"),
        based_in="US; xometry.eu serves Europe",
        note="Instant quote from an uploaded CAD file.",
    ),
    Handoff(
        id="sendcutsend",
        name="SendCutSend",
        url="https://sendcutsend.com",
        services=("laser_cutting", "sheet_metal"),
        formats=("dxf", "dwg", "ai", "eps"),
        based_in="US",
        note="Flat parts from 2D outlines; bends are specified on upload.",
    ),
)


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


async def handoffs_for(service: str, work_product_ids: list[str]) -> dict[str, Any]:
    """Vendors that take ``service``, and which of the given files each accepts."""
    from api_gateway.twin.routes import get_twin

    with tracer.start_as_current_span("vendor.handoffs") as span:
        span.set_attribute("vendor.service", service)
        twin = get_twin()
        files: list[dict[str, Any]] = []
        for raw in work_product_ids:
            try:
                wp = await twin.get_work_product(UUID(str(raw)))
            except ValueError:
                wp = None
            if wp is None:
                files.append({"work_product_id": raw, "found": False})
                continue
            meta = wp.metadata or {}
            filename = str(meta.get("original_filename") or "") or (
                f"{wp.name}.{wp.format}" if wp.format else wp.name
            )
            files.append(
                {
                    "work_product_id": str(wp.id),
                    "found": True,
                    "filename": filename,
                    "format": _extension(filename) or (wp.format or "").lower(),
                    "has_file": bool(wp.file_path or meta.get("minio_object_key")),
                    "download_path": f"/v1/twin/nodes/{wp.id}/file?download=true",
                }
            )
        vendors = []
        for vendor in HANDOFFS:
            if service not in vendor.services:
                continue
            usable = [
                f["work_product_id"]
                for f in files
                if f.get("found") and f.get("has_file") and f["format"] in vendor.formats
            ]
            vendors.append(
                {
                    "id": vendor.id,
                    "name": vendor.name,
                    "url": vendor.url,
                    "based_in": vendor.based_in,
                    "accepts": list(vendor.formats),
                    "usable_files": usable,
                    "ready": bool(usable),
                    "note": vendor.note,
                }
            )
    missing = [f["work_product_id"] for f in files if not f.get("found")]
    logger.info("vendor_handoffs_listed", service=service, vendors=len(vendors), files=len(files))
    return {
        "service": service,
        "vendors": vendors,
        "files": files,
        "not_found": missing,
        "catalogue_checked": CATALOGUE_CHECKED,
        "how": (
            "Nothing was sent. These vendors have no ordering API: give the person the page "
            "and the files (download_path on the MetaForge gateway). A vendor with no usable "
            "file needs one exported first, in a format it accepts. The accepted formats come "
            f"from MetaForge's catalogue (compiled {CATALOGUE_CHECKED}, not checked against "
            "each vendor's live page): tell the person to confirm them on upload. "
            "Any price they get there is a vendor quote: record it with twin.record_document "
            "or ask for it to be recorded, with the vendor and date."
        ),
    }
