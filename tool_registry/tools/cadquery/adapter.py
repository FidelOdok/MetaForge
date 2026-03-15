"""CadQuery CAD tool adapter -- MCP server for CAD operations."""

from __future__ import annotations

from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.cadquery.config import CadqueryConfig

logger = structlog.get_logger()


class CadqueryServer(McpToolServer):
    """CadQuery tool adapter for CAD operations via MCP.

    Provides 7 tools:
    - cadquery.create_parametric: Parametric CAD from shape type + dimensions
    - cadquery.boolean_operation: CSG union/subtract/intersect
    - cadquery.get_properties: Volume, area, CoM, bounding box, inertia
    - cadquery.export_geometry: Export to STEP/STL/OBJ/BREP/AMF/SVG
    - cadquery.execute_script: Execute sandboxed CadQuery Python script
    - cadquery.create_assembly: Multi-part assemblies with constraints (Phase 2)
    - cadquery.generate_enclosure: PCB enclosure generation (Phase 2)
    """

    def __init__(self, config: CadqueryConfig | None = None) -> None:
        super().__init__(adapter_id="cadquery", version="0.1.0")
        self.config = config or CadqueryConfig()
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all CadQuery tools."""
        self.register_tool(
            manifest=ToolManifest(
                tool_id="cadquery.create_parametric",
                adapter_id="cadquery",
                name="Create Parametric",
                description="Generate parametric CAD geometry from shape type and dimensions",
                capability="cad_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "shape_type": {
                            "type": "string",
                            "enum": [
                                "box", "cylinder", "sphere", "cone",
                                "bracket", "plate", "enclosure",
                            ],
                            "description": "Type of parametric shape to generate",
                        },
                        "parameters": {
                            "type": "object",
                            "description": (
                                "Shape-specific dimensions (width, height, thickness, etc.)"
                            ),
                        },
                        "material": {
                            "type": "string",
                            "description": "Material name for metadata",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output STEP file path",
                        },
                    },
                    "required": ["shape_type", "parameters", "output_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "cad_file": {"type": "string"},
                        "volume_mm3": {"type": "number"},
                        "surface_area_mm2": {"type": "number"},
                        "bounding_box": {"type": "object"},
                        "parameters_used": {"type": "object"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.create_parametric,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="cadquery.boolean_operation",
                adapter_id="cadquery",
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
                tool_id="cadquery.get_properties",
                adapter_id="cadquery",
                name="Get Properties",
                description=(
                    "Extract geometric properties (volume, area, center of mass, "
                    "bounding box, inertia)"
                ),
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
                                "volume", "area", "center_of_mass",
                                "bounding_box", "inertia",
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

        self.register_tool(
            manifest=ToolManifest(
                tool_id="cadquery.export_geometry",
                adapter_id="cadquery",
                name="Export Geometry",
                description="Export CAD model to STEP/STL/OBJ/BREP/AMF/SVG format",
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
                            "enum": ["step", "stl", "obj", "brep", "amf", "svg"],
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
                tool_id="cadquery.execute_script",
                adapter_id="cadquery",
                name="Execute Script",
                description=(
                    "Execute a sandboxed CadQuery Python script. The script must assign "
                    "its final result to a variable named 'result' (a CadQuery Workplane)."
                ),
                capability="cad_scripting",
                input_schema={
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": "CadQuery Python script to execute",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output STEP file path",
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Execution timeout in seconds",
                        },
                    },
                    "required": ["script"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "cad_file": {"type": "string"},
                        "script_text": {"type": "string"},
                        "volume_mm3": {"type": "number"},
                        "surface_area_mm2": {"type": "number"},
                        "bounding_box": {"type": "object"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.execute_script,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="cadquery.create_assembly",
                adapter_id="cadquery",
                name="Create Assembly",
                description="Create multi-part assembly from STEP files with constraints",
                capability="cad_assembly",
                input_schema={
                    "type": "object",
                    "properties": {
                        "parts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "file": {"type": "string"},
                                    "location": {"type": "object"},
                                },
                                "required": ["name", "file"],
                            },
                            "description": "Parts to assemble",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Assembly constraints",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output STEP file path",
                        },
                    },
                    "required": ["parts"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "assembly_file": {"type": "string"},
                        "part_count": {"type": "integer"},
                        "total_volume": {"type": "number"},
                        "interference_check_passed": {"type": "boolean"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(
                    max_memory_mb=4096, max_cpu_seconds=600, max_disk_mb=1024
                ),
            ),
            handler=self.create_assembly,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="cadquery.generate_enclosure",
                adapter_id="cadquery",
                name="Generate Enclosure",
                description=(
                    "Generate PCB enclosure from board dimensions and connector cutouts"
                ),
                capability="cad_enclosure",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pcb_length": {
                            "type": "number",
                            "description": "PCB length in mm",
                        },
                        "pcb_width": {
                            "type": "number",
                            "description": "PCB width in mm",
                        },
                        "pcb_thickness": {
                            "type": "number",
                            "default": 1.6,
                            "description": "PCB thickness in mm",
                        },
                        "component_max_height": {
                            "type": "number",
                            "default": 10.0,
                            "description": "Max component height above PCB in mm",
                        },
                        "connector_cutouts": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Connector cutout definitions",
                        },
                        "mounting_holes": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Mounting hole positions",
                        },
                        "wall_thickness": {
                            "type": "number",
                            "default": 2.0,
                            "description": "Enclosure wall thickness in mm",
                        },
                        "material": {
                            "type": "string",
                            "default": "ABS",
                            "description": "Material name",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Output STEP file path",
                        },
                    },
                    "required": ["pcb_length", "pcb_width"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "cad_file": {"type": "string"},
                        "internal_volume": {"type": "number"},
                        "external_dimensions": {"type": "object"},
                        "mounting_info": {"type": "object"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.generate_enclosure,
        )

    async def create_parametric(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Generate parametric CAD geometry from shape type and dimensions."""
        shape_type = arguments.get("shape_type", "")
        parameters = arguments.get("parameters", {})
        material = arguments.get("material", "")
        output_path = arguments.get("output_path", "")

        if not shape_type:
            raise ValueError("shape_type is required")
        valid_shapes = ("box", "cylinder", "sphere", "cone", "bracket", "plate", "enclosure")
        if shape_type not in valid_shapes:
            raise ValueError(f"Unsupported shape type: {shape_type}")
        if not parameters:
            raise ValueError("parameters is required")
        if not output_path:
            raise ValueError("output_path is required")

        logger.info(
            "Creating parametric CAD",
            shape_type=shape_type,
            material=material,
            output_path=output_path,
        )

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir,
            timeout=self.config.max_operation_time,
            max_script_lines=self.config.max_script_lines,
        )
        return ops.create_parametric(shape_type, parameters, material, output_path)

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

        logger.info(
            "Performing boolean operation",
            file_a=input_file_a,
            file_b=input_file_b,
            operation=operation,
        )

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir, timeout=self.config.max_operation_time,
        )
        return ops.boolean_operation(input_file_a, input_file_b, operation, output_path)

    async def get_properties(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Extract geometric properties from a CAD model."""
        input_file = arguments.get("input_file", "")
        properties = arguments.get(
            "properties", ["volume", "area", "center_of_mass", "bounding_box", "inertia"]
        )

        if not input_file:
            raise ValueError("input_file is required")

        logger.info("Getting properties", input_file=input_file, properties=properties)

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir, timeout=self.config.max_operation_time,
        )
        return ops.get_properties(input_file, properties)

    async def export_geometry(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Export CAD model to the requested format."""
        input_file = arguments.get("input_file", "")
        output_format = arguments.get("output_format", "")
        output_path = arguments.get("output_path", "")

        if not input_file:
            raise ValueError("input_file is required")
        if not output_format:
            raise ValueError("output_format is required")
        if output_format not in self.config.supported_export_formats:
            raise ValueError(f"Unsupported export format: {output_format}")

        logger.info(
            "Exporting geometry",
            input_file=input_file,
            output_format=output_format,
        )

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir, timeout=self.config.max_operation_time,
        )
        return ops.export_geometry(input_file, output_format, output_path)

    async def execute_script(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a sandboxed CadQuery Python script."""
        script = arguments.get("script", "")
        output_path = arguments.get("output_path", "")
        timeout = arguments.get("timeout")

        if not script:
            raise ValueError("script is required")

        logger.info("Executing CadQuery script", script_length=len(script))

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir,
            timeout=self.config.max_operation_time,
            max_script_lines=self.config.max_script_lines,
            sandbox_enabled=self.config.sandbox_enabled,
        )
        return ops.execute_script(script, output_path, timeout)

    async def create_assembly(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Create multi-part assembly from STEP files with constraints."""
        parts = arguments.get("parts", [])
        constraints = arguments.get("constraints")
        output_path = arguments.get("output_path", "")

        if not parts:
            raise ValueError("parts list is required and must not be empty")

        for i, part in enumerate(parts):
            if not part.get("name"):
                raise ValueError(f"Part {i} is missing 'name'")
            if not part.get("file"):
                raise ValueError(f"Part {i} is missing 'file'")

        logger.info("Creating assembly", part_count=len(parts))

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir, timeout=self.config.max_operation_time,
        )
        return ops.create_assembly(parts, constraints, output_path)

    async def generate_enclosure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Generate PCB enclosure from board dimensions and connector cutouts."""
        pcb_length = arguments.get("pcb_length", 0.0)
        pcb_width = arguments.get("pcb_width", 0.0)

        if pcb_length <= 0:
            raise ValueError("pcb_length must be positive")
        if pcb_width <= 0:
            raise ValueError("pcb_width must be positive")

        logger.info(
            "Generating enclosure",
            pcb_length=pcb_length,
            pcb_width=pcb_width,
        )

        from tool_registry.tools.cadquery.operations import CadqueryOperations

        ops = CadqueryOperations(
            work_dir=self.config.work_dir, timeout=self.config.max_operation_time,
        )
        return ops.generate_enclosure(
            pcb_length=pcb_length,
            pcb_width=pcb_width,
            pcb_thickness=arguments.get("pcb_thickness", 1.6),
            component_max_height=arguments.get("component_max_height", 10.0),
            connector_cutouts=arguments.get("connector_cutouts"),
            mounting_holes=arguments.get("mounting_holes"),
            wall_thickness=arguments.get("wall_thickness", 2.0),
            material=arguments.get("material", "ABS"),
            output_path=arguments.get("output_path", ""),
        )
