"""Blender render adapter: studio renders of a model, headless (FORGE-628).

Industrial design needs to *see* a product: renders for review, a turntable,
a scene a designer can open. Blender does that well and is free, but it is
GPL, and so is anything that calls its Python API. So:

* this module (MetaForge code) never imports ``bpy``;
* everything that runs inside Blender is ``bpy_scripts/render_scene.py``,
  GPL-3.0-or-later with its own LICENSE;
* :mod:`runner` starts Blender as a separate program and they exchange only
  files: a mesh and a job JSON in, PNGs, a .blend and a result JSON out.

Renders and .blend files are the user's (blender.org: "What you create with
Blender is your sole property").

The tools return what they made inline and remove their scratch directory,
so neither leaves anything behind: both are read-only. A model comes either
as a path in the shared adapter workspace or inline as base64. Blender reads
meshes (glTF/GLB, STL, OBJ, PLY); a STEP model is converted to GLB first by
the caller (the OCCT converter does this for the twin).
"""

from __future__ import annotations

import base64
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.blender.config import BlenderConfig
from tool_registry.tools.blender.runner import BlenderError, run_job

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.blender.adapter")

VIEWS = ["iso", "front", "back", "left", "right", "top", "bottom", "turntable"]
MESH_FORMATS = ("glb", "gltf", "stl", "obj", "ply")

_MODEL_PROPS: dict[str, Any] = {
    "input_file": {
        "type": "string",
        "description": "A mesh file in the shared adapter workspace (glb, gltf, stl, obj, ply).",
    },
    "model_base64": {"type": "string", "description": "The mesh file itself, base64."},
    "model_format": {"type": "string", "enum": list(MESH_FORMATS)},
    "model_up_axis": {
        "type": "string",
        "enum": ["y", "z"],
        "description": (
            "Which axis is up in a glTF/GLB: y (the glTF convention, the default) or z (a "
            "file written in CAD's Z-up axes, as MetaForge's STEP converter does). STL, "
            "OBJ and PLY are always read Z-up."
        ),
    },
}
_SCENE_PROPS: dict[str, Any] = {
    "colours": {
        "type": "object",
        "description": (
            "Override part colours: {part-name substring: [r, g, b, a]} in 0..1, sRGB (as a "
            "colour picker or a hex code gives it), or {rgba, roughness, metallic} to set "
            "the surface too (both 0..1). Parts not named keep the colours the model "
            "carries (from its STEP). A name that matches no part is reported in "
            "unmatched_colours."
        ),
    },
    "background": {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
    },
}


def _limits() -> ResourceLimits:
    return ResourceLimits(max_memory_mb=4096, max_cpu_seconds=900, max_disk_mb=2048)


class BlenderServer(McpToolServer):
    """Serves ``blender.render`` and ``blender.export_blend``."""

    def __init__(self, config: BlenderConfig | None = None) -> None:
        super().__init__(adapter_id="blender", version="0.1.0")
        self.config = config or BlenderConfig()
        self.register_tool(
            manifest=ToolManifest(
                tool_id="blender.render",
                adapter_id="blender",
                name="Render Model",
                description=(
                    "Studio renders of a mesh model in Blender (Cycles, CPU, headless): "
                    "named views (iso, front, back, left, right, top, bottom) and/or a "
                    "turntable of N frames, at a set resolution and sample count, "
                    "perspective or orthographic, optional transparent background and part "
                    "colour overrides. Returns PNGs inline (base64). The model is a mesh: "
                    "convert a STEP to GLB first. For industrial design review and for "
                    "showing a design in chat."
                ),
                capability="render",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_MODEL_PROPS,
                        "views": {"type": "array", "items": {"type": "string", "enum": VIEWS}},
                        "turntable_frames": {"type": "integer", "minimum": 1, "maximum": 36},
                        "resolution": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 64},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "samples": {"type": "integer", "minimum": 1, "maximum": 1024},
                        "projection": {"type": "string", "enum": ["perspective", "orthographic"]},
                        "transparent": {"type": "boolean"},
                        **_SCENE_PROPS,
                    },
                },
                output_schema={"type": "object"},
                phase=2,
                resource_limits=_limits(),
            ),
            handler=self.render,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="blender.export_blend",
                adapter_id="blender",
                name="Export Blender Scene",
                description=(
                    "The model staged as a Blender scene (lights, camera, colours) saved as a "
                    ".blend file, returned inline (base64), for a designer to open and refine "
                    "in Blender. The .blend is the user's; nothing is rendered."
                ),
                capability="render",
                input_schema={
                    "type": "object",
                    "properties": {
                        **_MODEL_PROPS,
                        "projection": {"type": "string", "enum": ["perspective", "orthographic"]},
                        **_SCENE_PROPS,
                    },
                },
                output_schema={"type": "object"},
                phase=2,
                resource_limits=_limits(),
            ),
            handler=self.export_blend,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _stage_model(self, arguments: dict[str, Any], scratch: Path) -> Path:
        """The model as a file Blender can read: workspace path or inline bytes."""
        if arguments.get("input_file"):
            base = Path(self.config.work_dir).resolve()
            path = (base / str(arguments["input_file"])).resolve()
            if base not in path.parents and path != base:
                raise ValueError("input_file must be inside the adapter workspace")
            if not path.is_file():
                raise ValueError(f"input_file {arguments['input_file']!r} does not exist")
            if path.suffix.lower().lstrip(".") not in MESH_FORMATS:
                raise ValueError(
                    f"Blender reads {', '.join(MESH_FORMATS)}; convert {path.suffix} to GLB first"
                )
            return path
        if arguments.get("model_base64"):
            fmt = str(arguments.get("model_format") or "").lower()
            if fmt not in MESH_FORMATS:
                raise ValueError(f"model_format must be one of {', '.join(MESH_FORMATS)}")
            try:
                data = base64.b64decode(str(arguments["model_base64"]), validate=True)
            except ValueError as exc:
                raise ValueError("model_base64 is not valid base64") from exc
            path = scratch / f"model.{fmt}"
            path.write_bytes(data)
            return path
        raise ValueError("give input_file (in the workspace) or model_base64 with model_format")

    @staticmethod
    def _surface(value: Any) -> dict[str, Any]:
        """One part's colour: [r, g, b, a], or {rgba, roughness, metallic}; all 0..1."""

        def unit(x: Any) -> bool:
            return isinstance(x, int | float) and not isinstance(x, bool) and 0 <= x <= 1

        surface = value if isinstance(value, dict) else {"rgba": value}
        rgba = surface.get("rgba")
        if not (isinstance(rgba, list) and len(rgba) == 4 and all(unit(c) for c in rgba)):
            raise ValueError(
                "colours must map a part name to [r, g, b, a] in 0..1, or to "
                "{rgba, roughness, metallic}"
            )
        out: dict[str, Any] = {"rgba": [float(c) for c in rgba]}
        for key in ("roughness", "metallic"):
            if surface.get(key) is not None:
                if not unit(surface[key]):
                    raise ValueError(f"colours: {key} must be between 0 and 1")
                out[key] = float(surface[key])
        unknown = set(surface) - {"rgba", "roughness", "metallic"}
        if unknown:
            raise ValueError(f"colours: unknown fields {', '.join(sorted(unknown))}")
        return out

    def _scene(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get("colours") or {}
        if not isinstance(raw, dict):
            raise ValueError("colours must map a part name to [r, g, b, a] in 0..1")
        colours = {str(k): self._surface(v) for k, v in raw.items()}
        background = arguments.get("background") or [0.92, 0.92, 0.92]
        up = str(arguments.get("model_up_axis") or "y").lower()
        if up not in ("y", "z"):
            raise ValueError("model_up_axis must be y or z")
        return {
            "model_up_axis": up,
            "projection": arguments.get("projection") or "perspective",
            "colours": colours,
            "background": [float(c) for c in background],
        }

    async def _run(self, job: dict[str, Any], scratch: Path) -> dict[str, Any]:
        try:
            return await run_job(
                self.config.blender_binary, job, scratch / "out", self.config.timeout_seconds
            )
        except BlenderError as exc:
            raise ValueError(str(exc)) from exc

    # ── tools ───────────────────────────────────────────────────────────

    async def render(self, arguments: dict[str, Any]) -> dict[str, Any]:
        views = list(arguments.get("views") or ["iso"])
        unknown = [v for v in views if v not in VIEWS]
        if unknown:
            raise ValueError(f"unknown views {unknown}; use {', '.join(VIEWS)}")
        frames = int(arguments.get("turntable_frames") or 12) if "turntable" in views else 0
        count = len([v for v in views if v != "turntable"]) + frames
        if count > self.config.max_images:
            raise ValueError(f"{count} images asked for; at most {self.config.max_images}")
        width, height = (int(n) for n in (arguments.get("resolution") or [1280, 960]))
        if width * height > self.config.max_pixels:
            raise ValueError(f"{width}x{height} is larger than {self.config.max_pixels} pixels")
        scratch = Path(tempfile.mkdtemp(prefix=f"render-{uuid.uuid4().hex[:8]}-"))
        try:
            with tracer.start_as_current_span("blender.render") as span:
                span.set_attribute("blender.images", count)
                job = {
                    **self._scene(arguments),
                    "input": str(self._stage_model(arguments, scratch)),
                    "views": views,
                    "turntable_frames": frames or 12,
                    "resolution": [width, height],
                    "samples": int(arguments.get("samples") or 32),
                    "transparent": bool(arguments.get("transparent")),
                }
                result = await self._run(job, scratch)
                images = []
                for image in result.get("images") or []:
                    path = scratch / "out" / str(image["file"])
                    if not path.is_file():
                        raise ValueError(f"Blender reported {image['file']} but did not write it")
                    images.append(
                        {
                            "view": image["view"],
                            "format": "png",
                            "width": width,
                            "height": height,
                            "png_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
                    )
                logger.info("blender_rendered", images=len(images), resolution=f"{width}x{height}")
                return {
                    "images": images,
                    "mesh_objects": result.get("mesh_objects"),
                    "coloured_objects": result.get("coloured_objects", []),
                    "unmatched_colours": result.get("unmatched_colours", []),
                    "bounds_min": result.get("bounds_min"),
                    "bounds_max": result.get("bounds_max"),
                    "blender_version": result.get("blender_version"),
                    "engine": "cycles-cpu",
                    "samples": job["samples"],
                }
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    async def export_blend(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scratch = Path(tempfile.mkdtemp(prefix=f"blend-{uuid.uuid4().hex[:8]}-"))
        try:
            with tracer.start_as_current_span("blender.export_blend"):
                job = {
                    **self._scene(arguments),
                    "input": str(self._stage_model(arguments, scratch)),
                    "views": [],
                    "save_blend": True,
                }
                result = await self._run(job, scratch)
                if not result.get("blend"):
                    raise ValueError("Blender did not save the scene")
                path = scratch / "out" / str(result["blend"])
                data = path.read_bytes()
                logger.info("blender_scene_exported", bytes=len(data))
                return {
                    "blend_base64": base64.b64encode(data).decode("ascii"),
                    "bytes": len(data),
                    "mesh_objects": result.get("mesh_objects"),
                    "blender_version": result.get("blender_version"),
                }
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
