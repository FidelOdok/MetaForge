"""FreeCAD operations -- conditional FreeCAD Python API usage.

Provides the core CAD operations (parametric creation, STEP export, mesh generation)
that the FreeCAD MCP adapter exposes. FreeCAD imports are conditional so the module
can be imported and tested without a real FreeCAD installation.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.freecad.operations")

# Conditional FreeCAD import -- allows testing without FreeCAD installed
try:
    import FreeCAD  # type: ignore[import-untyped]
    import Part  # type: ignore[import-untyped]

    HAS_FREECAD = True
except ImportError:
    FreeCAD = None  # type: ignore[assignment]
    Part = None  # type: ignore[assignment]
    HAS_FREECAD = False

# Conditional Mesh import (FEM workbench)
try:
    import Mesh  # type: ignore[import-untyped]

    HAS_MESH = True
except ImportError:
    Mesh = None  # type: ignore[assignment]
    HAS_MESH = False


class FreecadNotAvailableError(RuntimeError):
    """Raised when FreeCAD Python bindings are not available."""

    def __init__(self) -> None:
        super().__init__(
            "FreeCAD Python bindings are not installed. "
            "Run inside the FreeCAD Docker container or install FreeCAD with Python support."
        )


# Shape dimension defaults per shape type
_SHAPE_DEFAULTS: dict[str, dict[str, float]] = {
    "box": {"length": 10.0, "width": 10.0, "height": 10.0},
    "cylinder": {"radius": 5.0, "height": 20.0},
    "sphere": {"radius": 10.0},
    "cone": {"radius1": 10.0, "radius2": 5.0, "height": 20.0},
    "torus": {"radius1": 10.0, "radius2": 2.0},
    "bracket": {"length": 50.0, "width": 30.0, "thickness": 5.0, "hole_radius": 3.0},
    "plate": {"length": 100.0, "width": 50.0, "thickness": 2.0},
    "enclosure": {
        "length": 80.0,
        "width": 50.0,
        "height": 30.0,
        "wall_thickness": 2.0,
    },
}


class FreecadOperations:
    """Core FreeCAD CAD operations.

    All methods return structured dicts with file paths and metadata.
    Methods require FreeCAD Python bindings at runtime but can be tested
    with mocked internals.
    """

    def __init__(self, work_dir: str = "/workspace", timeout: float = 60.0) -> None:
        self.work_dir = work_dir
        self.timeout = timeout

    def _require_freecad(self) -> None:
        """Raise if FreeCAD is not available."""
        if not HAS_FREECAD:
            raise FreecadNotAvailableError

    def _ensure_output_dir(self, file_path: str) -> None:
        """Create parent directories for the output file if needed."""
        parent = Path(file_path).parent
        parent.mkdir(parents=True, exist_ok=True)

    def create_parametric(
        self,
        shape_type: str,
        parameters: dict[str, Any],
        material: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Create a parametric shape and export to STEP.

        Args:
            shape_type: One of box, cylinder, sphere, cone, bracket, plate, enclosure.
            parameters: Shape-specific dimensions.
            material: Material name for metadata.
            output_path: Where to write the STEP file.

        Returns:
            Dict with cad_file path, volume, surface area, bounding box, and parameters.
        """
        self._require_freecad()

        with tracer.start_as_current_span("freecad.create_parametric") as span:
            span.set_attribute("shape.type", shape_type)
            span.set_attribute("shape.material", material or "unspecified")

            start = time.monotonic()

            if not output_path:
                output_path = os.path.join(self.work_dir, f"{shape_type}.step")
            self._ensure_output_dir(output_path)

            # Merge defaults with provided parameters
            defaults = _SHAPE_DEFAULTS.get(shape_type, {})
            merged = {**defaults, **parameters}

            try:
                shape = self._build_shape(shape_type, merged)
            except Exception as exc:
                span.record_exception(exc)
                raise

            # Compute properties
            volume = shape.Volume
            area = shape.Area
            bb = shape.BoundBox

            # Export STEP
            shape.exportStep(output_path)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Created parametric shape",
                shape_type=shape_type,
                output_path=output_path,
                volume_mm3=round(volume, 2),
                duration_s=round(elapsed, 3),
            )

            return {
                "cad_file": output_path,
                "volume_mm3": round(volume, 2),
                "surface_area_mm2": round(area, 2),
                "bounding_box": {
                    "min_x": round(bb.XMin, 2),
                    "min_y": round(bb.YMin, 2),
                    "min_z": round(bb.ZMin, 2),
                    "max_x": round(bb.XMax, 2),
                    "max_y": round(bb.YMax, 2),
                    "max_z": round(bb.ZMax, 2),
                },
                "parameters_used": merged,
                "material": material,
            }

    def _build_shape(self, shape_type: str, params: dict[str, Any]) -> Any:
        """Build a FreeCAD Part shape from type and parameters."""
        if shape_type == "box":
            return Part.makeBox(
                params["length"],
                params["width"],
                params["height"],
            )
        elif shape_type == "cylinder":
            return Part.makeCylinder(params["radius"], params["height"])
        elif shape_type == "sphere":
            return Part.makeSphere(params["radius"])
        elif shape_type == "cone":
            return Part.makeCone(
                params["radius1"],
                params["radius2"],
                params["height"],
            )
        elif shape_type == "bracket":
            return self._build_bracket(params)
        elif shape_type == "plate":
            return Part.makeBox(
                params["length"],
                params["width"],
                params["thickness"],
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

        # Horizontal plate
        base = Part.makeBox(length, width, thickness)
        # Vertical plate
        import FreeCAD as FC  # type: ignore[import-untyped]

        vert = Part.makeBox(thickness, width, length / 2)
        vert.translate(FC.Vector(0, 0, thickness))
        bracket = base.fuse(vert)

        # Mounting hole in the base plate
        hole = Part.makeCylinder(
            hole_radius,
            thickness * 2,
            FC.Vector(length * 0.75, width / 2, -thickness / 2),
            FC.Vector(0, 0, 1),
        )
        bracket = bracket.cut(hole)
        return bracket

    def _build_enclosure(self, params: dict[str, Any]) -> Any:
        """Build a hollow box enclosure."""
        length = params["length"]
        width = params["width"]
        height = params["height"]
        wall = params.get("wall_thickness", 2.0)

        outer = Part.makeBox(length, width, height)
        import FreeCAD as FC  # type: ignore[import-untyped]

        inner = Part.makeBox(
            length - 2 * wall,
            width - 2 * wall,
            height - wall,
        )
        inner.translate(FC.Vector(wall, wall, wall))
        return outer.cut(inner)

    def export_step(
        self,
        input_file: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Load a CAD file and export to STEP format.

        Args:
            input_file: Path to the source CAD file.
            output_path: Where to write the STEP file.

        Returns:
            Dict with output file path and metadata.
        """
        self._require_freecad()

        with tracer.start_as_current_span("freecad.export_step") as span:
            span.set_attribute("input.file", input_file)

            start = time.monotonic()

            if not output_path:
                stem = Path(input_file).stem
                output_path = os.path.join(self.work_dir, f"{stem}.step")
            self._ensure_output_dir(output_path)

            try:
                doc = FreeCAD.openDocument(input_file)
                shapes = []
                for obj in doc.Objects:
                    if hasattr(obj, "Shape"):
                        shapes.append(obj.Shape)

                if not shapes:
                    raise ValueError(f"No shapes found in {input_file}")

                # Fuse all shapes or use the single shape
                if len(shapes) == 1:
                    combined = shapes[0]
                else:
                    combined = shapes[0]
                    for s in shapes[1:]:
                        combined = combined.fuse(s)

                combined.exportStep(output_path)
                file_size = os.path.getsize(output_path)

                FreeCAD.closeDocument(doc.Name)
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported STEP",
                input_file=input_file,
                output_path=output_path,
                file_size_bytes=file_size,
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "file_size_bytes": file_size,
                "format": "step",
            }

    def generate_mesh(
        self,
        input_file: str,
        element_size: float = 1.0,
        algorithm: str = "netgen",
        output_format: str = "inp",
    ) -> dict[str, Any]:
        """Generate a finite element mesh from a CAD file.

        Args:
            input_file: Path to the source CAD file.
            element_size: Target element size for meshing.
            algorithm: Meshing algorithm (netgen, gmsh, mefisto).
            output_format: Output format (inp, unv, stl).

        Returns:
            Dict with mesh file path, node/element counts, and quality metrics.
        """
        self._require_freecad()

        with tracer.start_as_current_span("freecad.mesh") as span:
            span.set_attribute("input.file", input_file)
            span.set_attribute("mesh.element_size", element_size)
            span.set_attribute("mesh.algorithm", algorithm)

            start = time.monotonic()

            stem = Path(input_file).stem
            output_path = os.path.join(self.work_dir, f"{stem}.{output_format}")
            self._ensure_output_dir(output_path)

            try:
                doc = FreeCAD.openDocument(input_file)

                # Find the first shape object
                shape = None
                for obj in doc.Objects:
                    if hasattr(obj, "Shape"):
                        shape = obj.Shape
                        break

                if shape is None:
                    raise ValueError(f"No shapes found in {input_file}")

                # Use Mesh module for meshing
                if not HAS_MESH:
                    raise RuntimeError("FreeCAD Mesh module is not available")

                mesh_obj = Mesh.Mesh()
                mesh_obj.addFacets(shape.tessellate(element_size)[1])

                # Export
                mesh_obj.write(output_path)

                num_points = mesh_obj.CountPoints
                num_facets = mesh_obj.CountFacets

                FreeCAD.closeDocument(doc.Name)
            except Exception as exc:
                span.record_exception(exc)
                raise

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Generated mesh",
                input_file=input_file,
                output_path=output_path,
                num_nodes=num_points,
                num_elements=num_facets,
                duration_s=round(elapsed, 3),
            )

            return {
                "mesh_file": output_path,
                "num_nodes": num_points,
                "num_elements": num_facets,
                "element_types": ["triangle"],
                "quality_metrics": {
                    "element_size": element_size,
                    "algorithm": algorithm,
                },
            }

    # ------------------------------------------------------------------
    # File-based ops (retire the adapter's NotImplementedError stubs)
    # ------------------------------------------------------------------

    def boolean_operation(
        self,
        input_file_a: str,
        input_file_b: str,
        operation: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Fuse / cut / common two CAD files and export the result to STEP."""
        self._require_freecad()
        with tracer.start_as_current_span("freecad.boolean") as span:
            span.set_attribute("boolean.operation", operation)
            if not output_path:
                stem = Path(input_file_a).stem
                output_path = os.path.join(self.work_dir, f"{stem}_{operation}.step")
            self._ensure_output_dir(output_path)

            shape_a = Part.Shape()
            shape_a.read(input_file_a)
            shape_b = Part.Shape()
            shape_b.read(input_file_b)

            if operation == "union":
                result = shape_a.fuse(shape_b)
            elif operation == "subtract":
                result = shape_a.cut(shape_b)
            elif operation == "intersect":
                result = shape_a.common(shape_b)
            else:
                raise ValueError(f"Unsupported boolean operation: {operation}")

            result.exportStep(output_path)
            return {
                "output_file": output_path,
                "operation": operation,
                "result_volume": round(result.Volume, 2),
                "result_area": round(result.Area, 2),
            }

    def get_properties(
        self, input_file: str, properties: list[str] | None = None
    ) -> dict[str, Any]:
        """Extract geometric properties (volume/area/center_of_mass/bounding_box)."""
        self._require_freecad()
        wanted = properties or ["volume", "area", "center_of_mass", "bounding_box"]
        with tracer.start_as_current_span("freecad.properties"):
            shape = Part.Shape()
            shape.read(input_file)
            props: dict[str, Any] = {}
            if "volume" in wanted:
                props["volume"] = round(shape.Volume, 2)
            if "area" in wanted:
                props["area"] = round(shape.Area, 2)
            if "center_of_mass" in wanted:
                com = shape.CenterOfMass
                props["center_of_mass"] = [round(com.x, 3), round(com.y, 3), round(com.z, 3)]
            if "bounding_box" in wanted:
                props["bounding_box"] = self._bbox_dict(shape.BoundBox)
            return {"file": input_file, "properties": props}

    # ------------------------------------------------------------------
    # Stateful authoring ops (MET-528) — operate on a live session document.
    # Each builds and returns a live FreeCAD object; the adapter registers it
    # in the session store and hands back a stable obj_id.
    # ------------------------------------------------------------------

    def create_primitive(self, document: Any, kind: str, params: dict[str, Any]) -> Any:
        """Add a parametric Part primitive (box/cylinder/sphere/cone/torus)."""
        self._require_freecad()
        defaults = _SHAPE_DEFAULTS.get(kind, {})
        merged = {**defaults, **params}
        if kind == "box":
            obj = document.addObject("Part::Box", "Box")
            obj.Length, obj.Width, obj.Height = (
                merged["length"],
                merged["width"],
                merged["height"],
            )
        elif kind == "cylinder":
            obj = document.addObject("Part::Cylinder", "Cylinder")
            obj.Radius, obj.Height = merged["radius"], merged["height"]
        elif kind == "sphere":
            obj = document.addObject("Part::Sphere", "Sphere")
            obj.Radius = merged["radius"]
        elif kind == "cone":
            obj = document.addObject("Part::Cone", "Cone")
            obj.Radius1, obj.Radius2, obj.Height = (
                merged["radius1"],
                merged["radius2"],
                merged["height"],
            )
        elif kind == "torus":
            obj = document.addObject("Part::Torus", "Torus")
            obj.Radius1 = params.get("radius1", 10.0)
            obj.Radius2 = params.get("radius2", 2.0)
        else:
            raise ValueError(f"Unsupported primitive: {kind}")
        document.recompute()
        return obj

    def create_body(self, document: Any, name: str = "Body") -> Any:
        """Create a PartDesign Body for parametric (sketch-based) modelling."""
        self._require_freecad()
        body = document.addObject("PartDesign::Body", name or "Body")
        document.recompute()
        return body

    def create_sketch(
        self,
        document: Any,
        body: Any,
        plane: str = "XY",
        elements: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Create a Sketch on a body's origin plane and add 2D geometry.

        Supported element ``type`` values: ``rectangle`` (x,y,width,height),
        ``circle`` (cx,cy,r), ``line`` (x1,y1,x2,y2).
        """
        self._require_freecad()
        sketch = body.newObject("Sketcher::SketchObject", "Sketch")
        plane_map = {"XY": "XY_Plane", "XZ": "XZ_Plane", "YZ": "YZ_Plane"}
        plane_name = plane_map.get(plane.upper(), "XY_Plane")
        origin_plane = next(
            (f for f in body.Origin.OriginFeatures if f.Name.startswith(plane_name[:2])), None
        )
        if origin_plane is not None:
            sketch.AttachmentSupport = [(origin_plane, "")]
            sketch.MapMode = "FlatFace"
        for el in elements or []:
            self._add_sketch_element(sketch, el)
        document.recompute()
        return sketch

    def _add_sketch_element(self, sketch: Any, el: dict[str, Any]) -> None:
        import FreeCAD as FC  # type: ignore[import-untyped]

        kind = el.get("type")
        if kind == "rectangle":
            x, y = float(el.get("x", 0.0)), float(el.get("y", 0.0))
            w, h = float(el["width"]), float(el["height"])
            pts = [
                (x, y),
                (x + w, y),
                (x + w, y + h),
                (x, y + h),
            ]
            for i in range(4):
                a = FC.Vector(*pts[i], 0)
                b = FC.Vector(*pts[(i + 1) % 4], 0)
                sketch.addGeometry(Part.LineSegment(a, b), False)
        elif kind == "circle":
            cx, cy, r = float(el["cx"]), float(el["cy"]), float(el["r"])
            sketch.addGeometry(Part.Circle(FC.Vector(cx, cy, 0), FC.Vector(0, 0, 1), r), False)
        elif kind == "line":
            a = FC.Vector(float(el["x1"]), float(el["y1"]), 0)
            b = FC.Vector(float(el["x2"]), float(el["y2"]), 0)
            sketch.addGeometry(Part.LineSegment(a, b), False)
        else:
            raise ValueError(f"Unsupported sketch element: {kind!r}")

    def pad_sketch(
        self,
        document: Any,
        body: Any,
        sketch: Any,
        length: float,
        *,
        reversed: bool = False,
        midplane: bool = False,
    ) -> Any:
        """Extrude (pad) a sketch into a solid on its PartDesign body."""
        self._require_freecad()
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sketch
        pad.Length = float(length)
        pad.Reversed = bool(reversed)
        pad.Midplane = bool(midplane)
        sketch.Visibility = False
        document.recompute()
        return pad

    def shape_props(self, obj: Any) -> dict[str, Any]:
        """Volume / surface area / bounding box for a live object's shape."""
        self._require_freecad()
        shape = obj.Shape
        return {
            "volume_mm3": round(shape.Volume, 2),
            "surface_area_mm2": round(shape.Area, 2),
            "bounding_box": self._bbox_dict(shape.BoundBox),
        }

    def export_object_step_bytes(self, obj: Any) -> bytes:
        """Export a live object's shape to STEP and return the bytes.

        Bytes (not a container-local path) so the gateway/twin layer can persist
        them to MinIO regardless of where the adapter runs (MET-529).
        """
        self._require_freecad()
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            obj.Shape.exportStep(tmp_path)
            return Path(tmp_path).read_bytes()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _bbox_dict(bb: Any) -> dict[str, float]:
        return {
            "min_x": round(bb.XMin, 2),
            "min_y": round(bb.YMin, 2),
            "min_z": round(bb.ZMin, 2),
            "max_x": round(bb.XMax, 2),
            "max_y": round(bb.YMax, 2),
            "max_z": round(bb.ZMax, 2),
        }

    # ------------------------------------------------------------------
    # Assembly authoring (MET-530). The container is an ``App::Part`` (stable
    # across FreeCAD versions); the durable, solver-relevant joint output is
    # metadata recorded by the adapter. Building a real ``Assembly::Joint`` for
    # the on-Apply full solve is the FreeCAD-runtime follow-up.
    # ------------------------------------------------------------------

    def create_assembly(self, document: Any, name: str = "Assembly") -> Any:
        """Create an assembly container (an ``App::Part``) to group parts."""
        self._require_freecad()
        assembly = document.addObject("App::Part", name or "Assembly")
        document.recompute()
        return assembly

    def add_part_to_assembly(
        self, document: Any, assembly: Any, part: Any, placement: dict[str, Any] | None = None
    ) -> Any:
        """Add an existing part object to the assembly, optionally placing it."""
        self._require_freecad()
        if placement:
            import FreeCAD as FC  # type: ignore[import-untyped]

            pos = placement.get("position", [0.0, 0.0, 0.0])
            part.Placement = FC.Placement(
                FC.Vector(float(pos[0]), float(pos[1]), float(pos[2])),
                part.Placement.Rotation,
            )
        assembly.addObject(part)
        document.recompute()
        return part
