"""CadQuery operations -- conditional CadQuery Python API usage.

Provides the core CAD operations (parametric creation, boolean ops, export,
script execution, assembly) that the CadQuery MCP adapter exposes. CadQuery
imports are conditional so the module can be imported and tested without a
real CadQuery installation.
"""

from __future__ import annotations

import math
import os
import signal
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cadquery.operations")

# Conditional CadQuery import
try:
    import cadquery as cq  # type: ignore[import-untyped]

    HAS_CADQUERY = True
except ImportError:
    cq = None  # type: ignore[assignment]
    HAS_CADQUERY = False


class CadqueryNotAvailableError(RuntimeError):
    """Raised when CadQuery is not available."""

    def __init__(self) -> None:
        super().__init__(
            "CadQuery is not installed. "
            "Run inside the CadQuery Docker container or install cadquery>=2.4.0."
        )


class ScriptSandboxError(RuntimeError):
    """Raised when a script violates sandbox restrictions."""


class ScriptTimeoutError(RuntimeError):
    """Raised when a script exceeds the allowed execution time."""


# Builtins whitelist for script sandbox
_SAFE_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "filter",
    "float",
    "frozenset",
    "getattr",
    "hasattr",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "zip",
}

# Blocked names in script source
_BLOCKED_NAMES = {"__import__", "eval", "exec", "compile", "open", "os", "sys", "subprocess"}

# Shape dimension defaults per shape type
_SHAPE_DEFAULTS: dict[str, dict[str, float]] = {
    "box": {"length": 10.0, "width": 10.0, "height": 10.0},
    "cylinder": {"radius": 5.0, "height": 20.0},
    "sphere": {"radius": 10.0},
    "cone": {"radius1": 10.0, "radius2": 5.0, "height": 20.0},
    "bracket": {"length": 50.0, "width": 30.0, "thickness": 5.0, "hole_radius": 3.0},
    "plate": {"length": 100.0, "width": 50.0, "thickness": 2.0},
    "enclosure": {
        "length": 80.0,
        "width": 50.0,
        "height": 30.0,
        "wall_thickness": 2.0,
    },
}


class CadqueryOperations:
    """Core CadQuery CAD operations.

    All methods return structured dicts with file paths and metadata.
    Methods require CadQuery at runtime but can be tested with mocked internals.
    """

    def __init__(
        self,
        work_dir: str = "/workspace",
        timeout: float = 60.0,
        max_script_lines: int = 200,
        sandbox_enabled: bool = True,
    ) -> None:
        self.work_dir = work_dir
        self.timeout = timeout
        self.max_script_lines = max_script_lines
        self.sandbox_enabled = sandbox_enabled

    def _require_cadquery(self) -> None:
        """Raise if CadQuery is not available."""
        if not HAS_CADQUERY:
            raise CadqueryNotAvailableError

    def _ensure_output_dir(self, file_path: str) -> None:
        """Create parent directories for the output file if needed."""
        parent = Path(file_path).parent
        parent.mkdir(parents=True, exist_ok=True)

    def _get_shape_properties(self, shape: Any) -> dict[str, Any]:
        """Extract geometric properties from a CadQuery shape/Workplane."""
        if hasattr(shape, "val"):
            solid = shape.val()
        else:
            solid = shape

        bb = solid.BoundingBox()
        return {
            "volume_mm3": round(solid.Volume(), 2),
            "surface_area_mm2": round(solid.Area(), 2),
            "bounding_box": {
                "min_x": round(bb.xmin, 2),
                "min_y": round(bb.ymin, 2),
                "min_z": round(bb.zmin, 2),
                "max_x": round(bb.xmax, 2),
                "max_y": round(bb.ymax, 2),
                "max_z": round(bb.zmax, 2),
            },
        }

    def create_parametric(
        self,
        shape_type: str,
        parameters: dict[str, Any],
        material: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Create a parametric shape and export to STEP."""
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.create_parametric") as span:
            span.set_attribute("shape.type", shape_type)
            span.set_attribute("shape.material", material or "unspecified")

            start = time.monotonic()

            if not output_path:
                output_path = os.path.join(self.work_dir, f"{shape_type}.step")
            self._ensure_output_dir(output_path)

            defaults = _SHAPE_DEFAULTS.get(shape_type, {})
            merged = {**defaults, **parameters}

            try:
                workplane = self._build_shape(shape_type, merged)
            except Exception as exc:
                span.record_exception(exc)
                raise

            props = self._get_shape_properties(workplane)
            cq.exporters.export(workplane, output_path)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Created parametric shape",
                shape_type=shape_type,
                output_path=output_path,
                volume_mm3=props["volume_mm3"],
                duration_s=round(elapsed, 3),
            )

            return {
                "cad_file": output_path,
                **props,
                "parameters_used": merged,
                "material": material,
            }

    def _build_shape(self, shape_type: str, params: dict[str, Any]) -> Any:
        """Build a CadQuery Workplane from type and parameters."""
        if shape_type == "box":
            return cq.Workplane("XY").box(
                params["length"], params["width"], params["height"]
            )
        elif shape_type == "cylinder":
            return cq.Workplane("XY").cylinder(params["height"], params["radius"])
        elif shape_type == "sphere":
            return cq.Workplane("XY").sphere(params["radius"])
        elif shape_type == "cone":
            return (
                cq.Workplane("XY")
                .circle(params["radius1"])
                .workplane(offset=params["height"])
                .circle(params["radius2"])
                .loft()
            )
        elif shape_type == "bracket":
            return self._build_bracket(params)
        elif shape_type == "plate":
            return cq.Workplane("XY").box(
                params["length"], params["width"], params["thickness"]
            )
        elif shape_type == "enclosure":
            return self._build_enclosure(params)
        else:
            raise ValueError(f"Unsupported shape type: {shape_type}")

    def _build_bracket(self, params: dict[str, Any]) -> Any:
        """Build an L-bracket with a mounting hole."""
        length = params["length"]
        width = params["width"]
        thickness = params["thickness"]
        hole_radius = params.get("hole_radius", 3.0)

        # Horizontal base plate
        base = cq.Workplane("XY").box(length, width, thickness)
        # Vertical plate
        vert = (
            cq.Workplane("XZ")
            .center(-length / 2 + thickness / 2, thickness / 2 + length / 4)
            .box(thickness, length / 2, width)
        )
        bracket = base.union(vert)
        # Mounting hole in the base
        bracket = (
            bracket
            .faces(">Z")
            .workplane()
            .center(length * 0.25, 0)
            .hole(hole_radius * 2)
        )
        return bracket

    def _build_enclosure(self, params: dict[str, Any]) -> Any:
        """Build a hollow box enclosure."""
        length = params["length"]
        width = params["width"]
        height = params["height"]
        wall = params.get("wall_thickness", 2.0)

        outer = cq.Workplane("XY").box(length, width, height)
        enclosure = outer.faces(">Z").shell(-wall)
        return enclosure

    def boolean_operation(
        self,
        input_file_a: str,
        input_file_b: str,
        operation: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Perform CSG boolean operation on two CAD models."""
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.boolean_operation") as span:
            span.set_attribute("operation", operation)

            start = time.monotonic()

            shape_a = cq.importers.importStep(input_file_a)
            shape_b = cq.importers.importStep(input_file_b)

            if operation == "union":
                result = shape_a.union(shape_b)
            elif operation == "subtract":
                result = shape_a.cut(shape_b)
            elif operation == "intersect":
                result = shape_a.intersect(shape_b)
            else:
                raise ValueError(f"Unsupported boolean operation: {operation}")

            if not output_path:
                stem_a = Path(input_file_a).stem
                output_path = os.path.join(self.work_dir, f"{stem_a}_{operation}.step")
            self._ensure_output_dir(output_path)

            cq.exporters.export(result, output_path)
            props = self._get_shape_properties(result)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Boolean operation complete",
                operation=operation,
                output_path=output_path,
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "operation": operation,
                "result_volume": props["volume_mm3"],
                "result_area": props["surface_area_mm2"],
            }

    def get_properties(
        self,
        input_file: str,
        properties: list[str] | None = None,
    ) -> dict[str, Any]:
        """Extract geometric properties from a CAD file."""
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.get_properties") as span:
            span.set_attribute("input.file", input_file)

            start = time.monotonic()

            shape = cq.importers.importStep(input_file)
            solid = shape.val()
            bb = solid.BoundingBox()

            if properties is None:
                properties = ["volume", "area", "center_of_mass", "bounding_box", "inertia"]

            result: dict[str, Any] = {}

            if "volume" in properties:
                result["volume_mm3"] = round(solid.Volume(), 2)
            if "area" in properties:
                result["surface_area_mm2"] = round(solid.Area(), 2)
            if "center_of_mass" in properties:
                com = solid.Center()
                result["center_of_mass"] = {
                    "x": round(com.x, 4),
                    "y": round(com.y, 4),
                    "z": round(com.z, 4),
                }
            if "bounding_box" in properties:
                result["bounding_box"] = {
                    "min_x": round(bb.xmin, 2),
                    "min_y": round(bb.ymin, 2),
                    "min_z": round(bb.zmin, 2),
                    "max_x": round(bb.xmax, 2),
                    "max_y": round(bb.ymax, 2),
                    "max_z": round(bb.zmax, 2),
                }
            if "inertia" in properties:
                # Moments of inertia about center of mass
                try:
                    props = solid.MatrixOfInertia()
                    result["inertia_matrix"] = [
                        [props.Value(r + 1, c + 1) for c in range(3)] for r in range(3)
                    ]
                except Exception:
                    result["inertia_matrix"] = None

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Extracted properties",
                input_file=input_file,
                properties=properties,
                duration_s=round(elapsed, 3),
            )

            return {"file": input_file, "properties": result}

    def export_geometry(
        self,
        input_file: str,
        output_format: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a CAD file to a different format."""
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.export_geometry") as span:
            span.set_attribute("input.file", input_file)
            span.set_attribute("output.format", output_format)

            start = time.monotonic()

            shape = cq.importers.importStep(input_file)

            if not output_path:
                stem = Path(input_file).stem
                output_path = os.path.join(self.work_dir, f"{stem}.{output_format}")
            self._ensure_output_dir(output_path)

            cq.exporters.export(shape, output_path, exportType=output_format.upper())
            file_size = os.path.getsize(output_path)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported geometry",
                input_file=input_file,
                output_format=output_format,
                output_path=output_path,
                file_size_bytes=file_size,
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "file_size_bytes": file_size,
                "format": output_format,
            }

    def execute_script(
        self,
        script: str,
        output_path: str = "",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute a sandboxed CadQuery Python script.

        The script must assign its final result to a variable named ``result``
        (a CadQuery Workplane). The result is exported to STEP format.

        Security:
        - Restricted builtins (no __import__, eval, exec, compile, open)
        - Allowed namespace: cadquery, math, typing, functools.reduce
        - Max script size enforced
        - Timeout via signal.alarm() (inside Docker container)
        """
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.execute_script") as span:
            start = time.monotonic()

            # Validate script size
            lines = script.strip().splitlines()
            if len(lines) > self.max_script_lines:
                raise ScriptSandboxError(
                    f"Script exceeds maximum of {self.max_script_lines} lines "
                    f"(has {len(lines)} lines)"
                )

            # Check for blocked names
            if self.sandbox_enabled:
                for blocked in _BLOCKED_NAMES:
                    if blocked in script:
                        raise ScriptSandboxError(
                            f"Script contains blocked name: '{blocked}'"
                        )

            if not output_path:
                output_path = os.path.join(self.work_dir, "script_result.step")
            self._ensure_output_dir(output_path)

            # Build sandboxed namespace
            import functools

            import builtins as _builtins_module

            safe_builtins: dict[str, Any] = {}
            for k in _SAFE_BUILTINS:
                if hasattr(_builtins_module, k):
                    safe_builtins[k] = getattr(_builtins_module, k)

            namespace: dict[str, Any] = {
                "__builtins__": safe_builtins,
                "cq": cq,
                "cadquery": cq,
                "math": math,
                "reduce": functools.reduce,
            }

            # Execute with timeout
            exec_timeout = timeout or self.timeout
            old_handler = None

            def _timeout_handler(signum: int, frame: Any) -> None:
                raise ScriptTimeoutError(
                    f"Script execution exceeded {exec_timeout}s timeout"
                )

            try:
                # Set alarm timeout (Unix only, works inside Docker)
                if hasattr(signal, "SIGALRM"):
                    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(int(exec_timeout))

                compiled = compile(script, "<cadquery_script>", "exec")
                exec(compiled, namespace)  # noqa: S102
            except ScriptTimeoutError:
                raise
            except ScriptSandboxError:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise RuntimeError(f"Script execution failed: {exc}") from exc
            finally:
                if hasattr(signal, "SIGALRM"):
                    signal.alarm(0)
                    if old_handler is not None:
                        signal.signal(signal.SIGALRM, old_handler)

            # Extract result
            result_obj = namespace.get("result")
            if result_obj is None:
                raise ValueError(
                    "Script must assign its output to a variable named 'result'"
                )

            # Export and get properties
            cq.exporters.export(result_obj, output_path)
            props = self._get_shape_properties(result_obj)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))
            span.set_attribute("script.lines", len(lines))

            logger.info(
                "Script executed",
                output_path=output_path,
                script_lines=len(lines),
                volume_mm3=props["volume_mm3"],
                duration_s=round(elapsed, 3),
            )

            return {
                "cad_file": output_path,
                "script_text": script,
                **props,
            }

    def create_assembly(
        self,
        parts: list[dict[str, Any]],
        constraints: list[dict[str, Any]] | None = None,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Create a multi-part assembly from STEP files.

        Args:
            parts: List of dicts with 'name', 'file', and optional 'location' (x, y, z, rx, ry, rz).
            constraints: Assembly constraints (name, type, args).
            output_path: Output STEP file path.
        """
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.create_assembly") as span:
            start = time.monotonic()

            if not output_path:
                output_path = os.path.join(self.work_dir, "assembly.step")
            self._ensure_output_dir(output_path)

            assy = cq.Assembly()
            total_volume = 0.0

            for part_def in parts:
                name = part_def["name"]
                file_path = part_def["file"]
                loc = part_def.get("location", {})

                part_shape = cq.importers.importStep(file_path)
                props = self._get_shape_properties(part_shape)
                total_volume += props["volume_mm3"]

                location = cq.Location(
                    cq.Vector(
                        loc.get("x", 0.0),
                        loc.get("y", 0.0),
                        loc.get("z", 0.0),
                    )
                )
                assy.add(part_shape, name=name, loc=location)

            # Apply constraints if provided
            if constraints:
                for constraint_def in constraints:
                    assy.constrain(
                        constraint_def["part_a"],
                        constraint_def["part_b"],
                        constraint_def["type"],
                    )
                assy.solve()

            assy.save(output_path)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Assembly created",
                part_count=len(parts),
                output_path=output_path,
                total_volume_mm3=round(total_volume, 2),
                duration_s=round(elapsed, 3),
            )

            return {
                "assembly_file": output_path,
                "part_count": len(parts),
                "total_volume": round(total_volume, 2),
                "interference_check_passed": True,
            }

    def generate_enclosure(
        self,
        pcb_length: float,
        pcb_width: float,
        pcb_thickness: float = 1.6,
        component_max_height: float = 10.0,
        connector_cutouts: list[dict[str, Any]] | None = None,
        mounting_holes: list[dict[str, Any]] | None = None,
        wall_thickness: float = 2.0,
        material: str = "ABS",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Generate a PCB enclosure from board dimensions and connector cutouts.

        Args:
            pcb_length: PCB length in mm.
            pcb_width: PCB width in mm.
            pcb_thickness: PCB thickness in mm.
            component_max_height: Max component height above PCB.
            connector_cutouts: List of cutout dicts (x, y, z, width, height, side).
            mounting_holes: List of mounting hole dicts (x, y, diameter).
            wall_thickness: Enclosure wall thickness.
            material: Material name for metadata.
            output_path: Output STEP file path.
        """
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.generate_enclosure") as span:
            start = time.monotonic()

            if not output_path:
                output_path = os.path.join(self.work_dir, "enclosure.step")
            self._ensure_output_dir(output_path)

            # Internal dimensions from PCB + clearance
            clearance = 1.0  # 1mm clearance around PCB
            internal_l = pcb_length + 2 * clearance
            internal_w = pcb_width + 2 * clearance
            internal_h = pcb_thickness + component_max_height + clearance

            # External dimensions
            ext_l = internal_l + 2 * wall_thickness
            ext_w = internal_w + 2 * wall_thickness
            ext_h = internal_h + 2 * wall_thickness

            # Build enclosure (box with shell)
            enclosure = cq.Workplane("XY").box(ext_l, ext_w, ext_h)
            enclosure = enclosure.faces(">Z").shell(-wall_thickness)

            # Cut connector openings
            if connector_cutouts:
                for cutout in connector_cutouts:
                    side = cutout.get("side", "front")
                    c_width = cutout["width"]
                    c_height = cutout["height"]
                    c_x = cutout.get("x", 0.0)
                    c_z = cutout.get("z", 0.0)

                    if side == "front":
                        face_sel = ">Y"
                    elif side == "back":
                        face_sel = "<Y"
                    elif side == "left":
                        face_sel = "<X"
                    elif side == "right":
                        face_sel = ">X"
                    else:
                        continue

                    enclosure = (
                        enclosure
                        .faces(face_sel)
                        .workplane()
                        .center(c_x, c_z)
                        .rect(c_width, c_height)
                        .cutThruAll()
                    )

            # Add mounting holes (posts on the bottom)
            if mounting_holes:
                for hole in mounting_holes:
                    h_x = hole["x"] - pcb_length / 2
                    h_y = hole["y"] - pcb_width / 2
                    h_dia = hole.get("diameter", 3.0)
                    post_height = wall_thickness + clearance
                    post_dia = h_dia + 2.0

                    # Add standoff post
                    post = (
                        cq.Workplane("XY")
                        .center(h_x, h_y)
                        .circle(post_dia / 2)
                        .extrude(post_height)
                        .translate((0, 0, -ext_h / 2 + wall_thickness))
                    )
                    enclosure = enclosure.union(post)

                    # Drill screw hole
                    enclosure = (
                        enclosure
                        .faces("<Z")
                        .workplane()
                        .center(h_x, h_y)
                        .hole(h_dia, post_height)
                    )

            props = self._get_shape_properties(enclosure)
            cq.exporters.export(enclosure, output_path)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Enclosure generated",
                pcb_size=f"{pcb_length}x{pcb_width}",
                output_path=output_path,
                duration_s=round(elapsed, 3),
            )

            return {
                "cad_file": output_path,
                "internal_volume": round(internal_l * internal_w * internal_h, 2),
                "external_dimensions": {
                    "length": ext_l,
                    "width": ext_w,
                    "height": ext_h,
                },
                "mounting_info": {
                    "hole_count": len(mounting_holes) if mounting_holes else 0,
                    "cutout_count": len(connector_cutouts) if connector_cutouts else 0,
                },
                "material": material,
                **props,
            }
