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
    # FORGE-753: online laser and sheet services by region, from each vendor's
    # own site (checked 2026-11-03). Whether they cut wood is in the note:
    # several cut metal only.
    Handoff(
        id="razorlab",
        name="RazorLAB",
        url="https://razorlab.online/",
        services=("laser_cutting",),
        formats=("ai", "svg", "dxf"),
        based_in="London, UK",
        note="Cuts plywood and MDF (thicknesses not published), acrylic, card, fabric. "
        "AI or SVG preferred, DXF usually fine; 20 MB maximum.",
    ),
    Handoff(
        id="cutlasercut",
        name="CutLaserCut",
        url="https://cutlasercut.com/",
        services=("laser_cutting",),
        formats=("ai", "eps", "dxf", "cdr", "svg", "pdf"),
        based_in="London, UK",
        note="Birch plywood 3, 6, 9 and 12 mm (thin birch 0.4 to 1.5 mm). DXF as AutoCAD 2004.",
    ),
    Handoff(
        id="fractory",
        name="Fractory",
        url="https://fractory.com/",
        services=("laser_cutting", "sheet_metal", "cnc"),
        formats=("step", "stp", "sldprt", "ipt", "dxf"),
        based_in="Manchester, UK",
        note="Metals only; no wood or plywood. 3D files preferred, DXF for flat cutting.",
    ),
    Handoff(
        id="formulor",
        name="Formulor",
        url="https://www.formulor.de/en/",
        services=("laser_cutting",),
        formats=("svg", "dxf", "pdf", "eps", "ai"),
        based_in="Pfinztal, Germany (EU)",
        note="Plywood: beech 2 mm, birch 0.8 and 3 mm, poplar 4 and 5 mm; "
        "also MDF, veneer, acrylic.",
    ),
    Handoff(
        id="laserboost",
        name="LaserBoost",
        url="https://www.laserboost.com/en/",
        services=("laser_cutting", "sheet_metal"),
        formats=("dxf", "dwg", "svg", "ai", "pdf", "eps", "step", "sldprt"),
        based_in="Madrid, Spain (EU)",
        note="Metals only, 0.5 to 15 mm, sheets up to 1500 x 3000 mm; no wood.",
    ),
    Handoff(
        id="xometry_eu",
        name="Xometry Europe",
        url="https://xometry.eu/en/",
        services=("laser_cutting", "sheet_metal", "additive"),
        formats=("step", "stp", "sldprt", "stl", "ipt", "3dxml", "catpart", "prt", "sat", "dxf"),
        based_in="Hohenbrunn, Germany (EU)",
        note="DXF preferred for sheet cutting; DWG and PDF go to a manual quote. Wood not stated.",
    ),
    Handoff(
        id="oshcut",
        name="OSH Cut",
        url="https://www.oshcut.com/",
        services=("laser_cutting", "sheet_metal"),
        formats=("dxf", "svg", "ai", "step", "stp", "sldprt", "iges", "igs", "x_t", "sat"),
        based_in="Spanish Fork, Utah, US",
        note="Metal sheet and tube only, with bending and hardware insertion; no wood.",
    ),
    Handoff(
        id="ponoko",
        name="Ponoko",
        url="https://www.ponoko.com/",
        services=("laser_cutting", "sheet_metal", "cnc", "additive"),
        formats=("dxf", "svg", "ai", "eps", "pdf", "step", "stp", "stl", "iges", "igs", "3mf"),
        based_in="Oakland, California, US",
        note="Plywood (birch, coloured, bamboo) up to 6.7 mm for laser cutting. DXF recommended.",
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
