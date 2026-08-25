"""OpenUSD conversion tool adapter -- MCP server for GLB -> USD conversion (MET-634)."""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.omniverse_usd.config import OmniverseUsdConfig
from tool_registry.tools.omniverse_usd.converter import (
    convert_glb_to_usd as converter_convert_glb_to_usd,
)
from tool_registry.tools.omniverse_usd.converter import (
    describe_stage as converter_describe_stage,
)
from tool_registry.tools.omniverse_usd.converter import (
    validate_usd_minimum as converter_validate_usd_minimum,
)

logger = structlog.get_logger()
tracer = get_tracer("tool_registry.tools.omniverse_usd.adapter")


class OmniverseUsdServer(McpToolServer):
    """OpenUSD conversion tool adapter.

    Provides three tools:
    - omniverse_usd.convert_glb_to_usd: convert a GLB (from cadquery/freecad/
      occt-converter's existing STEP tessellation) into an OpenUSD stage
    - omniverse_usd.validate_usd_minimum: cheap structural viability gate
      on a USD stage (default prim, mesh count, metersPerUnit)
    - omniverse_usd.describe_stage: basic structural info about a USD stage
    """

    def __init__(self, config: OmniverseUsdConfig | None = None) -> None:
        super().__init__(adapter_id="omniverse_usd", version="0.1.0")
        self.config = config or OmniverseUsdConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all OpenUSD conversion tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="omniverse_usd.convert_glb_to_usd",
                adapter_id="omniverse_usd",
                name="Convert GLB to USD",
                description="Convert a GLB file into an OpenUSD stage, preserving part "
                "names and transforms",
                capability="usd_conversion",
                input_schema={
                    "type": "object",
                    "properties": {
                        "glb_path": {"type": "string", "description": "Path to input GLB"},
                        "output_path": {
                            "type": "string",
                            "description": "Path to write the USD stage to",
                        },
                        "meters_per_unit": {"type": "number"},
                        "up_axis": {"type": "string", "enum": ["Y", "Z"]},
                    },
                    "required": ["glb_path", "output_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output_path": {"type": "string"},
                        "prim_count": {"type": "integer"},
                        "mesh_count": {"type": "integer"},
                        "part_names": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=1024, max_cpu_seconds=120),
            ),
            handler=self.convert_glb_to_usd,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="omniverse_usd.validate_usd_minimum",
                adapter_id="omniverse_usd",
                name="Validate USD Minimum Viability",
                description="Cheap structural viability gate on a USD stage before "
                "downstream simulation dispatch",
                capability="usd_validation",
                input_schema={
                    "type": "object",
                    "properties": {"usd_path": {"type": "string"}},
                    "required": ["usd_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "valid": {"type": "boolean"},
                        "mesh_count": {"type": "integer"},
                        "has_default_prim": {"type": "boolean"},
                        "meters_per_unit": {"type": "number"},
                        "issues": {"type": "array"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=30),
            ),
            handler=self.validate_usd_minimum,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="omniverse_usd.describe_stage",
                adapter_id="omniverse_usd",
                name="Describe USD Stage",
                description="Return basic structural info about a USD stage",
                capability="usd_inspection",
                input_schema={
                    "type": "object",
                    "properties": {"usd_path": {"type": "string"}},
                    "required": ["usd_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "up_axis": {"type": "string"},
                        "meters_per_unit": {"type": "number"},
                        "prim_paths": {"type": "array"},
                        "mesh_count": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=30),
            ),
            handler=self.describe_stage,
        )

    async def convert_glb_to_usd(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Convert a GLB file into an OpenUSD stage."""
        glb_path = arguments.get("glb_path", "")
        output_path = arguments.get("output_path", "")
        meters_per_unit = arguments.get("meters_per_unit", self.config.default_meters_per_unit)
        up_axis = arguments.get("up_axis", "Z")

        if not glb_path:
            raise ValueError("glb_path is required")
        if not output_path:
            raise ValueError("output_path is required")

        logger.info("Converting GLB to USD", glb_path=glb_path, output_path=output_path)

        with tracer.start_as_current_span("omniverse_usd.convert_glb_to_usd") as span:
            span.set_attribute("omniverse_usd.glb_path", glb_path)
            span.set_attribute("omniverse_usd.output_path", output_path)
            try:
                return converter_convert_glb_to_usd(
                    glb_path, output_path, meters_per_unit=meters_per_unit, up_axis=up_axis
                )
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def validate_usd_minimum(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run the minimum-viability structural gate on a USD stage."""
        usd_path = arguments.get("usd_path", "")
        if not usd_path:
            raise ValueError("usd_path is required")

        logger.info("Validating USD stage viability", usd_path=usd_path)

        with tracer.start_as_current_span("omniverse_usd.validate_usd_minimum") as span:
            span.set_attribute("omniverse_usd.usd_path", usd_path)
            try:
                return converter_validate_usd_minimum(usd_path)
            except Exception as exc:
                span.record_exception(exc)
                raise

    async def describe_stage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return basic structural info about a USD stage."""
        usd_path = arguments.get("usd_path", "")
        if not usd_path:
            raise ValueError("usd_path is required")

        logger.info("Describing USD stage", usd_path=usd_path)

        with tracer.start_as_current_span("omniverse_usd.describe_stage") as span:
            span.set_attribute("omniverse_usd.usd_path", usd_path)
            try:
                return converter_describe_stage(usd_path)
            except Exception as exc:
                span.record_exception(exc)
                raise
