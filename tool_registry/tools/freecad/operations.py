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

# Importing the Sketcher + PartDesign workbench modules registers their object
# types (``Sketcher::SketchObject``, ``PartDesign::Body`` / ``Pad`` / ``Pocket``
# / ``Revolution``) with the FreeCAD application, so ``addObject``/``newObject``
# for them work. ``freecadcmd`` auto-loads these; a plain ``python3`` process
# does not, so we load them explicitly here. Kept separate from HAS_FREECAD —
# the Part-based primitive/boolean/export ops work even if these are absent.
if HAS_FREECAD:
    try:
        import PartDesign  # type: ignore[import-untyped]  # noqa: F401
        import Sketcher  # type: ignore[import-untyped]  # noqa: F401

        HAS_PARTDESIGN = True
    except ImportError:
        HAS_PARTDESIGN = False
else:
    HAS_PARTDESIGN = False


class ScriptSandboxError(RuntimeError):
    """Raised when an execute_code script violates sandbox restrictions."""


class ScriptTimeoutError(RuntimeError):
    """Raised when an execute_code script exceeds the allowed execution time."""


# Sandbox policy for execute_code — mirrors the reviewed cadquery.execute_script
# model. NOTE: this is best-effort source-level guarding; the real isolation
# boundary is the container (non-root, resource-limited, no network needed).
# FreeCAD's own API can still touch the filesystem, so execute_code is a power
# tool gated by that container boundary, not a hard sandbox.
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
_BLOCKED_NAMES = {"__import__", "eval", "exec", "compile", "open", "os", "sys", "subprocess"}
_SANDBOX_MODULES = {"FreeCAD", "App", "Part", "math"}  # injected into the namespace
import re as _re  # noqa: E402

_IMPORT_RE = _re.compile(
    r"^(?:import\s+(?P<mod>\w+)(?:\s+as\s+\w+)?|from\s+(?P<from_mod>\w+)\s+import\s+.+)$"
)


def _strip_sandbox_imports(script: str) -> str:
    """Drop top-level import lines for modules already injected (FreeCAD/App/Part)."""
    out = []
    for line in script.splitlines():
        m = _IMPORT_RE.match(line.strip())
        if m and (m.group("mod") or m.group("from_mod")) in _SANDBOX_MODULES:
            continue
        out.append(line)
    return "\n".join(out)


class FreecadNotAvailableError(RuntimeError):
    """Raised when FreeCAD Python bindings (or a required workbench) are unavailable."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "FreeCAD Python bindings are not installed. "
                "Run inside the FreeCAD Docker container or install FreeCAD with Python support."
            )
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

    def _require_partdesign(self) -> None:
        """Raise if the PartDesign/Sketcher workbenches aren't loaded."""
        self._require_freecad()
        if not HAS_PARTDESIGN:
            raise FreecadNotAvailableError(
                "FreeCAD PartDesign/Sketcher workbenches are not importable. "
                "Ensure the FreeCAD Mod dirs (/usr/lib/freecad/Mod, "
                "/usr/share/freecad/Mod) are on PYTHONPATH."
            )

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
        self._require_partdesign()
        body = document.addObject("PartDesign::Body", name or "Body")
        document.recompute()
        return body

    def create_sketch(
        self,
        document: Any,
        body: Any,
        plane: str = "XY",
        elements: list[dict[str, Any]] | None = None,
        offset: float = 0.0,
    ) -> Any:
        """Create a Sketch on a body's origin plane and add 2D geometry.

        Supported element ``type`` values: ``rectangle`` (x,y,width,height),
        ``circle`` (cx,cy,r), ``line`` (x1,y1,x2,y2). ``offset`` shifts the sketch
        along the plane normal (e.g. a second loft profile at z=20 on XY).
        """
        self._require_partdesign()
        sketch = body.newObject("Sketcher::SketchObject", "Sketch")
        plane_map = {"XY": "XY_Plane", "XZ": "XZ_Plane", "YZ": "YZ_Plane"}
        # Match by Role (not Name) so it works for the 2nd+ body in a document,
        # where FreeCAD suffixes the origin Names (XY_Plane001).
        origin_plane = self._origin_feature(body, plane_map.get(plane.upper(), "XY_Plane"))
        sketch.AttachmentSupport = [(origin_plane, "")]
        sketch.MapMode = "FlatFace"
        if offset:
            import FreeCAD as FC  # type: ignore[import-untyped]

            sketch.AttachmentOffset = FC.Placement(FC.Vector(0, 0, float(offset)), FC.Rotation())
        for el in elements or []:
            self._add_sketch_element(sketch, el)
        document.recompute()
        return sketch

    def loft_sketches(self, document: Any, body: Any, profile: Any, sections: list[Any]) -> Any:
        """Loft a profile sketch through one or more section sketches (additive)."""
        self._require_partdesign()
        loft = body.newObject("PartDesign::AdditiveLoft", "Loft")
        loft.Profile = profile
        loft.Sections = list(sections)
        for sk in [profile, *sections]:
            sk.Visibility = False
        document.recompute()
        return loft

    def sweep_sketch(self, document: Any, body: Any, profile: Any, path: Any) -> Any:
        """Sweep a profile sketch along a path sketch (additive pipe)."""
        self._require_partdesign()
        sweep = body.newObject("PartDesign::AdditivePipe", "Sweep")
        sweep.Profile = profile
        sweep.Spine = (path, [])
        profile.Visibility = False
        path.Visibility = False
        document.recompute()
        return sweep

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
        self._require_partdesign()
        pad = body.newObject("PartDesign::Pad", "Pad")
        pad.Profile = sketch
        pad.Length = float(length)
        pad.Reversed = bool(reversed)
        pad.Midplane = bool(midplane)
        sketch.Visibility = False
        document.recompute()
        return pad

    def pocket_sketch(
        self,
        document: Any,
        body: Any,
        sketch: Any,
        depth: float,
        *,
        reversed: bool = False,
    ) -> Any:
        """Cut a pocket from a sketch on its PartDesign body (the inverse of pad)."""
        self._require_partdesign()
        pocket = body.newObject("PartDesign::Pocket", "Pocket")
        pocket.Profile = sketch
        pocket.Length = float(depth)
        pocket.Reversed = bool(reversed)
        sketch.Visibility = False
        document.recompute()
        return pocket

    def revolve_sketch(
        self,
        document: Any,
        body: Any,
        sketch: Any,
        angle: float = 360.0,
        *,
        axis: str = "V",
        reversed: bool = False,
    ) -> Any:
        """Revolve a sketch around one of its axes (V_Axis / H_Axis)."""
        self._require_partdesign()
        rev = body.newObject("PartDesign::Revolution", "Revolution")
        rev.Profile = sketch
        rev.Angle = float(angle)
        rev.Reversed = bool(reversed)
        axis_ref = {"V": "V_Axis", "H": "H_Axis"}.get(axis.upper(), "V_Axis")
        try:
            rev.ReferenceAxis = (sketch, [axis_ref])
        except Exception as exc:  # noqa: BLE001 — axis ref is version-sensitive
            logger.warning("freecad_revolve_axis_ref_failed", error=str(exc))
        sketch.Visibility = False
        document.recompute()
        return rev

    def transform_object(
        self,
        document: Any,
        obj: Any,
        position: list[float] | None = None,
        rotation: dict[str, Any] | None = None,
    ) -> Any:
        """Move and/or rotate an object by setting its Placement.

        ``rotation`` is ``{"axis": [x, y, z], "angle_deg": <deg>}``.
        """
        self._require_freecad()
        import FreeCAD as FC  # type: ignore[import-untyped]

        current = obj.Placement
        base = current.Base
        pos = FC.Vector(*position) if position else FC.Vector(base.x, base.y, base.z)
        rot = current.Rotation
        if rotation:
            axis = rotation.get("axis", [0.0, 0.0, 1.0])
            rot = FC.Rotation(
                FC.Vector(float(axis[0]), float(axis[1]), float(axis[2])),
                float(rotation.get("angle_deg", 0.0)),
            )
        obj.Placement = FC.Placement(pos, rot)
        document.recompute()
        return obj

    # ------------------------------------------------------------------
    # Dress-up features (MET-527): fillet / chamfer / shell. They operate on a
    # PartDesign body's tip; edges/faces are caller-supplied selectors (e.g.
    # ["Edge1", "Edge3"]). Default = all edges (fillet/chamfer) so a simple call
    # rounds the whole solid; shell requires the face(s) to open.
    # ------------------------------------------------------------------

    def _tip(self, body: Any) -> Any:
        """The body's current tip feature (the solid dress-ups attach to)."""
        tip = getattr(body, "Tip", None)
        if tip is None:
            raise ValueError("body has no tip feature to dress up (pad a sketch first)")
        return tip

    def fillet_edges(
        self, document: Any, body: Any, radius: float, edges: list[str] | None = None
    ) -> Any:
        """Round edges of the body's tip. Defaults to all edges."""
        self._require_partdesign()
        tip = self._tip(body)
        names = edges or [f"Edge{i + 1}" for i in range(len(tip.Shape.Edges))]
        fillet = body.newObject("PartDesign::Fillet", "Fillet")
        fillet.Base = (tip, names)
        fillet.Radius = float(radius)
        document.recompute()
        return fillet

    def chamfer_edges(
        self, document: Any, body: Any, size: float, edges: list[str] | None = None
    ) -> Any:
        """Chamfer edges of the body's tip. Defaults to all edges."""
        self._require_partdesign()
        tip = self._tip(body)
        names = edges or [f"Edge{i + 1}" for i in range(len(tip.Shape.Edges))]
        chamfer = body.newObject("PartDesign::Chamfer", "Chamfer")
        chamfer.Base = (tip, names)
        chamfer.Size = float(size)
        document.recompute()
        return chamfer

    # ------------------------------------------------------------------
    # Patterns + mirror (MET-527): replicate a feature across a body. The
    # direction/axis/plane reference the body's origin features (stable across
    # versions). Validated headless against FreeCAD 1.0.0.
    # ------------------------------------------------------------------

    def _origin_feature(self, body: Any, role: str) -> Any:
        """Return a body origin feature by its stable ``Role`` (X_Axis / Z_Axis /
        YZ_Plane / ...). Matching on Role (not Name) is essential: in a multi-body
        document FreeCAD suffixes the Names (``X_Axis001``) while Role is stable."""
        feat = next(
            (f for f in body.Origin.OriginFeatures if getattr(f, "Role", None) == role), None
        )
        if feat is None:
            raise ValueError(f"origin feature with role {role!r} not found on body")
        return feat

    def linear_pattern(
        self,
        document: Any,
        body: Any,
        feature: Any,
        count: int,
        spacing: float,
        axis: str = "X",
    ) -> Any:
        """Replicate ``feature`` ``count`` times along an axis (X/Y/Z), ``spacing`` apart."""
        self._require_partdesign()
        axis_name = {"X": "X_Axis", "Y": "Y_Axis", "Z": "Z_Axis"}.get(axis.upper(), "X_Axis")
        pat = body.newObject("PartDesign::LinearPattern", "LinearPattern")
        pat.Originals = [feature]
        pat.Direction = (self._origin_feature(body, axis_name), [""])
        pat.Length = float(spacing) * max(int(count) - 1, 1)
        pat.Occurrences = int(count)
        document.recompute()
        return pat

    def polar_pattern(
        self,
        document: Any,
        body: Any,
        feature: Any,
        count: int,
        angle: float = 360.0,
        axis: str = "Z",
    ) -> Any:
        """Replicate ``feature`` ``count`` times around an axis (X/Y/Z) over ``angle``."""
        self._require_partdesign()
        axis_name = {"X": "X_Axis", "Y": "Y_Axis", "Z": "Z_Axis"}.get(axis.upper(), "Z_Axis")
        pat = body.newObject("PartDesign::PolarPattern", "PolarPattern")
        pat.Originals = [feature]
        pat.Axis = (self._origin_feature(body, axis_name), [""])
        pat.Angle = float(angle)
        pat.Occurrences = int(count)
        document.recompute()
        return pat

    def mirror_feature(self, document: Any, body: Any, feature: Any, plane: str = "YZ") -> Any:
        """Mirror ``feature`` across a body origin plane (XY/XZ/YZ)."""
        self._require_partdesign()
        plane_name = {"XY": "XY_Plane", "XZ": "XZ_Plane", "YZ": "YZ_Plane"}.get(
            plane.upper(), "YZ_Plane"
        )
        mir = body.newObject("PartDesign::Mirrored", "Mirrored")
        mir.Originals = [feature]
        mir.MirrorPlane = (self._origin_feature(body, plane_name), [""])
        document.recompute()
        return mir

    def execute_code(
        self,
        document: Any,
        code: str,
        *,
        max_lines: int = 200,
        timeout: float = 30.0,
    ) -> Any:
        """Run a sandboxed FreeCAD Python script against the session ``doc``.

        The namespace provides ``FreeCAD`` (alias ``App``), ``Part``, ``math`` and
        the active ``doc``. Assign the object to surface to a variable named
        ``result`` (it gets registered + returned). Source-level guarding mirrors
        cadquery.execute_script; the real isolation boundary is the container.
        """
        # Sandbox policy is validated first (no FreeCAD needed) so it's unit-testable.
        lines = code.strip().splitlines()
        if len(lines) > max_lines:
            raise ScriptSandboxError(f"Script exceeds {max_lines} lines (has {len(lines)})")
        for blocked in _BLOCKED_NAMES:
            if _re.search(r"\b" + _re.escape(blocked) + r"\b", code):
                raise ScriptSandboxError(f"Script contains blocked name: {blocked!r}")
        code = _strip_sandbox_imports(code)

        self._require_freecad()
        import builtins as _builtins_module
        import math
        import signal
        import threading

        safe_builtins = {
            k: getattr(_builtins_module, k) for k in _SAFE_BUILTINS if hasattr(_builtins_module, k)
        }
        namespace: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
            "Part": Part,
            "math": math,
            "doc": document,
        }

        is_main = threading.current_thread() is threading.main_thread()
        old_handler = None

        def _timeout(_signum: int, _frame: Any) -> None:
            raise ScriptTimeoutError(f"Script exceeded {timeout}s")

        with tracer.start_as_current_span("freecad.execute_code"):
            try:
                if is_main and hasattr(signal, "SIGALRM"):
                    old_handler = signal.signal(signal.SIGALRM, _timeout)
                    signal.alarm(int(timeout))
                exec(compile(code, "<freecad_script>", "exec"), namespace)  # noqa: S102
            except (ScriptTimeoutError, ScriptSandboxError):
                raise
            except Exception as exc:
                raise RuntimeError(f"Script execution failed: {exc}") from exc
            finally:
                if is_main and hasattr(signal, "SIGALRM"):
                    signal.alarm(0)
                    if old_handler is not None:
                        signal.signal(signal.SIGALRM, old_handler)

        document.recompute()
        return namespace.get("result")

    def shell_solid(
        self, document: Any, body: Any, thickness: float, faces: list[str] | None = None
    ) -> Any:
        """Hollow the body's tip to a wall thickness, opening ``faces`` (default:
        the topmost face by Z). Uses the Part-workbench ``makeThickness`` (which
        works headless, unlike PartDesign::Thickness — MET-533); the result is a
        ``Part::Feature`` and the source body is hidden."""
        self._require_partdesign()
        tip = self._tip(body)
        shape = tip.Shape
        shape_faces = shape.Faces
        if faces:
            idxs = [int(f.replace("Face", "")) - 1 for f in faces]
        else:
            idxs = [max(range(len(shape_faces)), key=lambda i: shape_faces[i].CenterOfMass.z)]
        remove = [shape_faces[i] for i in idxs if 0 <= i < len(shape_faces)]
        if not remove:
            raise ValueError("no valid faces to open for the shell")
        hollow = shape.makeThickness(remove, -abs(float(thickness)), 1e-3)
        feat = document.addObject("Part::Feature", "Shell")
        feat.Shape = hollow
        body.Visibility = False
        document.recompute()
        return feat

    # ------------------------------------------------------------------
    # Composite skills (MET-527/531): higher-level generators that compose the
    # primitive authoring ops into a finished part in one call.
    # ------------------------------------------------------------------

    def generate_enclosure(
        self,
        document: Any,
        length: float,
        width: float,
        height: float,
        wall_thickness: float = 2.0,
    ) -> Any:
        """Parametric electronics enclosure: a hollow box open at the top.

        Composes create_body → create_sketch → pad_sketch → shell_solid (the
        FreeCAD ``/enclosure`` skill; replaces the cadquery_generate_enclosure
        stub, MET-531). Returns the hollow ``Part::Feature``.
        """
        self._require_partdesign()
        body = self.create_body(document, "Enclosure")
        sketch = self.create_sketch(
            document,
            body,
            "XY",
            [{"type": "rectangle", "x": 0, "y": 0, "width": length, "height": width}],
        )
        self.pad_sketch(document, body, sketch, height)
        return self.shell_solid(document, body, wall_thickness)

    def fastener_hole(
        self,
        document: Any,
        body: Any,
        x: float,
        y: float,
        diameter: float,
        depth: float | None = None,
        counterbore_diameter: float = 0.0,
        counterbore_depth: float = 0.0,
    ) -> Any:
        """Drill a (optionally counterbored) fastener hole into a body's top face.

        The ``/fastener-hole`` skill: pockets a clearance hole at (x, y) from the
        top face down ``depth`` (default: through), plus an optional counterbore.
        Modifies and returns the ``body``.
        """
        self._require_partdesign()
        top_z = self._tip(body).Shape.BoundBox.ZMax
        through = top_z if depth is None else float(depth)
        clr = self.create_sketch(
            document,
            body,
            "XY",
            [{"type": "circle", "cx": x, "cy": y, "r": diameter / 2.0}],
            offset=top_z,
        )
        self.pocket_sketch(document, body, clr, through)
        if counterbore_diameter and counterbore_depth:
            cb = self.create_sketch(
                document,
                body,
                "XY",
                [{"type": "circle", "cx": x, "cy": y, "r": counterbore_diameter / 2.0}],
                offset=top_z,
            )
            self.pocket_sketch(document, body, cb, float(counterbore_depth))
        document.recompute()
        return body

    def thread_insert(
        self,
        document: Any,
        body: Any,
        x: float,
        y: float,
        boss_diameter: float,
        boss_height: float,
        hole_diameter: float,
        hole_depth: float,
    ) -> Any:
        """Add a screw boss for a heat-set thread insert at (x, y) on a body's top.

        The ``/thread-insert`` skill: pads a cylindrical boss up from the top face,
        then pockets a pilot hole down through it. Modifies and returns the body.
        """
        self._require_partdesign()
        top_z = self._tip(body).Shape.BoundBox.ZMax
        boss_sk = self.create_sketch(
            document,
            body,
            "XY",
            [{"type": "circle", "cx": x, "cy": y, "r": boss_diameter / 2.0}],
            offset=top_z,
        )
        self.pad_sketch(document, body, boss_sk, float(boss_height))
        boss_top = self._tip(body).Shape.BoundBox.ZMax
        hole_sk = self.create_sketch(
            document,
            body,
            "XY",
            [{"type": "circle", "cx": x, "cy": y, "r": hole_diameter / 2.0}],
            offset=boss_top,
        )
        self.pocket_sketch(document, body, hole_sk, float(hole_depth))
        document.recompute()
        return body

    def lattice_perforation(
        self,
        document: Any,
        body: Any,
        cell_size: float,
        hole_diameter: float,
        margin: float = 5.0,
    ) -> Any:
        """Lighten a body's top face with a square grid of through-holes.

        The ``/lattice`` skill (a grid-perforation lightening pattern — not a
        gyroid implicit surface, which isn't reliably headless). Pockets an N×M
        grid of circles through the body. Modifies and returns the body.
        """
        self._require_partdesign()
        bb = self._tip(body).Shape.BoundBox
        top_z = bb.ZMax
        elements: list[dict[str, Any]] = []
        x = bb.XMin + margin
        while x <= bb.XMax - margin:
            y = bb.YMin + margin
            while y <= bb.YMax - margin:
                elements.append({"type": "circle", "cx": x, "cy": y, "r": hole_diameter / 2.0})
                y += cell_size
            x += cell_size
        if not elements:
            raise ValueError("no lattice cells fit — reduce cell_size/margin")
        sketch = self.create_sketch(document, body, "XY", elements, offset=top_z)
        self.pocket_sketch(document, body, sketch, bb.ZLength)
        document.recompute()
        return body, len(elements)

    def generate_gear(
        self,
        document: Any,
        module: float,
        teeth: int,
        thickness: float,
        pressure_angle: float = 20.0,
    ) -> Any:
        """Generate a spur gear with a true involute tooth profile.

        The ``/gear`` skill — uses FreeCAD's bundled ``fcgear`` involute generator
        (``CreateExternalGear``), so the tooth profile is correct, not an
        approximation. Pitch diameter = module·teeth; addendum (outer) diameter =
        module·(teeth+2). Returns a ``Part::Feature``.
        """
        self._require_freecad()
        import math
        import sys

        # The gear generator ships with FreeCAD under Mod/PartDesign/fcgear.
        mod_dir = os.path.join(FreeCAD.getResourceDir(), "Mod", "PartDesign")
        if mod_dir not in sys.path:
            sys.path.insert(0, mod_dir)
        from fcgear import involute  # type: ignore[import-untyped]
        from fcgear.fcgear import FCWireBuilder  # type: ignore[import-untyped]

        builder = FCWireBuilder()
        involute.CreateExternalGear(
            builder, float(module), int(teeth), math.radians(pressure_angle), True
        )
        profile = Part.Wire([seg.toShape() for seg in builder.wire])
        solid = Part.Face(profile).extrude(FreeCAD.Vector(0, 0, float(thickness)))
        feat = document.addObject("Part::Feature", "Gear")
        feat.Shape = solid
        document.recompute()
        return feat

    # ------------------------------------------------------------------
    # Datasheet-driven component generation (MET-540): build a parametric
    # 3D model of an IC package from its datasheet dimensions. Body + leads
    # are emitted as separately-named parts so the digital thread, viewer,
    # and colouring all see one named product per feature.
    # ------------------------------------------------------------------

    # Number of lead-bearing sides per package family.
    _IC_SIDES = {
        "SOIC": 2,
        "SOP": 2,
        "SSOP": 2,
        "TSSOP": 2,
        "MSOP": 2,
        "DIP": 2,
        "SOT": 2,
        "QFP": 4,
        "LQFP": 4,
        "TQFP": 4,
        "QFN": 4,
        "MQFP": 4,
    }

    @staticmethod
    def ic_pin_layout(package_type: str, lead_count: int, pitch: float) -> list[dict[str, Any]]:
        """Pure pin layout: pin number, side, and centred position-along-side.

        FreeCAD-free so it is unit-testable. Sides are ``y-``/``y+`` (leads run
        out along ±Y, positioned along X) for 2-sided families, plus ``x+``/``x-``
        for 4-sided ones. Numbering is counter-clockwise from the ``y-`` side,
        the usual IC convention. ``u`` is the centred coordinate along the side.
        """
        sides = FreecadOperations._IC_SIDES.get(package_type.upper())
        if sides is None:
            raise ValueError(f"Unsupported package family: {package_type}")
        if lead_count <= 0 or lead_count % sides != 0:
            raise ValueError(
                f"lead_count must be a positive multiple of {sides} for {package_type}"
            )
        per = lead_count // sides
        coords = [(i - (per - 1) / 2.0) * pitch for i in range(per)]
        pins: list[dict[str, Any]] = []
        if sides == 2:
            for i, u in enumerate(coords):  # y- side, left→right
                pins.append({"pin": i + 1, "side": "y-", "u": u})
            for i, u in enumerate(reversed(coords)):  # y+ side, right→left
                pins.append({"pin": per + i + 1, "side": "y+", "u": u})
        else:  # 4-sided, CCW: y- (bottom), x+ (right), y+ (top), x- (left)
            for i, u in enumerate(coords):
                pins.append({"pin": i + 1, "side": "y-", "u": u})
            for i, u in enumerate(coords):
                pins.append({"pin": per + i + 1, "side": "x+", "u": u})
            for i, u in enumerate(reversed(coords)):
                pins.append({"pin": 2 * per + i + 1, "side": "y+", "u": u})
            for i, u in enumerate(reversed(coords)):
                pins.append({"pin": 3 * per + i + 1, "side": "x-", "u": u})
        return pins

    # Per-family default dimensions (mm) — used to fill any param the caller
    # (or datasheet extractor) didn't supply. Sensible JEDEC-ish nominals.
    _IC_DEFAULTS = {
        "SOIC": dict(
            body_length=4.9,
            body_width=3.9,
            body_height=1.4,
            lead_count=8,
            pitch=1.27,
            lead_span=6.0,
            lead_width=0.41,
            lead_thickness=0.2,
            standoff=0.1,
        ),
        "QFP": dict(
            body_length=7.0,
            body_width=7.0,
            body_height=1.0,
            lead_count=32,
            pitch=0.8,
            lead_span=9.0,
            lead_width=0.35,
            lead_thickness=0.15,
            standoff=0.05,
        ),
    }

    def generate_ic_package(
        self,
        document: Any,
        package_type: str,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> list[tuple[str, Any]]:
        """Build a parametric IC package (body + gull-wing leads) from dimensions.

        ``package_type`` selects the family (SOIC/SOP/…, QFP/LQFP/…). ``params``
        carries the datasheet dimensions (body_length/width/height, lead_count,
        pitch, lead_span, lead_width, lead_thickness, standoff); anything missing
        falls back to family nominals. Returns ``[(part_name, object), …]`` — the
        epoxy body, a pin-1 marker, and one solid per lead — for the adapter to
        register as individually-named parts.
        """
        self._require_freecad()
        fam = package_type.upper()
        base_key = "QFP" if self._IC_SIDES.get(fam) == 4 else "SOIC"
        cfg = {**self._IC_DEFAULTS[base_key], **(params or {})}
        v = FreeCAD.Vector
        name = name or f"{fam}-{int(cfg['lead_count'])}"

        L = float(cfg["body_length"])
        W = float(cfg["body_width"])
        H = float(cfg["body_height"])
        so = float(cfg["standoff"])
        lt = float(cfg["lead_thickness"])
        lw = float(cfg["lead_width"])
        lead_len = max(0.1, (float(cfg["lead_span"]) - W) / 2.0)

        out: list[tuple[str, Any]] = []

        def feat(label: str, shape: Any) -> None:
            o = document.addObject("Part::Feature", label)
            o.Shape = shape
            o.Label = label
            out.append((label, o))

        # Epoxy body
        feat(f"{name}_epoxy_body", Part.makeBox(L, W, H, v(-L / 2, -W / 2, so)))
        # Pin-1 orientation dimple on the top face, near pin 1's corner
        feat(
            f"{name}_pin1_mark",
            Part.makeCylinder(
                min(0.35, L / 8), 0.05, v(-L / 2 + 0.6, -W / 2 + 0.6, so + H), v(0, 0, 1)
            ),
        )

        def gull_lead(u: float, side: str) -> Any:
            """Foot on the seating plane + riser up to the body — fused."""
            if side in ("y-", "y+"):
                sgn = -1.0 if side == "y-" else 1.0
                y0 = min(sgn * W / 2, sgn * (W / 2 + lead_len))
                foot = Part.makeBox(lw, lead_len, lt, v(u - lw / 2, y0, 0.0))
                riser = Part.makeBox(
                    lw, lt, so + lt, v(u - lw / 2, sgn * (W / 2) - (lt if sgn > 0 else 0), 0.0)
                )
            else:
                sgn = -1.0 if side == "x-" else 1.0
                x0 = min(sgn * L / 2, sgn * (L / 2 + lead_len))
                foot = Part.makeBox(lead_len, lw, lt, v(x0, u - lw / 2, 0.0))
                riser = Part.makeBox(
                    lt, lw, so + lt, v(sgn * (L / 2) - (lt if sgn > 0 else 0), u - lw / 2, 0.0)
                )
            try:
                return foot.fuse(riser)
            except Exception:  # noqa: BLE001 — fall back to the flat foot
                return foot

        for p in self.ic_pin_layout(fam, int(cfg["lead_count"]), float(cfg["pitch"])):
            feat(f"{name}_pin_{p['pin']}", gull_lead(float(p["u"]), str(p["side"])))

        document.recompute()
        return out

    # ------------------------------------------------------------------
    # Profile-driven generation (MET-541): build a part from a datasheet's 2D
    # dimensioned outline — revolve it about an axis (shafts, spacers, sensor
    # cans, seals) or extrude it along a depth (constant-section parts). The
    # outline is the bridge from a mechanical drawing to a solid.
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_profile(
        points: list[dict[str, Any]], operation: str
    ) -> list[tuple[float, float]]:
        """Validate + close a 2D profile. Pure (FreeCAD-free), so it's testable.

        Points are ``{"x":…, "y":…}``. For ``revolve`` the profile lives in the
        (radius=x, height=y) half-plane and is spun about the Y/Z axis, so every
        x must be ≥ 0. The ring is auto-closed if the last point ≠ the first.
        """
        op = operation.lower()
        if op not in ("revolve", "extrude"):
            raise ValueError(f"operation must be 'revolve' or 'extrude', got {operation!r}")
        pts = [(float(p["x"]), float(p["y"])) for p in points]
        if len(pts) < 3:
            raise ValueError("profile needs at least 3 points")
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        if op == "revolve" and any(x < -1e-9 for x, _ in pts):
            raise ValueError("revolve profile radius (x) must be >= 0")
        return pts

    def generate_profile_part(
        self,
        document: Any,
        name: str,
        profile: list[dict[str, Any]],
        operation: str = "revolve",
        angle: float = 360.0,
        depth: float = 10.0,
    ) -> Any:
        """Build a solid from a 2D profile by revolving or extruding it.

        ``operation='revolve'``: profile is (radius, height) in the X-Z plane,
        spun ``angle`` degrees about the Z axis (default 360 → full body of
        revolution). ``operation='extrude'``: profile is (x, y), extruded
        ``depth`` along +Z. Returns a single named ``Part::Feature``.
        """
        self._require_freecad()
        pts = self.normalize_profile(profile, operation)
        v = FreeCAD.Vector
        if operation.lower() == "revolve":
            wire = Part.makePolygon([v(x, 0.0, y) for x, y in pts])
            solid = Part.Face(wire).revolve(v(0, 0, 0), v(0, 0, 1), float(angle))
        else:
            wire = Part.makePolygon([v(x, y, 0.0) for x, y in pts])
            solid = Part.Face(wire).extrude(v(0, 0, float(depth)))
        feat = document.addObject("Part::Feature", name or "ProfilePart")
        feat.Shape = solid
        feat.Label = name or "ProfilePart"
        document.recompute()
        return feat

    def shape_props(self, obj: Any) -> dict[str, Any]:
        """Volume / surface area / bounding box for a live object's shape."""
        self._require_freecad()
        shape = obj.Shape
        return {
            "volume_mm3": round(shape.Volume, 2),
            "surface_area_mm2": round(shape.Area, 2),
            "bounding_box": self._bbox_dict(shape.BoundBox),
        }

    # ------------------------------------------------------------------
    # Inspection (MET-527): read-only geometry queries so agents can reason
    # about what they've authored. Work on any object with a ``.Shape`` (no
    # PartDesign needed).
    # ------------------------------------------------------------------

    def measure(self, obj: Any) -> dict[str, Any]:
        """Full geometric measurement of an object's shape."""
        self._require_freecad()
        shape = obj.Shape
        com = shape.CenterOfMass
        return {
            "volume_mm3": round(shape.Volume, 2),
            "surface_area_mm2": round(shape.Area, 2),
            "bounding_box": self._bbox_dict(shape.BoundBox),
            "center_of_mass": [round(com.x, 3), round(com.y, 3), round(com.z, 3)],
            "vertex_count": len(shape.Vertexes),
            "edge_count": len(shape.Edges),
            "face_count": len(shape.Faces),
            "solid_count": len(shape.Solids),
        }

    def describe_model(self, obj: Any) -> dict[str, Any]:
        """Human-oriented geometry summary: dimensions, solid/hollow, counts."""
        self._require_freecad()
        shape = obj.Shape
        bb = shape.BoundBox
        solids = shape.Solids
        # A solid whose volume is well below its bounding box is likely hollow/thin.
        bbox_vol = bb.XLength * bb.YLength * bb.ZLength
        fill_ratio = (shape.Volume / bbox_vol) if bbox_vol > 1e-9 else 0.0
        return {
            "name": getattr(obj, "Label", getattr(obj, "Name", "object")),
            "dimensions_mm": {
                "x": round(bb.XLength, 2),
                "y": round(bb.YLength, 2),
                "z": round(bb.ZLength, 2),
            },
            "volume_mm3": round(shape.Volume, 2),
            "surface_area_mm2": round(shape.Area, 2),
            "solid_count": len(solids),
            "is_solid": len(solids) > 0 and bool(getattr(shape, "isClosed", lambda: True)()),
            "fill_ratio": round(fill_ratio, 3),
            "likely_hollow": bool(0.0 < fill_ratio < 0.5),
            "face_count": len(shape.Faces),
            "edge_count": len(shape.Edges),
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

    # ------------------------------------------------------------------
    # Parametric modelling (MET-531): a VarSet of named variables + property
    # expressions that bind model dimensions to them, so a drag/Apply can
    # re-parameterise and re-solve instead of just suggesting a number.
    # ------------------------------------------------------------------

    _PROP_TYPES: dict[str, str] = {
        "length": "App::PropertyLength",
        "float": "App::PropertyFloat",
        "int": "App::PropertyInteger",
        "angle": "App::PropertyAngle",
    }

    def create_variable_set(self, document: Any, name: str, variables: dict[str, Any]) -> Any:
        """Create an ``App::VarSet`` of named parametric variables.

        ``variables`` maps ``var_name -> {"value": <num>, "type": length|float|int|angle}``
        (a bare number is treated as a float).
        """
        self._require_freecad()
        varset = document.addObject("App::VarSet", name or "Params")
        for var_name, spec in variables.items():
            if isinstance(spec, dict):
                value = spec.get("value", 0.0)
                vtype = str(spec.get("type", "float")).lower()
            else:
                value, vtype = spec, "float"
            prop_type = self._PROP_TYPES.get(vtype, "App::PropertyFloat")
            varset.addProperty(prop_type, var_name, "Variables", f"{var_name} parameter")
            setattr(varset, var_name, value)
        document.recompute()
        return varset

    def set_expression(self, document: Any, obj: Any, property_path: str, expression: str) -> None:
        """Bind an object property to a FreeCAD expression (parametric link)."""
        self._require_freecad()
        obj.setExpression(property_path, expression)
        document.recompute()
