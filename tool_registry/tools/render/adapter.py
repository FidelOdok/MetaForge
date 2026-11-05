"""Browser render adapter: ``render.model_views``, fixed views of a GLB as PNGs.

A design loop needs to *see* the model it just built, from the same views
every time, cheaply and without a GPU. This adapter loads the GLB into a
local three.js page in headless Chromium (Playwright) and reads back one PNG
per view:

* views: ``iso``, ``front``, ``back``, ``side`` (the right view), ``left``,
  ``top``, ``bottom`` and ``section`` (a cut along a CAD axis at a fraction of
  the model's extent, seen from the side cut away); orthographic by default,
  the iso view in perspective;
* a neutral studio environment, a floor grid, a scale bar on orthographic
  views, and an optional dimension overlay from measured extents passed by
  the caller (``dimensions``), labelled with where they came from;
* a contact sheet of every view in one PNG, for a reader that takes one image.

three.js (r169) ships inside the image and the page is served from local
files: nothing is fetched at render time. The tool returns the images inline
and keeps nothing, so it is read-only.

Registers in-process so tools/list is stable; a call needs the
render-adapter container (``METAFORGE_ADAPTER_RENDER_URL``), since the
gateway and sidecar images have no browser and raise
:class:`~tool_registry.tools.render.browser.BrowserNotAvailableError`.
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.render.browser import render_in_browser
from tool_registry.tools.render.config import RenderConfig
from tool_registry.tools.render.page import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MAX_VIEWS,
    MIN_SIZE,
    THREE_VERSION,
    VIEW_NAMES,
    build_plan,
    cad_bbox,
    decode_png,
    normalize_views,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.render.adapter")

_GLB_MAGIC = b"glTF"

_VIEW_ITEM: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "enum": list(VIEW_NAMES)},
        {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": list(VIEW_NAMES)},
                "projection": {"type": "string", "enum": ["orthographic", "perspective"]},
                "section": {
                    "type": "object",
                    "properties": {
                        "axis": {"type": "string", "enum": ["x", "y", "z"]},
                        "offset": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["axis"],
                },
                "label": {"type": "string", "maxLength": 60},
            },
            "required": ["view"],
        },
    ]
}

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "glb_base64": {"type": "string", "description": "The GLB model, base64."},
        "input_file": {
            "type": "string",
            "description": "A GLB in the shared adapter workspace, instead of glb_base64.",
        },
        "views": {
            "type": "array",
            "items": _VIEW_ITEM,
            "maxItems": MAX_VIEWS,
            "description": "Default iso, front, side, top.",
        },
        "width": {"type": "integer", "minimum": MIN_SIZE, "maximum": MAX_SIZE},
        "height": {"type": "integer", "minimum": MIN_SIZE, "maximum": MAX_SIZE},
        "projection": {
            "type": "string",
            "enum": ["orthographic", "perspective"],
            "description": "For the flat views; iso is perspective unless it says otherwise.",
        },
        "up_axis": {
            "type": "string",
            "enum": ["y", "z"],
            "description": "z (default): CAD axes, as MetaForge's STEP converter writes; y: glTF.",
        },
        "unit_scale": {"type": "number", "description": "Model units per millimetre (def 1)."},
        "grid": {"type": "boolean"},
        "scale_bar": {"type": "boolean"},
        "environment": {"type": "string", "enum": ["neutral", "none"]},
        "dimensions": {
            "type": "object",
            "description": "Measured extents in mm to draw on the views, and their source.",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
                "source": {"type": "string"},
            },
        },
        "title": {"type": "string", "maxLength": 120},
        "sheet": {
            "type": "boolean",
            "description": "Also return every view on one PNG (def true).",
        },
    },
}


class RenderServer(McpToolServer):
    """Serves ``render.model_views``."""

    def __init__(self, config: RenderConfig | None = None) -> None:
        super().__init__(adapter_id="render", version="0.1.0")
        self.config = config or RenderConfig()
        self._slots = asyncio.Semaphore(self.config.max_concurrent)
        self.register_tool(
            manifest=ToolManifest(
                tool_id="render.model_views",
                adapter_id="render",
                name="Render Model Views",
                description=(
                    "Render a GLB from fixed views in a headless browser (three.js, "
                    "Chromium): iso, front, side, top, back, left, bottom, and section "
                    "views cut along a CAD axis. Orthographic flat views with a scale "
                    "bar, a floor grid and an optional dimension overlay from measured "
                    "extents (dimensions, labelled with their source); a neutral studio "
                    "environment. Returns one PNG per view and a contact sheet, inline "
                    "(base64), with the view metadata and the mesh's bounding box in mm. "
                    "Pictures of the model, never a measurement of it. Free, local, "
                    "keeps nothing."
                ),
                capability="render",
                input_schema=_INPUT_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "views": {"type": "array", "items": {"type": "object"}},
                        "sheet_png_base64": {"type": ["string", "null"]},
                        "bbox_mm": {"type": ["object", "null"]},
                        "triangles": {"type": "integer"},
                        "renderer": {"type": "object"},
                        "duration_s": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=180, max_disk_mb=256
                ),
            ),
            handler=self.model_views,
        )

    def _read_model(self, arguments: dict[str, Any]) -> bytes:
        raw = arguments.get("glb_base64")
        path = arguments.get("input_file")
        if raw and path:
            raise ValueError("give glb_base64 or input_file, not both")
        if raw:
            if not isinstance(raw, str):
                raise ValueError("glb_base64 must be a base64 string")
            data = base64.b64decode(raw, validate=False)
        elif path:
            if not isinstance(path, str):
                raise ValueError("input_file must be a path in the adapter workspace")
            root = Path(self.config.work_dir).resolve()
            target = (root / path).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("input_file must be inside the adapter workspace") from exc
            if not target.is_file():
                raise ValueError(f"no file {path} in the adapter workspace")
            data = target.read_bytes()
        else:
            raise ValueError("glb_base64 or input_file is required")
        if len(data) > self.config.max_input_mb * 1024 * 1024:
            raise ValueError(f"the model is larger than {self.config.max_input_mb} MB")
        if not data.startswith(_GLB_MAGIC):
            raise ValueError("the model is not a binary glTF (GLB); convert a STEP to GLB first")
        return data

    async def model_views(self, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        with tracer.start_as_current_span("render.model_views") as span:
            glb = self._read_model(arguments)
            views = normalize_views(arguments.get("views"))
            plan = build_plan(
                views,
                width=int(arguments.get("width") or DEFAULT_SIZE),
                height=int(arguments.get("height") or DEFAULT_SIZE),
                projection=str(arguments.get("projection") or "orthographic"),
                up_axis=str(arguments.get("up_axis") or "z"),
                unit_scale=float(arguments.get("unit_scale") or 1.0),
                grid=arguments.get("grid") is not False,
                scale_bar=arguments.get("scale_bar") is not False,
                environment=str(arguments.get("environment") or "neutral"),
                dimensions=arguments.get("dimensions") or None,
                title=str(arguments.get("title") or ""),
                sheet=arguments.get("sheet") is not False,
            )
            span.set_attribute("render.views", len(plan.views))
            span.set_attribute("render.model_bytes", len(glb))
            async with self._slots:
                raw = await render_in_browser(
                    glb,
                    plan.model_dump(mode="json"),
                    three_dir=Path(self.config.three_dir),
                    timeout_s=float(self.config.max_operation_time),
                )
            out_views: list[dict[str, Any]] = []
            for view in raw.get("views") or []:
                png = decode_png(view.get("png"))
                out_views.append(
                    {
                        "view": view.get("name"),
                        "label": view.get("label"),
                        "projection": view.get("projection"),
                        "section": view.get("section"),
                        "mm_per_px": view.get("mm_per_px"),
                        "width": plan.width,
                        "height": plan.height,
                        "png_base64": base64.b64encode(png).decode("ascii"),
                    }
                )
            if len(out_views) != len(plan.views):
                raise RuntimeError(f"the page rendered {len(out_views)} of {len(plan.views)} views")
            sheet = raw.get("sheet")
            duration = round(time.monotonic() - started, 3)
            result = {
                "views": out_views,
                "sheet_png_base64": (
                    base64.b64encode(decode_png(sheet)).decode("ascii") if sheet else None
                ),
                "bbox_mm": cad_bbox(raw.get("bbox_mm")),
                "triangles": int(raw.get("triangles") or 0),
                "dimensions_source": plan.dimensions.source if plan.dimensions else "mesh",
                "renderer": {
                    "engine": f"three.js r{THREE_VERSION.split('.')[1]}",
                    "browser": raw.get("browser"),
                },
                "duration_s": duration,
            }
            if raw.get("page_errors"):
                result["page_errors"] = raw["page_errors"]
            logger.info(
                "render_model_views_done",
                views=len(out_views),
                triangles=result["triangles"],
                duration_s=duration,
            )
            return result
