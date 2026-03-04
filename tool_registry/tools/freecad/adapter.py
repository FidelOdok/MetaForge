"""FreeCAD CAD tool adapter -- MCP server for CAD operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.freecad.config import FreecadConfig

logger = structlog.get_logger()


class FreecadServer(McpToolServer):
    """FreeCAD tool adapter for CAD operations via MCP.

    Provides 4 tools:
    - freecad.export_geometry: Export CAD model to STEP/STL/OBJ format
    - freecad.generate_mesh: Generate finite element mesh from CAD geometry
    - freecad.boolean_operation: Perform CSG boolean operations (union, subtract, intersect)
    - freecad.get_properties: Extract geometric properties (volume, area, etc.)
    """

    def __init__(self, config: FreecadConfig | None = None) -> None:
        super().__init__(adapter_id="freecad", version="0.1.0")
        self.config = config or FreecadConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all FreeCAD tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="freecad.export_geometry",
                adapter_id="freecad",
                name="Export Geometry",
                description="Export CAD model to STEP/STL/OBJ/BREP format",
                capability="cad_export",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input_file": {
                            "type": "string",
                            "description": "Path to CAD file",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["step", "stl", "obj", "brep"],
                            "description": "Target export format",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional output file path",
                        },
                    },
                    "required": ["input_file", "output_format"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output_file": {"type": "string"},
                        "file_size_bytes": {"type": "integer"},
                        "format": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.export_geometry,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="freecad.generate_mesh",
                adapter_id="freecad",
                name="Generate Mesh",
                description="Generate finite element mesh from CAD geometry",
                capability="mesh_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input_file": {
                            "type": "string",
                            "description": "Path to CAD file",
                        },
                        "element_size": {
                            "type": "number",
                            "default": 1.0,
                            "description": "Target element size",
                        },
                        "algorithm": {
                            "type": "string",
                            "enum": ["netgen", "gmsh", "mefisto"],
                            "description": "Meshing algorithm",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["inp", "unv", "stl"],
                            "default": "inp",
                            "description": "Output mesh format",
                        },
                    },
                    "required": ["input_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "mesh_file": {"type": "string"},
                        "num_nodes": {"type": "integer"},
                        "num_elements": {"type": "integer"},
                        "element_types": {"type": "array"},
                        "quality_metrics": {"type": "object"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.generate_mesh,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="freecad.boolean_operation",
                adapter_id="freecad",
                name="Boolean Operation",
                description="Perform CSG boolean operations (union, subtract, intersect)",
                capability="cad_operations",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input_file_a": {
                            "type": "string",
                            "description": "Path to first CAD file",
                        },
                        "input_file_b": {
                            "type": "string",
                            "description": "Path to second CAD file",
                        },
                        "operation": {
                            "type": "string",
                            "enum": ["union", "subtract", "intersect"],
                            "description": "Boolean operation type",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Optional output file path",
                        },
                    },
                    "required": ["input_file_a", "input_file_b", "operation"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output_file": {"type": "string"},
                        "operation": {"type": "string"},
                        "result_volume": {"type": "number"},
                        "result_area": {"type": "number"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.boolean_operation,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="freecad.get_properties",
                adapter_id="freecad",
                name="Get Properties",
                description="Extract geometric properties (volume, area, etc.)",
                capability="cad_analysis",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input_file": {
                            "type": "string",
                            "description": "Path to CAD file",
                        },
                        "properties": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": [
                                "volume",
                                "area",
                                "center_of_mass",
                                "bounding_box",
                            ],
                            "description": "Properties to extract",
                        },
                    },
                    "required": ["input_file"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "properties": {"type": "object"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.get_properties,
        )

    async def export_geometry(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Export CAD model to the requested format.

        In production, this invokes FreeCAD headless. For now, it validates
        arguments and delegates to _execute_export().
        """
        input_file = arguments.get("input_file", "")
        output_format = arguments.get("output_format", "")
        output_path = arguments.get("output_path", "")

        if not input_file:
            raise ValueError("input_file is required")
        if not output_format:
            raise ValueError("output_format is required")
        if output_format not in ("step", "stl", "obj", "brep"):
            raise ValueError(f"Unsupported export format: {output_format}")

        if not output_path:
            stem = Path(input_file).stem
            output_path = f"{self.config.work_dir}/{stem}.{output_format}"

        logger.info(
            "Exporting geometry",
            input_file=input_file,
            output_format=output_format,
            output_path=output_path,
        )

        result = await self._execute_export(input_file, output_format, output_path)
        return result

    async def generate_mesh(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Generate finite element mesh from CAD geometry."""
        input_file = arguments.get("input_file", "")
        element_size = arguments.get("element_size", 1.0)
        algorithm = arguments.get("algorithm", self.config.default_mesh_algorithm)
        output_format = arguments.get("output_format", "inp")

        if not input_file:
            raise ValueError("input_file is required")
        if algorithm not in ("netgen", "gmsh", "mefisto"):
            raise ValueError(f"Unsupported meshing algorithm: {algorithm}")

        logger.info(
            "Generating mesh",
            input_file=input_file,
            element_size=element_size,
            algorithm=algorithm,
            output_format=output_format,
        )

        result = await self._execute_meshing(
            input_file, element_size, algorithm, output_format
        )
        return result

    async def boolean_operation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Perform CSG boolean operation on two CAD models."""
        input_file_a = arguments.get("input_file_a", "")
        input_file_b = arguments.get("input_file_b", "")
        operation = arguments.get("operation", "")
        output_path = arguments.get("output_path", "")

        if not input_file_a:
            raise ValueError("input_file_a is required")
        if not input_file_b:
            raise ValueError("input_file_b is required")
        if not operation:
            raise ValueError("operation is required")
        if operation not in ("union", "subtract", "intersect"):
            raise ValueError(f"Unsupported boolean operation: {operation}")

        if not output_path:
            stem_a = Path(input_file_a).stem
            output_path = f"{self.config.work_dir}/{stem_a}_{operation}.step"

        logger.info(
            "Performing boolean operation",
            file_a=input_file_a,
            file_b=input_file_b,
            operation=operation,
        )

        result = await self._execute_boolean(
            input_file_a, input_file_b, operation, output_path
        )
        return result

    async def get_properties(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Extract geometric properties from a CAD model."""
        input_file = arguments.get("input_file", "")
        properties = arguments.get(
            "properties", ["volume", "area", "center_of_mass", "bounding_box"]
        )

        if not input_file:
            raise ValueError("input_file is required")

        logger.info(
            "Getting properties",
            input_file=input_file,
            properties=properties,
        )

        result = await self._execute_analysis(input_file, properties)
        return result

    async def _execute_export(
        self, input_file: str, output_format: str, output_path: str
    ) -> dict[str, Any]:
        """Execute FreeCAD geometry export. In production, runs FreeCAD headless.

        This method is designed to be easily mockable in tests.
        The actual implementation would:
        1. Load the CAD file in FreeCAD headless mode
        2. Export to the target format
        3. Return the output file path and metadata
        """
        _stem = Path(input_file).stem

        # This would be the actual FreeCAD call in production:
        # result = await asyncio.create_subprocess_exec(
        #     self.config.freecad_binary, "--console", export_script,
        #     cwd=self.config.work_dir,
        #     stdout=asyncio.subprocess.PIPE,
        #     stderr=asyncio.subprocess.PIPE,
        # )

        raise NotImplementedError(
            "FreeCAD export requires the freecadcmd binary. "
            "Use mock in tests or install FreeCAD for production use."
        )

    async def _execute_meshing(
        self,
        input_file: str,
        element_size: float,
        algorithm: str,
        output_format: str,
    ) -> dict[str, Any]:
        """Execute FreeCAD mesh generation. See _execute_export for notes."""
        raise NotImplementedError(
            "FreeCAD meshing requires the freecadcmd binary. "
            "Use mock in tests or install FreeCAD for production use."
        )

    async def _execute_boolean(
        self,
        file_a: str,
        file_b: str,
        operation: str,
        output_path: str,
    ) -> dict[str, Any]:
        """Execute FreeCAD boolean operation. See _execute_export for notes."""
        raise NotImplementedError(
            "FreeCAD boolean operations require the freecadcmd binary. "
            "Use mock in tests or install FreeCAD for production use."
        )

    async def _execute_analysis(
        self, input_file: str, properties: list[str]
    ) -> dict[str, Any]:
        """Execute FreeCAD property analysis. See _execute_export for notes."""
        raise NotImplementedError(
            "FreeCAD property analysis requires the freecadcmd binary. "
            "Use mock in tests or install FreeCAD for production use."
        )
