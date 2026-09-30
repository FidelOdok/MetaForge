"""Manufacture-release: chain a real committed work product's geometry
into a real manufacturing output file (FORGE-294, gap G-H2).

``cadquery.export_geometry`` already exports STEP/STL/OBJ/BREP/AMF/SVG
today, generically, for any CAD file on the shared adapter workspace --
not a stub. The missing piece was end-to-end wiring: a work product's
*committed* geometry lives in MinIO (via ``twin_core``), not on any
adapter's local disk, and nothing chained "resolve the real committed
file -> stage it onto the shared workspace -> export it -> hand the
result back" into one capability. ``twin.stage_work_product_file``
(MET-618, ``api_gateway/twin/blob_stager.py``) already does the first
half (real MinIO-first blob resolution, written onto
``ADAPTER_WORKSPACE_DIR`` so any adapter container can load it); this
module is the second half.

Deliberately two processes, one format each -- the Jira gap list's own
"3MF/STL for printing, STEP + drawing for CNC, flat patterns for sheet
metal" breakdown has no single existing capability behind most of it:

- **3MF** is not in ``cadquery.export_geometry``'s ``supported_export_
  formats`` (only step/stl/obj/brep/amf/svg) -- would need new library
  work, a separate ticket. 3D-print release ships STL only.
- **"...+ drawing" for CNC** needs FORGE-293's 2D technical drawing
  generation, which does not exist anywhere in this codebase yet (zero
  drawing-generation capability, confirmed during scoping). CNC release
  ships the STEP file alone; the drawing half stays explicitly deferred
  pending FORGE-293.
- **Sheet-metal flat patterns** need a flat-pattern geometry model this
  codebase doesn't have at all (same finding FORGE-273's own scoping
  reached independently) -- a separate, substantial ticket.

No "manufacturing process" attribute is stored on the work product --
process is a per-call parameter, matching the ticket's own "process
choice" wizard wording: a choice made at release time, not a persisted
classification.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.manufacture_release")

_RELEASE_SUBDIR = "_manufacture_releases"

#: process -> (export format, content-type-ish label kept for logging only;
#: the route owns the actual HTTP content-type mapping).
PROCESS_EXPORT_FORMATS: dict[str, str] = {
    "3d_print": "stl",
    "cnc": "step",
}

#: Work-product source formats ``cadquery.export_geometry`` can actually
#: read -- it calls ``cq.importers.importStep`` unconditionally regardless
#: of the target export format, so a non-STEP source fails confusingly
#: downstream if not rejected here with a clear message first.
_SUPPORTED_SOURCE_FORMATS = {"step", "stp"}


def make_manufacture_release(
    twin: Any,
    *,
    blob_stager: Any,
    mcp_bridge: Any,
    workspace_dir: Path | None = None,
) -> Any:
    """Return an async ``release(*, work_product_id, process) -> dict``
    bound to a twin + blob stager + mcp_bridge (a real
    ``cadquery.export_geometry`` call).

    ``workspace_dir`` overrides ``ADAPTER_WORKSPACE_DIR`` (default
    ``/workspace``) -- the directory the gateway and every adapter
    container mount the same volume at, so a path written or read here is
    immediately visible to both sides without another network hop.
    """

    root = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))

    async def release(*, work_product_id: str, process: str) -> dict[str, Any]:
        with tracer.start_as_current_span("manufacture.release") as span:
            span.set_attribute("manufacture.work_product_id", work_product_id)
            span.set_attribute("manufacture.process", process)

            if process not in PROCESS_EXPORT_FORMATS:
                raise ValueError(
                    f"twin.manufacture_release: unknown process {process!r}, "
                    f"expected one of {sorted(PROCESS_EXPORT_FORMATS)}"
                )
            output_format = PROCESS_EXPORT_FORMATS[process]

            try:
                uid = UUID(work_product_id)
            except ValueError as exc:
                raise ValueError(
                    f"twin.manufacture_release: invalid work_product_id {work_product_id!r}"
                ) from exc

            wp = await twin.get_work_product(uid)
            if wp is None:
                raise ValueError(f"twin.manufacture_release: no work_product {work_product_id!r}")
            wp_format = (wp.format or "").lower()
            if wp_format not in _SUPPORTED_SOURCE_FORMATS:
                raise ValueError(
                    f"twin.manufacture_release: work_product {work_product_id} has "
                    f"format {wp.format!r}, expected a STEP file "
                    f"({'/'.join(sorted(_SUPPORTED_SOURCE_FORMATS))}) -- "
                    "cadquery.export_geometry only imports STEP source geometry"
                )

            staged = await blob_stager(work_product_id)
            input_file = staged["file_path"]

            release_dir = root / _RELEASE_SUBDIR / work_product_id
            release_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(release_dir / f"release.{output_format}")

            export_result = await mcp_bridge.invoke(
                "cadquery.export_geometry",
                {
                    "input_file": input_file,
                    "output_format": output_format,
                    "output_path": output_path,
                },
            )

            out_file = Path(export_result.get("output_file") or output_path)
            content = out_file.read_bytes()

            logger.info(
                "manufacture_released",
                work_product_id=work_product_id,
                process=process,
                format=output_format,
                file_size_bytes=len(content),
            )
            span.set_attribute("manufacture.file_size_bytes", len(content))

            return {
                "work_product_id": work_product_id,
                "process": process,
                "format": output_format,
                "filename": out_file.name,
                "file_size_bytes": len(content),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }

    return release
