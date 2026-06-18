"""FreeCAD CAD tool adapter -- MCP server for CAD operations.

Runs FreeCAD headless (in-process, or in the freecad-adapter container) and
exposes both the legacy stateless file-in/file-out tools and a stateful
PartDesign authoring surface (MET-528): open a session → create a body →
sketch on it → pad the sketch → export the authored solid. Stateful tools
address prior objects by a stable ``obj_id`` held in the session store.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.freecad.config import FreecadConfig
from tool_registry.tools.freecad.operations import FreecadOperations
from tool_registry.tools.freecad.session import FreecadSessionStore

logger = structlog.get_logger()


class FreecadServer(McpToolServer):
    """FreeCAD tool adapter for CAD operations via MCP.

    Stateless tools (file-in/file-out): export_geometry, generate_mesh,
    boolean_operation, get_properties, create_parametric.

    Stateful authoring tools (operate on a live session document, MET-528):
    open_session, close_session, describe_session, create_primitive,
    create_body, create_sketch, pad_sketch, export_model.
    """

    def __init__(self, config: FreecadConfig | None = None) -> None:
        super().__init__(adapter_id="freecad", version="0.2.0")
        self.config = config or FreecadConfig()
        self._ops = FreecadOperations(
            work_dir=self.config.work_dir, timeout=float(self.config.max_operation_time)
        )
        self._sessions = FreecadSessionStore()
        self._register_tools()
        self._register_authoring_tools()

    def _register_tools(self) -> None:
        """Register the stateless file-based FreeCAD tools."""
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

        self.register_tool(
            manifest=ToolManifest(
                tool_id="freecad.create_parametric",
                adapter_id="freecad",
                name="Create Parametric",
                description="Generate parametric CAD geometry from shape type and dimensions",
                capability="cad_generation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "shape_type": {
                            "type": "string",
                            "enum": ["bracket", "plate", "enclosure", "cylinder"],
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
                phase=2,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=300, max_disk_mb=512
                ),
            ),
            handler=self.create_parametric,
        )

    async def create_parametric(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Generate parametric CAD geometry from shape type and dimensions.

        In production, this invokes FreeCAD headless with a parametric script.
        For now, it validates arguments and raises NotImplementedError.
        """
        shape_type = arguments.get("shape_type", "")
        parameters = arguments.get("parameters", {})
        material = arguments.get("material", "")
        output_path = arguments.get("output_path", "")

        if not shape_type:
            raise ValueError("shape_type is required")
        if shape_type not in ("bracket", "plate", "enclosure", "cylinder"):
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

        result = await self._execute_parametric(shape_type, parameters, material, output_path)
        return result

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

        result = await self._execute_meshing(input_file, element_size, algorithm, output_format)
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

        result = await self._execute_boolean(input_file_a, input_file_b, operation, output_path)
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
        """Export a CAD file via FreeCAD (headless). STEP today; raises for others."""
        if output_format != "step":
            raise ValueError(
                f"FreeCAD adapter currently exports STEP only, not {output_format!r}"
            )
        return self._ops.export_step(input_file, output_path)

    async def _execute_meshing(
        self,
        input_file: str,
        element_size: float,
        algorithm: str,
        output_format: str,
    ) -> dict[str, Any]:
        """Generate a mesh via FreeCAD (headless)."""
        return self._ops.generate_mesh(input_file, element_size, algorithm, output_format)

    async def _execute_boolean(
        self,
        file_a: str,
        file_b: str,
        operation: str,
        output_path: str,
    ) -> dict[str, Any]:
        """Perform a boolean op via FreeCAD (headless)."""
        return self._ops.boolean_operation(file_a, file_b, operation, output_path)

    async def _execute_analysis(self, input_file: str, properties: list[str]) -> dict[str, Any]:
        """Extract geometric properties via FreeCAD (headless)."""
        return self._ops.get_properties(input_file, properties)

    async def _execute_parametric(
        self,
        shape_type: str,
        parameters: dict[str, Any],
        material: str,
        output_path: str,
    ) -> dict[str, Any]:
        """Generate a parametric shape via FreeCAD (headless)."""
        return self._ops.create_parametric(shape_type, parameters, material, output_path)

    # ------------------------------------------------------------------
    # Stateful authoring surface (MET-528)
    # ------------------------------------------------------------------

    def _register_authoring_tools(self) -> None:
        """Register session-lifecycle + PartDesign authoring tools."""
        limits = ResourceLimits(max_memory_mb=2048, max_cpu_seconds=120, max_disk_mb=512)

        def obj_schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
            return {"type": "object", "properties": props, "required": required}

        sid = {"type": "string", "description": "Session id from freecad.open_session"}

        specs: list[tuple[str, str, str, dict[str, Any], Any]] = [
            (
                "open_session",
                "Open a stateful FreeCAD authoring session (live document)",
                "cad_session",
                obj_schema({"name": {"type": "string"}}, []),
                self.open_session,
            ),
            (
                "close_session",
                "Close a FreeCAD session and free its document",
                "cad_session",
                obj_schema({"session_id": sid}, ["session_id"]),
                self.close_session,
            ),
            (
                "describe_session",
                "List the objects authored in a session",
                "cad_inspect",
                obj_schema({"session_id": sid}, ["session_id"]),
                self.describe_session,
            ),
            (
                "create_primitive",
                "Create a primitive solid (box/cylinder/sphere/cone/torus) in a session",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "kind": {
                            "type": "string",
                            "enum": ["box", "cylinder", "sphere", "cone", "torus"],
                        },
                        "parameters": {"type": "object"},
                    },
                    ["session_id", "kind"],
                ),
                self.create_primitive,
            ),
            (
                "create_body",
                "Create a PartDesign Body for parametric modelling",
                "cad_author",
                obj_schema({"session_id": sid, "name": {"type": "string"}}, ["session_id"]),
                self.create_body,
            ),
            (
                "create_sketch",
                "Create a sketch on a body's plane with 2D geometry (rectangle/circle/line)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]},
                        "elements": {"type": "array", "items": {"type": "object"}},
                    },
                    ["session_id", "body_id"],
                ),
                self.create_sketch,
            ),
            (
                "pad_sketch",
                "Extrude (pad) a sketch into a solid on its body",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "sketch_id": {"type": "string"},
                        "length": {"type": "number"},
                        "reversed": {"type": "boolean"},
                        "midplane": {"type": "boolean"},
                    },
                    ["session_id", "body_id", "sketch_id", "length"],
                ),
                self.pad_sketch,
            ),
            (
                "export_model",
                "Export a session object to STEP and return the bytes (base64)",
                "cad_export",
                obj_schema(
                    {"session_id": sid, "obj_id": {"type": "string"}}, ["session_id", "obj_id"]
                ),
                self.export_model,
            ),
        ]

        for name, description, capability, input_schema, handler in specs:
            self.register_tool(
                manifest=ToolManifest(
                    tool_id=f"freecad.{name}",
                    adapter_id="freecad",
                    name=name.replace("_", " ").title(),
                    description=description,
                    capability=capability,
                    input_schema=input_schema,
                    phase=2,
                    resource_limits=limits,
                ),
                handler=handler,
            )

    async def open_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._sessions.open_session(name=arguments.get("name", ""))
        return {"session_id": session_id}

    async def close_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        return {"closed": self._sessions.close_session(session_id)}

    async def describe_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._sessions.describe(self._require(arguments, "session_id"))

    async def create_primitive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        kind = self._require(arguments, "kind")
        session = self._sessions.get(session_id)
        obj = self._ops.create_primitive(session.document, kind, arguments.get("parameters", {}))
        obj_id = self._sessions.register_object(session_id, obj, "primitive", kind)
        return {"obj_id": obj_id, "kind": kind, **self._ops.shape_props(obj)}

    async def create_body(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        session = self._sessions.get(session_id)
        body = self._ops.create_body(session.document, arguments.get("name", "Body"))
        obj_id = self._sessions.register_object(
            session_id, body, "body", arguments.get("name", "Body")
        )
        return {"obj_id": obj_id, "kind": "body"}

    async def create_sketch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        sketch = self._ops.create_sketch(
            session.document,
            body,
            plane=arguments.get("plane", "XY"),
            elements=arguments.get("elements", []),
        )
        obj_id = self._sessions.register_object(session_id, sketch, "sketch", "Sketch")
        return {"obj_id": obj_id, "kind": "sketch", "body_id": body_id}

    async def pad_sketch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        sketch_id = self._require(arguments, "sketch_id")
        length = arguments.get("length")
        if length is None:
            raise ValueError("length is required")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        sketch = self._sessions.get_object(session_id, sketch_id)
        pad = self._ops.pad_sketch(
            session.document,
            body,
            sketch,
            float(length),
            reversed=bool(arguments.get("reversed", False)),
            midplane=bool(arguments.get("midplane", False)),
        )
        obj_id = self._sessions.register_object(session_id, pad, "feature", "Pad")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def export_model(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        obj_id = self._require(arguments, "obj_id")
        obj = self._sessions.get_object(session_id, obj_id)
        step_bytes = self._ops.export_object_step_bytes(obj)
        return {
            "obj_id": obj_id,
            "format": "step",
            "size_bytes": len(step_bytes),
            "step_base64": base64.b64encode(step_bytes).decode("ascii"),
            **self._ops.shape_props(obj),
        }

    @staticmethod
    def _require(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key, "")
        if not value:
            raise ValueError(f"{key} is required")
        return str(value)
