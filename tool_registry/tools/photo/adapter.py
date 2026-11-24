"""Photo adapter: the plain-code half of reading a component photo (FORGE-922).

The client's agent reads the photo (the partscope skill); these tools check and size
what it read. ``scale.resolve`` (FORGE-926, R4) turns pixels into sourced millimetres.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.photo.scale import AXES, ScaleError, resolve

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.photo.adapter")

_LIMITS = ResourceLimits(max_memory_mb=128, max_cpu_seconds=10, max_disk_mb=1)
_AXIS = {"enum": list(AXES)}


class PhotoServer(McpToolServer):
    """Plain code over a client's photo reading: no model, no network."""

    def __init__(self) -> None:
        super().__init__(adapter_id="photo", version="0.1.0")
        self.register_tool(
            manifest=ToolManifest(
                tool_id="scale.resolve",
                adapter_id="photo",
                name="Resolve Sizes From A Photo Reading",
                description=(
                    "Turn a photo reading's pixel sizes into millimetres, from ranked "
                    "sources, or say what is missing; plain code, no model. Each side "
                    "(length, width, height) is set by its best source: printed (a size "
                    "in metadata_text, kept only if its quote is found there and holds "
                    "the number), package_nominal (a KiCad footprint name such as "
                    "SOIC-8_3.9x4.9mm_P1.27mm), similar_part (a similar part's sourced "
                    "size) or photo (a scale object seen along that side: header_2.54, "
                    "jst_ph, jst_xh, jst_sh, jst_gh, jst_zh, or one you cite in "
                    "scale_objects, such as a ruler). Every other source is compared and "
                    "a disagreement over 15 % warns and lowers confidence. A side with no "
                    "source reuses the other side's photo scale, flagged (and in an angled "
                    "view, probably too small). Measured pin pitches (pitches_px) snap to "
                    "a standard pitch only within 12 %. Every size is an estimate "
                    "{value, unit, source_kind: estimate, method, basis, image_ref, "
                    "confidence}; status is resolved, partial or needs, and needs says "
                    "what would give the missing sides. Record the sizes with "
                    "library.update_component as estimates."
                ),
                capability="photo_reading",
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_ref": {"type": "string"},
                        "view": {"enum": ["top", "side", "angled", "bottom", "unclear"]},
                        "body_px": {
                            "type": "object",
                            "description": (
                                "Per side {px, image_ref?}: the body's size in pixels, "
                                "e.g. {length: {px: 400}, width: {px: 236}}."
                            ),
                        },
                        "metadata_text": {"type": "string"},
                        "metadata_dims": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": _AXIS,
                                    "value_mm": {"type": "number"},
                                    "quote": {"type": "string"},
                                },
                            },
                        },
                        "package_code": {"type": "string"},
                        "similar_part": {
                            "type": "object",
                            "description": "{ref, length_mm?, width_mm?, height_mm?, source_ref}",
                        },
                        "references": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string"},
                                    "axis": _AXIS,
                                    "span_px": {"type": "number"},
                                    "count": {
                                        "type": "number",
                                        "description": "Pitches (or objects) the span covers.",
                                    },
                                },
                            },
                        },
                        "pitches_px": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "axis": _AXIS,
                                    "px": {"type": "number"},
                                    "count": {"type": "number"},
                                },
                            },
                        },
                        "scale_objects": {
                            "type": "array",
                            "description": (
                                "Extra objects you cite: {kind, size_mm, source_ref, title?}."
                            ),
                            "items": {"type": "object"},
                        },
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "status": {"enum": ["resolved", "partial", "needs"]},
                        "sizes": {"type": "object"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                        "needs": {"type": "array", "items": {"type": "string"}},
                        "rejected_quotes": {"type": "array", "items": {"type": "object"}},
                        "pitches": {"type": "array", "items": {"type": "object"}},
                    },
                },
                phase=1,
                resource_limits=_LIMITS,
            ),
            handler=self.scale_resolve,
        )

    async def scale_resolve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("scale.resolve") as span:
            try:
                result = resolve(arguments)
            except ScaleError as exc:
                raise ValueError(f"scale.resolve: {exc}") from exc
            span.set_attribute("scale.status", result["status"])
            span.set_attribute("scale.warnings", len(result["warnings"]))
            logger.info(
                "scale_resolved",
                status=result["status"],
                sides=sorted(result["sizes"]),
                warnings=len(result["warnings"]),
                rejected_quotes=len(result["rejected_quotes"]),
            )
            return result
