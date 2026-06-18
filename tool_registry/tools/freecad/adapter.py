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

# Joint types the live solver understands. Mirrors
# ``api_gateway.constraint.kinematics._JOINT_TYPES`` — duplicated (not imported)
# because tool_registry (Layer 3) must not import api_gateway (Layer 4). Keep in
# sync with the kinematics module.
_VALID_JOINT_TYPES: frozenset[str] = frozenset(
    {"fixed", "revolute", "slider", "cylindrical", "ball"}
)


class FreecadServer(McpToolServer):
    """FreeCAD tool adapter for CAD operations via MCP.

    Stateless tools (file-in/file-out): export_geometry, generate_mesh,
    boolean_operation, get_properties, create_parametric.

    Stateful authoring tools (operate on a live session document, MET-528/527):
    open_session, close_session, describe_session, create_primitive,
    create_body, create_sketch, pad_sketch, pocket_sketch, revolve_sketch,
    transform_object, fillet_edges, chamfer_edges, shell_solid, linear_pattern,
    polar_pattern, mirror_feature, loft_sketches, sweep_sketch, execute_code,
    export_model.

    Assembly authoring (MET-530): create_assembly, add_part_to_assembly,
    add_assembly_joint (emits the joint model the live solver consumes),
    list_joints.

    Parametric (MET-531): create_variable_set, set_expression.

    Skills (composite generators, MET-527/531): generate_enclosure, fastener_hole,
    thread_insert.
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
            raise ValueError(f"FreeCAD adapter currently exports STEP only, not {output_format!r}")
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
                "Create a sketch on a body's plane (offset along normal) with 2D geometry",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]},
                        "elements": {"type": "array", "items": {"type": "object"}},
                        "offset": {"type": "number"},
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
                "pocket_sketch",
                "Cut a pocket from a sketch on its body",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "sketch_id": {"type": "string"},
                        "depth": {"type": "number"},
                        "reversed": {"type": "boolean"},
                    },
                    ["session_id", "body_id", "sketch_id", "depth"],
                ),
                self.pocket_sketch,
            ),
            (
                "revolve_sketch",
                "Revolve a sketch around an axis (V/H) into a solid",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "sketch_id": {"type": "string"},
                        "angle": {"type": "number"},
                        "axis": {"type": "string", "enum": ["V", "H"]},
                        "reversed": {"type": "boolean"},
                    },
                    ["session_id", "body_id", "sketch_id"],
                ),
                self.revolve_sketch,
            ),
            (
                "transform_object",
                "Move and/or rotate a session object (set its placement)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "obj_id": {"type": "string"},
                        "position": {"type": "array", "items": {"type": "number"}},
                        "rotation": {"type": "object"},
                    },
                    ["session_id", "obj_id"],
                ),
                self.transform_object,
            ),
            (
                "fillet_edges",
                "Round edges of a body's tip (defaults to all edges)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "radius": {"type": "number"},
                        "edges": {"type": "array", "items": {"type": "string"}},
                    },
                    ["session_id", "body_id", "radius"],
                ),
                self.fillet_edges,
            ),
            (
                "chamfer_edges",
                "Chamfer edges of a body's tip (defaults to all edges)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "size": {"type": "number"},
                        "edges": {"type": "array", "items": {"type": "string"}},
                    },
                    ["session_id", "body_id", "size"],
                ),
                self.chamfer_edges,
            ),
            (
                "linear_pattern",
                "Replicate a feature N times along an axis (X/Y/Z)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "feature_id": {"type": "string"},
                        "count": {"type": "integer"},
                        "spacing": {"type": "number"},
                        "axis": {"type": "string", "enum": ["X", "Y", "Z"]},
                    },
                    ["session_id", "body_id", "feature_id", "count", "spacing"],
                ),
                self.linear_pattern,
            ),
            (
                "polar_pattern",
                "Replicate a feature N times around an axis (X/Y/Z)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "feature_id": {"type": "string"},
                        "count": {"type": "integer"},
                        "angle": {"type": "number"},
                        "axis": {"type": "string", "enum": ["X", "Y", "Z"]},
                    },
                    ["session_id", "body_id", "feature_id", "count"],
                ),
                self.polar_pattern,
            ),
            (
                "mirror_feature",
                "Mirror a feature across a body plane (XY/XZ/YZ)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "feature_id": {"type": "string"},
                        "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]},
                    },
                    ["session_id", "body_id", "feature_id"],
                ),
                self.mirror_feature,
            ),
            (
                "loft_sketches",
                "Loft a profile sketch through section sketches into a solid",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "profile_id": {"type": "string"},
                        "section_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    ["session_id", "body_id", "profile_id", "section_ids"],
                ),
                self.loft_sketches,
            ),
            (
                "sweep_sketch",
                "Sweep a profile sketch along a path sketch into a solid",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "profile_id": {"type": "string"},
                        "path_id": {"type": "string"},
                    },
                    ["session_id", "body_id", "profile_id", "path_id"],
                ),
                self.sweep_sketch,
            ),
            (
                "shell_solid",
                "Hollow a body's tip to a wall thickness, opening a face (Part makeThickness)",
                "cad_author",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "thickness": {"type": "number"},
                        "faces": {"type": "array", "items": {"type": "string"}},
                    },
                    ["session_id", "body_id", "thickness"],
                ),
                self.shell_solid,
            ),
            (
                "generate_enclosure",
                "Skill: parametric electronics enclosure (hollow box, open top)",
                "cad_skill",
                obj_schema(
                    {
                        "session_id": sid,
                        "length": {"type": "number"},
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "wall_thickness": {"type": "number"},
                    },
                    ["session_id", "length", "width", "height"],
                ),
                self.generate_enclosure,
            ),
            (
                "fastener_hole",
                "Skill: drill a (optionally counterbored) fastener hole in a body's top",
                "cad_skill",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "diameter": {"type": "number"},
                        "depth": {"type": "number"},
                        "counterbore_diameter": {"type": "number"},
                        "counterbore_depth": {"type": "number"},
                    },
                    ["session_id", "body_id", "x", "y", "diameter"],
                ),
                self.fastener_hole,
            ),
            (
                "thread_insert",
                "Skill: add a screw boss (heat-set insert) at (x,y) on a body's top",
                "cad_skill",
                obj_schema(
                    {
                        "session_id": sid,
                        "body_id": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "boss_diameter": {"type": "number"},
                        "boss_height": {"type": "number"},
                        "hole_diameter": {"type": "number"},
                        "hole_depth": {"type": "number"},
                    },
                    [
                        "session_id",
                        "body_id",
                        "x",
                        "y",
                        "boss_diameter",
                        "boss_height",
                        "hole_diameter",
                        "hole_depth",
                    ],
                ),
                self.thread_insert,
            ),
            (
                "execute_code",
                "Run a sandboxed FreeCAD Python script against the session doc "
                "(escape hatch; assign `result` to surface an object)",
                "cad_scripting",
                obj_schema(
                    {"session_id": sid, "code": {"type": "string"}},
                    ["session_id", "code"],
                ),
                self.execute_code,
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
            (
                "create_assembly",
                "Create an assembly container to group parts in a session",
                "cad_assembly",
                obj_schema({"session_id": sid, "name": {"type": "string"}}, ["session_id"]),
                self.create_assembly,
            ),
            (
                "add_part_to_assembly",
                "Add a session part to an assembly, optionally placing it",
                "cad_assembly",
                obj_schema(
                    {
                        "session_id": sid,
                        "assembly_id": {"type": "string"},
                        "part_id": {"type": "string"},
                        "position": {"type": "array", "items": {"type": "number"}},
                    },
                    ["session_id", "assembly_id", "part_id"],
                ),
                self.add_part_to_assembly,
            ),
            (
                "add_assembly_joint",
                "Joint two assembly parts (fixed/revolute/slider/cylindrical/ball) — "
                "drives the live kinematic solver",
                "cad_assembly",
                obj_schema(
                    {
                        "session_id": sid,
                        "assembly_id": {"type": "string"},
                        "base_id": {"type": "string"},
                        "follower_id": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": sorted(_VALID_JOINT_TYPES),
                        },
                        "axis": {"type": "array", "items": {"type": "number"}},
                        "anchor": {"type": "array", "items": {"type": "number"}},
                    },
                    ["session_id", "assembly_id", "base_id", "follower_id", "type"],
                ),
                self.add_assembly_joint,
            ),
            (
                "list_joints",
                "List the assembly joints authored in a session (live-solver shape)",
                "cad_inspect",
                obj_schema({"session_id": sid}, ["session_id"]),
                self.list_joints,
            ),
            (
                "measure",
                "Measure a session object: volume, area, bbox, CoM, vertex/edge/face counts",
                "cad_inspect",
                obj_schema(
                    {"session_id": sid, "obj_id": {"type": "string"}}, ["session_id", "obj_id"]
                ),
                self.measure,
            ),
            (
                "describe_model",
                "Geometry summary of a session object: dimensions, solid/hollow, counts",
                "cad_inspect",
                obj_schema(
                    {"session_id": sid, "obj_id": {"type": "string"}}, ["session_id", "obj_id"]
                ),
                self.describe_model,
            ),
            (
                "create_variable_set",
                "Create a VarSet of named parametric variables in a session",
                "cad_parametric",
                obj_schema(
                    {
                        "session_id": sid,
                        "name": {"type": "string"},
                        "variables": {"type": "object"},
                    },
                    ["session_id", "variables"],
                ),
                self.create_variable_set,
            ),
            (
                "set_expression",
                "Bind an object property to an expression (parametric link)",
                "cad_parametric",
                obj_schema(
                    {
                        "session_id": sid,
                        "obj_id": {"type": "string"},
                        "property": {"type": "string"},
                        "expression": {"type": "string"},
                    },
                    ["session_id", "obj_id", "property", "expression"],
                ),
                self.set_expression,
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
            offset=float(arguments.get("offset", 0.0)),
        )
        obj_id = self._sessions.register_object(session_id, sketch, "sketch", "Sketch")
        return {"obj_id": obj_id, "kind": "sketch", "body_id": body_id}

    async def loft_sketches(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body = self._sessions.get_object(session_id, self._require(arguments, "body_id"))
        profile = self._sessions.get_object(session_id, self._require(arguments, "profile_id"))
        section_ids = arguments.get("section_ids") or []
        if not section_ids:
            raise ValueError("section_ids is required (at least one section sketch)")
        sections = [self._sessions.get_object(session_id, s) for s in section_ids]
        session = self._sessions.get(session_id)
        loft = self._ops.loft_sketches(session.document, body, profile, sections)
        obj_id = self._sessions.register_object(session_id, loft, "feature", "Loft")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def sweep_sketch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body = self._sessions.get_object(session_id, self._require(arguments, "body_id"))
        profile = self._sessions.get_object(session_id, self._require(arguments, "profile_id"))
        path = self._sessions.get_object(session_id, self._require(arguments, "path_id"))
        session = self._sessions.get(session_id)
        sweep = self._ops.sweep_sketch(session.document, body, profile, path)
        obj_id = self._sessions.register_object(session_id, sweep, "feature", "Sweep")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

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

    async def pocket_sketch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        sketch_id = self._require(arguments, "sketch_id")
        depth = arguments.get("depth")
        if depth is None:
            raise ValueError("depth is required")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        sketch = self._sessions.get_object(session_id, sketch_id)
        pocket = self._ops.pocket_sketch(
            session.document,
            body,
            sketch,
            float(depth),
            reversed=bool(arguments.get("reversed", False)),
        )
        obj_id = self._sessions.register_object(session_id, pocket, "feature", "Pocket")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def revolve_sketch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        sketch_id = self._require(arguments, "sketch_id")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        sketch = self._sessions.get_object(session_id, sketch_id)
        rev = self._ops.revolve_sketch(
            session.document,
            body,
            sketch,
            float(arguments.get("angle", 360.0)),
            axis=str(arguments.get("axis", "V")),
            reversed=bool(arguments.get("reversed", False)),
        )
        obj_id = self._sessions.register_object(session_id, rev, "feature", "Revolution")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def transform_object(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        obj_id = self._require(arguments, "obj_id")
        session = self._sessions.get(session_id)
        obj = self._sessions.get_object(session_id, obj_id)
        rotation = arguments.get("rotation")
        self._ops.transform_object(
            session.document,
            obj,
            arguments.get("position"),
            rotation if isinstance(rotation, dict) else None,
        )
        return {"obj_id": obj_id, "transformed": True}

    async def _dress_up(
        self, arguments: dict[str, Any], op: str, param_key: str, sel_key: str
    ) -> dict[str, Any]:
        """Shared fillet/chamfer/shell handler: run the op on a body's tip, register
        the resulting feature, return the body's new shape props."""
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        amount = arguments.get(param_key)
        if amount is None:
            raise ValueError(f"{param_key} is required")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        selectors = arguments.get(sel_key)
        feature = getattr(self._ops, op)(
            session.document, body, float(amount), selectors if selectors else None
        )
        obj_id = self._sessions.register_object(session_id, feature, "feature", op)
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def fillet_edges(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._dress_up(arguments, "fillet_edges", "radius", "edges")

    async def chamfer_edges(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._dress_up(arguments, "chamfer_edges", "size", "edges")

    async def shell_solid(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body = self._sessions.get_object(session_id, self._require(arguments, "body_id"))
        thickness = arguments.get("thickness")
        if thickness is None:
            raise ValueError("thickness is required")
        session = self._sessions.get(session_id)
        faces = arguments.get("faces")
        shell = self._ops.shell_solid(
            session.document, body, float(thickness), faces if faces else None
        )
        # makeThickness yields a standalone Part::Feature (not a body feature),
        # so report the shell's own shape props, not the (now-hidden) body's.
        obj_id = self._sessions.register_object(session_id, shell, "feature", "Shell")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(shell)}

    async def linear_pattern(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body = self._sessions.get_object(session_id, self._require(arguments, "body_id"))
        feature = self._sessions.get_object(session_id, self._require(arguments, "feature_id"))
        count = arguments.get("count")
        spacing = arguments.get("spacing")
        if count is None or spacing is None:
            raise ValueError("count and spacing are required")
        session = self._sessions.get(session_id)
        pat = self._ops.linear_pattern(
            session.document,
            body,
            feature,
            int(count),
            float(spacing),
            axis=str(arguments.get("axis", "X")),
        )
        obj_id = self._sessions.register_object(session_id, pat, "feature", "LinearPattern")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def polar_pattern(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body = self._sessions.get_object(session_id, self._require(arguments, "body_id"))
        feature = self._sessions.get_object(session_id, self._require(arguments, "feature_id"))
        count = arguments.get("count")
        if count is None:
            raise ValueError("count is required")
        session = self._sessions.get(session_id)
        pat = self._ops.polar_pattern(
            session.document,
            body,
            feature,
            int(count),
            angle=float(arguments.get("angle", 360.0)),
            axis=str(arguments.get("axis", "Z")),
        )
        obj_id = self._sessions.register_object(session_id, pat, "feature", "PolarPattern")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def mirror_feature(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body = self._sessions.get_object(session_id, self._require(arguments, "body_id"))
        feature = self._sessions.get_object(session_id, self._require(arguments, "feature_id"))
        session = self._sessions.get(session_id)
        mir = self._ops.mirror_feature(
            session.document, body, feature, plane=str(arguments.get("plane", "YZ"))
        )
        obj_id = self._sessions.register_object(session_id, mir, "feature", "Mirrored")
        return {"obj_id": obj_id, "kind": "feature", **self._ops.shape_props(body)}

    async def generate_enclosure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        for k in ("length", "width", "height"):
            if arguments.get(k) is None:
                raise ValueError(f"{k} is required")
        session = self._sessions.get(session_id)
        shell = self._ops.generate_enclosure(
            session.document,
            float(arguments["length"]),
            float(arguments["width"]),
            float(arguments["height"]),
            float(arguments.get("wall_thickness", 2.0)),
        )
        obj_id = self._sessions.register_object(session_id, shell, "feature", "Enclosure")
        return {
            "obj_id": obj_id,
            "kind": "feature",
            "skill": "enclosure",
            **self._ops.shape_props(shell),
        }

    async def fastener_hole(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        for k in ("x", "y", "diameter"):
            if arguments.get(k) is None:
                raise ValueError(f"{k} is required")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        depth = arguments.get("depth")
        self._ops.fastener_hole(
            session.document,
            body,
            float(arguments["x"]),
            float(arguments["y"]),
            float(arguments["diameter"]),
            float(depth) if depth is not None else None,
            float(arguments.get("counterbore_diameter", 0.0)),
            float(arguments.get("counterbore_depth", 0.0)),
        )
        return {"body_id": body_id, "skill": "fastener_hole", **self._ops.shape_props(body)}

    async def thread_insert(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        body_id = self._require(arguments, "body_id")
        keys = ("x", "y", "boss_diameter", "boss_height", "hole_diameter", "hole_depth")
        for k in keys:
            if arguments.get(k) is None:
                raise ValueError(f"{k} is required")
        session = self._sessions.get(session_id)
        body = self._sessions.get_object(session_id, body_id)
        self._ops.thread_insert(session.document, body, *(float(arguments[k]) for k in keys))
        return {"body_id": body_id, "skill": "thread_insert", **self._ops.shape_props(body)}

    async def execute_code(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        code = self._require(arguments, "code")
        session = self._sessions.get(session_id)
        result = self._ops.execute_code(session.document, code)
        if result is not None and hasattr(result, "Shape"):
            obj_id = self._sessions.register_object(
                session_id, result, "feature", getattr(result, "Name", "Result")
            )
            out: dict[str, Any] = {"executed": True, "obj_id": obj_id}
            try:
                out.update(self._ops.shape_props(result))
            except Exception:  # noqa: BLE001 — result may have no solid shape
                pass
            return out
        return {"executed": True, "session": self._sessions.describe(session_id)}

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

    # ---- assembly authoring (MET-530) ---------------------------------

    async def create_assembly(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        session = self._sessions.get(session_id)
        name = arguments.get("name", "Assembly")
        assembly = self._ops.create_assembly(session.document, name)
        obj_id = self._sessions.register_object(session_id, assembly, "assembly", name)
        return {"obj_id": obj_id, "kind": "assembly"}

    async def add_part_to_assembly(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        assembly_id = self._require(arguments, "assembly_id")
        part_id = self._require(arguments, "part_id")
        session = self._sessions.get(session_id)
        assembly = self._sessions.get_object(session_id, assembly_id)
        part = self._sessions.get_object(session_id, part_id)
        position = arguments.get("position")
        placement = {"position": position} if position else None
        self._ops.add_part_to_assembly(session.document, assembly, part, placement)
        return {"assembly_id": assembly_id, "part_id": part_id}

    async def add_assembly_joint(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Joint two parts. The joint metadata (type/axis/anchor/base/follower) is
        recorded in the kinematics-solver shape; the live solver (MET-530) consumes
        it via list_joints. A real FreeCAD ``Assembly::Joint`` for the on-Apply full
        solve is the FreeCAD-runtime follow-up."""
        session_id = self._require(arguments, "session_id")
        assembly_id = self._require(arguments, "assembly_id")
        base_id = self._require(arguments, "base_id")
        follower_id = self._require(arguments, "follower_id")
        jtype = self._require(arguments, "type").lower()
        if jtype not in _VALID_JOINT_TYPES:
            raise ValueError(
                f"unknown joint type {jtype!r}; expected one of {sorted(_VALID_JOINT_TYPES)}"
            )
        # Resolve part references to the names the viewer/manifest uses as group names.
        base = self._sessions.get_entry(session_id, base_id)
        follower = self._sessions.get_entry(session_id, follower_id)
        axis = arguments.get("axis") or [0.0, 0.0, 1.0]
        anchor = arguments.get("anchor") or [0.0, 0.0, 0.0]
        joint_name = str(arguments.get("name") or f"{base.name}-{follower.name}")
        joint_meta: dict[str, Any] = {
            "name": joint_name,
            "type": jtype,
            "base": base.name,
            "follower": follower.name,
            "axis": [float(axis[0]), float(axis[1]), float(axis[2])],
            "anchor": [float(anchor[0]), float(anchor[1]), float(anchor[2])],
            "assembly_id": assembly_id,
        }
        obj_id = self._sessions.register_object(
            session_id, None, "joint", joint_name, metadata=joint_meta
        )
        return {"obj_id": obj_id, "kind": "joint", "joint": joint_meta}

    async def list_joints(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        return {"joints": self._sessions.joints(session_id)}

    async def measure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        obj = self._sessions.get_object(session_id, self._require(arguments, "obj_id"))
        return self._ops.measure(obj)

    async def describe_model(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        obj = self._sessions.get_object(session_id, self._require(arguments, "obj_id"))
        return self._ops.describe_model(obj)

    # ---- parametric modelling (MET-531) -------------------------------

    async def create_variable_set(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        variables = arguments.get("variables")
        if not isinstance(variables, dict) or not variables:
            raise ValueError("variables is required (a non-empty object)")
        name = arguments.get("name", "Params")
        session = self._sessions.get(session_id)
        varset = self._ops.create_variable_set(session.document, name, variables)
        obj_id = self._sessions.register_object(
            session_id, varset, "varset", name, metadata={"variables": variables}
        )
        return {"obj_id": obj_id, "kind": "varset", "variables": list(variables)}

    async def set_expression(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require(arguments, "session_id")
        obj_id = self._require(arguments, "obj_id")
        prop = self._require(arguments, "property")
        expression = self._require(arguments, "expression")
        session = self._sessions.get(session_id)
        obj = self._sessions.get_object(session_id, obj_id)
        self._ops.set_expression(session.document, obj, prop, expression)
        return {"obj_id": obj_id, "property": prop, "expression": expression}

    @staticmethod
    def _require(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key, "")
        if not value:
            raise ValueError(f"{key} is required")
        return str(value)
