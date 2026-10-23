"""MCP adapter for corrugated carton arithmetic (FORGE-672).

``packaging.box_compression`` and ``packaging.rsc_dieline`` are arithmetic
over figures the caller gives, in-process and deterministic like
``power.check_budget``: no container, no credentials. Each figure carries its
source; one not given is unknown and the verdict is ``not_established``.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.packaging.carton import (
    CompressionRequest,
    CompressionResult,
    DielineRequest,
    DielineResult,
    box_compression,
    rsc_dieline,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.packaging.adapter")

_LIMITS = ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1)


class PackagingServer(McpToolServer):
    """Serves ``packaging.box_compression`` and ``packaging.rsc_dieline``."""

    def __init__(self) -> None:
        super().__init__(adapter_id="packaging", version="0.1.0")
        self.register_tool(
            manifest=ToolManifest(
                tool_id="packaging.box_compression",
                adapter_id="packaging",
                name="Estimate Box Compression Strength",
                description=(
                    "Simplified McKee estimate of a regular slotted carton's compression "
                    "strength (BCT = 5.87 * ECT * sqrt(caliper * perimeter)), reduced by every "
                    "derating factor you give (humidity, storage time, overhang; each 0..1 "
                    "with its source) and compared with the stack load times the required "
                    "safety factor. Give the board's ECT and caliper, the inside length and "
                    "width, one packed box's gross mass, the stack count and the safety factor, "
                    "each as {value, source}. An estimate: a real compression test outranks it. "
                    "A figure not given is unknown: not_established."
                ),
                capability="box_compression",
                input_schema=_inline(CompressionRequest.model_json_schema()),
                output_schema=CompressionResult.model_json_schema(),
                phase=1,
                resource_limits=_LIMITS,
            ),
            handler=self.box_compression,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="packaging.rsc_dieline",
                adapter_id="packaging",
                name="Regular Slotted Container Dieline",
                description=(
                    "Blank size of a regular slotted container (FEFCO 0201) from its inside "
                    "length, width and depth, the converter's score allowance and the glue "
                    "flap, checked against the converter's rules (minimum glue flap, minimum "
                    "panel, sheet length and width). Each figure is {value, source}; rules come "
                    "from the converter or the process library, never assumed."
                ),
                capability="dieline_design",
                input_schema=_inline(DielineRequest.model_json_schema()),
                output_schema=DielineResult.model_json_schema(),
                phase=1,
                resource_limits=_LIMITS,
            ),
            handler=self.rsc_dieline,
        )

    async def box_compression(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("packaging.box_compression") as span:
            result = box_compression(CompressionRequest.model_validate(arguments))
            span.set_attribute("packaging.verdict", result.verdict)
            logger.info(
                "packaging_compression_checked",
                verdict=result.verdict,
                margin=result.compression_margin,
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")

    async def rsc_dieline(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("packaging.rsc_dieline") as span:
            result = rsc_dieline(DielineRequest.model_validate(arguments))
            span.set_attribute("packaging.verdict", result.verdict)
            logger.info(
                "packaging_dieline_checked",
                verdict=result.verdict,
                violations=len(result.violations),
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """``$defs`` inlined: some MCP clients do not resolve ``$ref``."""
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.split("/")[-1]])
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    inlined: dict[str, Any] = inline(schema)
    return inlined
