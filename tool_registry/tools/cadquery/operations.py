"""CadQuery operations -- conditional CadQuery Python API usage.

Provides the core CAD operations (parametric creation, boolean ops, export,
script execution, assembly) that the CadQuery MCP adapter exposes. CadQuery
imports are conditional so the module can be imported and tested without a
real CadQuery installation.
"""

from __future__ import annotations

import base64
import math
import os
import re
import signal
import threading
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import structlog

from observability.tracing import get_tracer
from tool_registry.tools.cadquery.materials import resolve_density_kg_m3
from tool_registry.tools.cadquery.ros_launch import build_ros2_launch_py
from tool_registry.tools.cadquery.usd_export import build_usda, parse_stl_mesh

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cadquery.operations")

# URDF/mass-property unit conversion -- CadQuery/OCCT report volume in mm^3
# and inertia in mm^5 (both implicitly unit-density); URDF wants SI (m, kg,
# kg*m^2). See export_urdf()'s docstring for the derivation.
_MM_TO_M = 1e-3
_MM3_TO_M3 = 1e-9  # (1e-3)^3
_MM5_TO_M5 = 1e-15  # (1e-3)^5

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
    # Exception types: pure classes, no capability to catch/raise them --
    # unlike open/eval/exec/__import__ these were never a security boundary,
    # just an oversight. A script wrapping its own logic in try/except (a
    # completely normal Python pattern) crashed with "name 'Exception' is
    # not defined" without these (found live during the MET-642 S3 eval,
    # in the sibling FreeCAD adapter -- ported here for consistency).
    "Exception",
    "BaseException",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "RuntimeError",
    "StopIteration",
    "ZeroDivisionError",
    "ArithmeticError",
    "NotImplementedError",
}

# Blocked names in script source
_BLOCKED_NAMES = {"__import__", "eval", "exec", "compile", "open", "os", "sys", "subprocess"}

# Modules already provided in the sandbox namespace.  Import lines for
# these are stripped before exec() so scripts work despite __import__
# being excluded from safe builtins.
_SANDBOX_MODULES = {"cadquery", "cq", "math"}

# MET-649: `_strip_sandbox_imports` drops `from math import sin, cos, ...`
# entirely (math is a sandbox module, so the whole line matches and is
# removed) but nothing rebinds `sin`/`cos` as bare names afterward, so a
# script that wrote the from-import form got a NameError on first use.
_MATH_CONVENIENCE_NAMES = ("sin", "cos", "tan", "atan2", "sqrt", "pi", "radians", "degrees")

_IMPORT_RE = re.compile(
    r"^(?:import\s+(?P<mod>\w+)(?:\s+as\s+\w+)?|from\s+(?P<from_mod>\w+)\s+import\s+.+)$",
)


def _strip_sandbox_imports(script: str) -> str:
    """Remove import lines for modules already injected into the sandbox.

    LLM-generated and deterministic fallback scripts typically begin with
    ``import cadquery as cq`` or ``import math``.  Since the sandbox namespace
    already contains these modules and ``__import__`` is intentionally
    excluded from the safe builtins, we strip those lines so they don't
    cause a ``NameError`` at exec() time.

    Only top-level import lines whose root module is in ``_SANDBOX_MODULES``
    are removed.  Unknown imports are left in place so they correctly fail
    against the sandbox policy.

    Best-effort only -- ``_IMPORT_RE`` requires a bare module name, so a
    dotted submodule import or a comma-separated import list passes through
    untouched. ``_sandboxed_import`` below is the real enforcement point
    (mirrors the identical fix in ``tool_registry/tools/freecad/operations.py``
    MET-645 follow-up).
    """
    out_lines: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        m = _IMPORT_RE.match(stripped)
        if m:
            mod = m.group("mod") or m.group("from_mod")
            if mod in _SANDBOX_MODULES:
                continue  # drop this import line
        out_lines.append(line)
    return "\n".join(out_lines)


def _sandboxed_import(
    name: str,
    globals: dict[str, Any] | None = None,  # noqa: A002
    locals: dict[str, Any] | None = None,  # noqa: A002
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    """Restricted ``__import__`` for execute_script's namespace (MET-645 follow-up).

    Only the already-injected sandbox modules (cadquery/cq/math) may be
    imported -- via any syntax (dotted submodules, from-import, aliasing) --
    since ``_strip_sandbox_imports`` only catches the plain ``import X`` /
    ``from X import Y`` forms textually. Resolves to the SAME objects already
    bound in the namespace rather than performing a real import, so it grants
    no capability beyond what's already pre-bound. Anything else raises
    ImportError, same as a genuinely missing module.
    """
    top_level = name.split(".", 1)[0]
    if top_level not in _SANDBOX_MODULES:
        raise ImportError(f"import of {name!r} is not permitted in this sandbox")
    resolved = {"cadquery": cq, "cq": cq, "math": math}[top_level]
    return resolved


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


def _build_single_link_urdf(
    *,
    link_name: str,
    mesh_uri: str,
    mass_kg: float,
    com_m: tuple[float, float, float],
    inertia_kgm2: tuple[float, float, float, float, float, float],
) -> str:
    """Build a single-link URDF document (visual + collision + inertial).

    Built with ``xml.etree.ElementTree`` rather than string formatting so
    ``link_name``/``mesh_uri`` (caller-controlled strings) are XML-escaped
    correctly rather than risking malformed or injected markup.

    Visual and collision share the same mesh -- acceptable for a single
    authored part; a real robot pipeline would want a simplified collision
    hull, which is out of scope for this tier-1 cut (no joints either, see
    ``CadqueryOperations.export_urdf``'s docstring).
    """
    ixx, ixy, ixz, iyy, iyz, izz = inertia_kgm2
    robot = ET.Element("robot", name=f"{link_name}_robot")
    link = ET.SubElement(robot, "link", name=link_name)

    for tag in ("visual", "collision"):
        section = ET.SubElement(link, tag)
        geometry = ET.SubElement(section, "geometry")
        ET.SubElement(geometry, "mesh", filename=mesh_uri)

    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(
        inertial,
        "origin",
        xyz=f"{com_m[0]:.9g} {com_m[1]:.9g} {com_m[2]:.9g}",
        rpy="0 0 0",
    )
    ET.SubElement(inertial, "mass", value=f"{mass_kg:.9g}")
    ET.SubElement(
        inertial,
        "inertia",
        ixx=f"{ixx:.9g}",
        ixy=f"{ixy:.9g}",
        ixz=f"{ixz:.9g}",
        iyy=f"{iyy:.9g}",
        iyz=f"{iyz:.9g}",
        izz=f"{izz:.9g}",
    )

    ET.indent(robot, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(robot, encoding="unicode")


# Tier 2a (MET-706 session follow-on): FreeCAD's assembly-joint types
# (fixed/revolute/slider/cylindrical/ball -- tool_registry/tools/freecad/
# adapter.py's _VALID_JOINT_TYPES) don't map 1:1 onto URDF's joint types.
# `fixed` and `slider` map directly (`slider` -> `prismatic`). `revolute`
# maps to URDF's `continuous` (unlimited rotation), NOT `revolute` --
# MetaForge's joint metadata (base/follower/axis/anchor) never captures a
# rotation limit, and `revolute` is only valid in URDF *with* a `<limit>`;
# inventing one would be the same class of mistake avoided for USD's
# inertia (silently-plausible-but-wrong data). `continuous` needs no
# `<limit>` at all, so it is the honest mapping given what's actually known.
# `cylindrical`/`ball` have no single-joint URDF equivalent (URDF joints
# are single-DOF except `floating`/`planar`, neither of which is a
# faithful match) -- decomposing them into a chained multi-joint,
# zero-mass-link structure is real, separate work, not attempted here.
_URDF_JOINT_TYPE_MAP = {"fixed": "fixed", "slider": "prismatic", "revolute": "continuous"}
_URDF_UNSUPPORTED_JOINT_TYPES = {"cylindrical", "ball"}


class UnsupportedJointTypeError(ValueError):
    """Raised for a FreeCAD joint type with no single-joint URDF/SDF
    equivalent (cylindrical, ball) -- see the module-level joint-mapping
    comment above ``_URDF_JOINT_TYPE_MAP``."""


class MissingJointLimitsError(ValueError):
    """Raised when a ``prismatic`` joint has no caller-supplied ``limits``
    -- URDF requires a ``<limit>`` element for prismatic joints, and
    MetaForge's joint metadata never captures one, so fabricating a value
    would silently claim data that doesn't exist."""


def _build_assembly_urdf(
    *,
    robot_name: str,
    links: list[dict[str, Any]],
    joints: list[dict[str, Any]],
) -> str:
    """Build a multi-link URDF document with real joints.

    ``links``: each ``{name, mesh_uri, mass_kg, com_m, inertia_kgm2}`` --
    same per-link shape ``_build_single_link_urdf`` uses, just N of them.
    ``joints``: each FreeCAD joint record's shape directly
    (``{name, type, base, follower, axis, anchor}``, optionally
    ``limits: {lower, upper, effort, velocity}`` for a ``slider`` joint --
    see ``_URDF_JOINT_TYPE_MAP``'s reasoning above for why ``revolute``
    doesn't need one).
    """
    robot = ET.Element("robot", name=robot_name)

    for link in links:
        link_el = ET.SubElement(robot, "link", name=link["name"])
        for tag in ("visual", "collision"):
            section = ET.SubElement(link_el, tag)
            geometry = ET.SubElement(section, "geometry")
            ET.SubElement(geometry, "mesh", filename=link["mesh_uri"])

        ixx, ixy, ixz, iyy, iyz, izz = link["inertia_kgm2"]
        com_m = link["com_m"]
        inertial = ET.SubElement(link_el, "inertial")
        ET.SubElement(
            inertial,
            "origin",
            xyz=f"{com_m[0]:.9g} {com_m[1]:.9g} {com_m[2]:.9g}",
            rpy="0 0 0",
        )
        ET.SubElement(inertial, "mass", value=f"{link['mass_kg']:.9g}")
        ET.SubElement(
            inertial,
            "inertia",
            ixx=f"{ixx:.9g}",
            ixy=f"{ixy:.9g}",
            ixz=f"{ixz:.9g}",
            iyy=f"{iyy:.9g}",
            iyz=f"{iyz:.9g}",
            izz=f"{izz:.9g}",
        )

    for joint in joints:
        fc_type = joint["type"].lower()
        if fc_type in _URDF_UNSUPPORTED_JOINT_TYPES:
            raise UnsupportedJointTypeError(
                f"joint {joint.get('name', '?')!r} has type {fc_type!r}, which has no "
                "single-joint URDF equivalent (needs multi-joint decomposition, not "
                "yet implemented -- see _URDF_JOINT_TYPE_MAP's module comment)"
            )
        urdf_type = _URDF_JOINT_TYPE_MAP[fc_type]

        joint_el = ET.SubElement(
            robot,
            "joint",
            name=joint.get("name", f"{joint['base']}_to_{joint['follower']}"),
            type=urdf_type,
        )
        ET.SubElement(joint_el, "parent", link=joint["base"])
        ET.SubElement(joint_el, "child", link=joint["follower"])
        anchor = joint.get("anchor") or (0.0, 0.0, 0.0)
        ET.SubElement(
            joint_el,
            "origin",
            xyz=f"{anchor[0] * _MM_TO_M:.9g} {anchor[1] * _MM_TO_M:.9g} {anchor[2] * _MM_TO_M:.9g}",
            rpy="0 0 0",
        )
        if urdf_type in ("continuous", "prismatic"):
            axis = joint.get("axis") or (0.0, 0.0, 1.0)
            ET.SubElement(joint_el, "axis", xyz=f"{axis[0]:.9g} {axis[1]:.9g} {axis[2]:.9g}")
        if urdf_type == "prismatic":
            limits = joint.get("limits")
            if not limits:
                raise MissingJointLimitsError(
                    f"joint {joint.get('name', '?')!r} is a prismatic (slider) joint, which "
                    "URDF requires a <limit> element for, but no 'limits' "
                    "({'lower','upper','effort','velocity'}) was supplied for it -- "
                    "MetaForge's joint metadata never captures one, so it must be passed "
                    "explicitly rather than fabricated"
                )
            ET.SubElement(
                joint_el,
                "limit",
                lower=f"{limits['lower']:.9g}",
                upper=f"{limits['upper']:.9g}",
                effort=f"{limits.get('effort', 100.0):.9g}",
                velocity=f"{limits.get('velocity', 1.0):.9g}",
            )

    ET.indent(robot, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(robot, encoding="unicode")


def _build_single_link_sdf(
    *,
    model_name: str,
    link_name: str,
    mesh_uri: str,
    mass_kg: float,
    com_m: tuple[float, float, float],
    inertia_kgm2: tuple[float, float, float, float, float, float],
    static: bool,
    world_name: str,
) -> str:
    """Build a single-link, single-model SDFormat document.

    Schema per gazebosim/sdformat's ``sdf/1.11/{model,link,inertial,
    collision,visual,mesh_shape}.sdf`` (fetched directly from the primary
    source while building this, not guessed): ``<sdf version><model name>``
    contains ``<link name>``, which contains ``<inertial>`` (``<mass value>``,
    ``<pose>`` for the center-of-mass frame relative to the link frame,
    ``<inertia><ixx>...<izz></inertia>``) plus ``<collision name>``/
    ``<visual name>`` (each wrapping ``<geometry><mesh><uri></uri></mesh>
    </geometry>``). SDF poses are ``"x y z roll pitch yaw"`` in meters/
    radians -- already SI, same convention the mass properties are computed
    in, unlike URDF's separate xyz+rpy attributes but the same numbers.
    """
    ixx, ixy, ixz, iyy, iyz, izz = inertia_kgm2
    sdf = ET.Element("sdf", version="1.11")
    parent = sdf
    if world_name:
        parent = ET.SubElement(sdf, "world", name=world_name)

    model = ET.SubElement(parent, "model", name=model_name)
    ET.SubElement(model, "static").text = "true" if static else "false"
    link = ET.SubElement(model, "link", name=link_name)

    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(inertial, "mass").text = f"{mass_kg:.9g}"
    ET.SubElement(inertial, "pose").text = f"{com_m[0]:.9g} {com_m[1]:.9g} {com_m[2]:.9g} 0 0 0"
    inertia = ET.SubElement(inertial, "inertia")
    for tag, val in (
        ("ixx", ixx),
        ("ixy", ixy),
        ("ixz", ixz),
        ("iyy", iyy),
        ("iyz", iyz),
        ("izz", izz),
    ):
        ET.SubElement(inertia, tag).text = f"{val:.9g}"

    # Visual and collision share the same mesh -- same tier-1 tradeoff as
    # _build_single_link_urdf (a real pipeline would want a simplified
    # collision hull).
    for tag in ("collision", "visual"):
        section = ET.SubElement(link, tag, name=f"{link_name}_{tag}")
        geometry = ET.SubElement(section, "geometry")
        mesh = ET.SubElement(geometry, "mesh")
        ET.SubElement(mesh, "uri").text = mesh_uri

    ET.indent(sdf, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(sdf, encoding="unicode")


# SDF's own <joint> schema (gazebosim/sdformat's sdf/1.11/joint.sdf, fetched
# from the primary source) is materially more complete than URDF's: it has a
# native `ball` type (single element, no axis needed -- a true 1:1 match for
# FreeCAD's `ball` joint, unlike URDF which has no single-joint ball
# equivalent) and a `continuous` type distinct from `revolute` whose own
# <limit> is honestly optional ("Omit if joint is continuous" per the spec
# text, despite the schema's required=1 on the <limit> element itself --
# a known sdformat quirk where required= governs XML-schema generation, not
# runtime validity). So the only type still rejected is `cylindrical`: SDF
# has no direct 2-DOF (1 translation + 1 rotation, same axis) joint element
# -- `screw` looks similar but couples the two motions via a fixed pitch,
# which isn't the same kinematics, so it would misrepresent the joint rather
# than approximate it.
_SDF_JOINT_TYPE_MAP = {
    "fixed": "fixed",
    "slider": "prismatic",
    "revolute": "continuous",
    "ball": "ball",
}
_SDF_UNSUPPORTED_JOINT_TYPES = {"cylindrical"}


def _build_assembly_sdf(
    *,
    model_name: str,
    links: list[dict[str, Any]],
    joints: list[dict[str, Any]],
    static: bool,
    world_name: str,
) -> str:
    """Build a multi-link, single-model SDFormat document with real joints.

    ``links``/``joints`` have the same shapes ``_build_assembly_urdf`` takes
    -- see its docstring and ``_SDF_JOINT_TYPE_MAP``'s comment above for the
    joint-type mapping (SDF's is more permissive than URDF's: it also
    supports `ball` natively).
    """
    sdf = ET.Element("sdf", version="1.11")
    parent = sdf
    if world_name:
        parent = ET.SubElement(sdf, "world", name=world_name)

    model = ET.SubElement(parent, "model", name=model_name)
    ET.SubElement(model, "static").text = "true" if static else "false"

    for link in links:
        link_el = ET.SubElement(model, "link", name=link["name"])
        ixx, ixy, ixz, iyy, iyz, izz = link["inertia_kgm2"]
        com_m = link["com_m"]

        inertial = ET.SubElement(link_el, "inertial")
        ET.SubElement(inertial, "mass").text = f"{link['mass_kg']:.9g}"
        ET.SubElement(inertial, "pose").text = f"{com_m[0]:.9g} {com_m[1]:.9g} {com_m[2]:.9g} 0 0 0"
        inertia = ET.SubElement(inertial, "inertia")
        for tag, val in (
            ("ixx", ixx),
            ("ixy", ixy),
            ("ixz", ixz),
            ("iyy", iyy),
            ("iyz", iyz),
            ("izz", izz),
        ):
            ET.SubElement(inertia, tag).text = f"{val:.9g}"

        for tag in ("collision", "visual"):
            section = ET.SubElement(link_el, tag, name=f"{link['name']}_{tag}")
            geometry = ET.SubElement(section, "geometry")
            mesh = ET.SubElement(geometry, "mesh")
            ET.SubElement(mesh, "uri").text = link["mesh_uri"]

    for joint in joints:
        fc_type = joint["type"].lower()
        if fc_type in _SDF_UNSUPPORTED_JOINT_TYPES:
            raise UnsupportedJointTypeError(
                f"joint {joint.get('name', '?')!r} has type {fc_type!r}, which has no "
                "direct SDF <joint> equivalent (see _SDF_JOINT_TYPE_MAP's module comment)"
            )
        sdf_type = _SDF_JOINT_TYPE_MAP[fc_type]

        joint_el = ET.SubElement(
            model,
            "joint",
            name=joint.get("name", f"{joint['base']}_to_{joint['follower']}"),
            type=sdf_type,
        )
        ET.SubElement(joint_el, "parent").text = joint["base"]
        ET.SubElement(joint_el, "child").text = joint["follower"]

        if sdf_type in ("continuous", "prismatic"):
            axis = joint.get("axis") or (0.0, 0.0, 1.0)
            axis_el = ET.SubElement(joint_el, "axis")
            ET.SubElement(axis_el, "xyz").text = f"{axis[0]:.9g} {axis[1]:.9g} {axis[2]:.9g}"
            if sdf_type == "prismatic":
                limits = joint.get("limits")
                if not limits:
                    raise MissingJointLimitsError(
                        f"joint {joint.get('name', '?')!r} is a prismatic (slider) joint -- "
                        "no 'limits' ({'lower','upper','effort','velocity'}) was supplied for "
                        "it, and MetaForge's joint metadata never captures one, so it must be "
                        "passed explicitly rather than fabricated"
                    )
                limit_el = ET.SubElement(axis_el, "limit")
                ET.SubElement(limit_el, "lower").text = f"{limits['lower']:.9g}"
                ET.SubElement(limit_el, "upper").text = f"{limits['upper']:.9g}"
                if "effort" in limits:
                    ET.SubElement(limit_el, "effort").text = f"{limits['effort']:.9g}"
                if "velocity" in limits:
                    ET.SubElement(limit_el, "velocity").text = f"{limits['velocity']:.9g}"

    ET.indent(sdf, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(sdf, encoding="unicode")


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
            return cq.Workplane("XY").box(params["length"], params["width"], params["height"])
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
            return cq.Workplane("XY").box(params["length"], params["width"], params["thickness"])
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
        bracket = bracket.faces(">Z").workplane().center(length * 0.25, 0).hole(hole_radius * 2)
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

    def _compute_mass_properties(
        self, shape: Any, material: str, density_kg_m3: float | None
    ) -> dict[str, Any]:
        """Shared by ``export_urdf``/``export_sdf``: real mass properties
        from geometry + a material density, not a placeholder.

        Reuses exactly the mass-property data ``get_properties`` already
        computes (``Solid.MatrixOfInertia()`` -- unit-density, i.e.
        geometric moments about the center of mass, per that method's own
        docstring) rather than introducing a second computation path. The
        one new ingredient is ``material``: nothing previously converted it
        to a density (see ``materials.py``), but both URDF's and SDF's
        ``<inertial>`` blocks need a real mass, not a geometric volume.

        Unit conversion: CadQuery/OCCT report volume in mm^3 and inertia in
        mm^5 (both implicitly unit-density -- ``density=1``). Both URDF and
        SDF expect SI: mass in kg, inertia in kg*m^2, lengths in m.
            mass_kg      = volume_mm3 * 1e-9 * density_kg_m3
            inertia_kgm2 = inertia_mm5 * 1e-15 * density_kg_m3
            (1 mm^3 = 1e-9 m^3; 1 mm^5 = 1e-15 m^5)
        """
        solid = shape.val()
        volume_mm3 = solid.Volume()
        com = solid.Center()
        inertia_geo = solid.MatrixOfInertia()

        density = resolve_density_kg_m3(material, density_kg_m3)
        mass_kg = volume_mm3 * _MM3_TO_M3 * density

        def _inertia(r: int, c: int) -> float:
            return inertia_geo.Value(r + 1, c + 1) * _MM5_TO_M5 * density

        return {
            "density_kg_m3": density,
            "mass_kg": mass_kg,
            "com_m": (com.x * _MM_TO_M, com.y * _MM_TO_M, com.z * _MM_TO_M),
            "inertia_kgm2": (
                _inertia(0, 0),
                _inertia(0, 1),
                _inertia(0, 2),
                _inertia(1, 1),
                _inertia(1, 2),
                _inertia(2, 2),
            ),
        }

    def export_urdf(
        self,
        input_file: str,
        link_name: str = "base_link",
        material: str = "",
        density_kg_m3: float | None = None,
        mesh_format: str = "stl",
        mesh_uri_prefix: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a single-link URDF (robot description) for a STEP file.

        Tier-1 URDF support (MET-706 session): one link, no joints -- a
        multi-body assembly with real kinematic joints needs the FreeCAD
        assembly-joint tools (``freecad_add_assembly_joint``) mapped to
        URDF's ``<joint>`` schema, which is separate follow-on work. See
        ``_compute_mass_properties`` for the mass/inertia derivation.
        """
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.export_urdf") as span:
            span.set_attribute("input.file", input_file)
            span.set_attribute("link.name", link_name)

            start = time.monotonic()

            shape = cq.importers.importStep(input_file)
            mp = self._compute_mass_properties(shape, material, density_kg_m3)
            density = mp["density_kg_m3"]
            mass_kg = mp["mass_kg"]
            com_m = mp["com_m"]
            ixx, ixy, ixz, iyy, iyz, izz = mp["inertia_kgm2"]

            if not output_path:
                stem = Path(input_file).stem
                output_path = os.path.join(self.work_dir, f"{stem}.urdf")
            self._ensure_output_dir(output_path)

            out_dir = os.path.dirname(output_path) or self.work_dir
            mesh_stem = Path(output_path).stem
            mesh_path = os.path.join(out_dir, f"{mesh_stem}.{mesh_format}")
            cq.exporters.export(shape, mesh_path, exportType=mesh_format.upper())
            mesh_uri = mesh_uri_prefix + os.path.basename(mesh_path)

            urdf_xml = _build_single_link_urdf(
                link_name=link_name,
                mesh_uri=mesh_uri,
                mass_kg=mass_kg,
                com_m=com_m,
                inertia_kgm2=(ixx, ixy, ixz, iyy, iyz, izz),
            )
            with open(output_path, "w", encoding="utf-8") as f:  # noqa: PTH123
                f.write(urdf_xml)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported URDF",
                input_file=input_file,
                output_path=output_path,
                mesh_path=mesh_path,
                mass_kg=round(mass_kg, 6),
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "mesh_file": mesh_path,
                "link_name": link_name,
                "density_kg_m3": density,
                # Not decimal-place-rounded -- same reasoning as inertia_kgm2
                # below: a small real part can legitimately mass < 1e-6 kg.
                "mass_kg": mass_kg,
                "center_of_mass_m": {
                    "x": round(com_m[0], 6),
                    "y": round(com_m[1], 6),
                    "z": round(com_m[2], 6),
                },
                # noqa comment: NOT decimal-place-rounded like the mm-scale
                # fields above -- physical inertia in kg*m^2 is legitimately
                # tiny for small parts (e.g. ~1e-11), and round(x, 9) would
                # silently zero out a real, correct value.
                "inertia_kgm2": {
                    "ixx": ixx,
                    "ixy": ixy,
                    "ixz": ixz,
                    "iyy": iyy,
                    "iyz": iyz,
                    "izz": izz,
                },
            }

    def export_urdf_assembly(
        self,
        parts: list[dict[str, Any]],
        joints: list[dict[str, Any]],
        robot_name: str = "robot",
        mesh_format: str = "stl",
        mesh_uri_prefix: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a multi-link URDF with real kinematic joints.

        Tier-2a (MET-706 session follow-on to ``export_urdf``'s tier-1).
        Decoupled from any live FreeCAD session -- ``joints`` takes the same
        shape FreeCAD's ``add_assembly_joint``/``list_joints`` already
        produce (``{name, type, base, follower, axis, anchor}``, see
        ``tool_registry/tools/freecad/adapter.py``), so a caller can pass
        a FreeCAD session's joint list straight through. See
        ``_URDF_JOINT_TYPE_MAP``'s module comment for the joint-type
        mapping (and why ``cylindrical``/``ball`` raise rather than
        approximate) and ``_build_assembly_urdf`` for the URDF assembly.

        ``parts``: each ``{input_file, link_name, material="", density_kg_m3=None}``.
        ``joints``: each ``{name, type, base, follower, axis, anchor, limits?}``
        where ``base``/``follower`` are ``link_name`` values from ``parts``,
        and ``limits`` (``{lower, upper, effort?, velocity?}``) is required
        only for ``slider`` (URDF ``prismatic``) joints.
        """
        self._require_cadquery()

        if not parts:
            raise ValueError("parts is required and must be non-empty")

        with tracer.start_as_current_span("cadquery.export_urdf_assembly") as span:
            span.set_attribute("robot.name", robot_name)
            span.set_attribute("parts.count", len(parts))
            span.set_attribute("joints.count", len(joints))

            start = time.monotonic()

            if not output_path:
                output_path = os.path.join(self.work_dir, f"{robot_name}.urdf")
            self._ensure_output_dir(output_path)
            out_dir = os.path.dirname(output_path) or self.work_dir

            links: list[dict[str, Any]] = []
            mesh_files: list[str] = []
            for part in parts:
                link_name = part["link_name"]
                shape = cq.importers.importStep(part["input_file"])
                mp = self._compute_mass_properties(
                    shape, part.get("material", ""), part.get("density_kg_m3")
                )
                mesh_path = os.path.join(out_dir, f"{link_name}.{mesh_format}")
                cq.exporters.export(shape, mesh_path, exportType=mesh_format.upper())
                mesh_files.append(mesh_path)
                links.append(
                    {
                        "name": link_name,
                        "mesh_uri": mesh_uri_prefix + os.path.basename(mesh_path),
                        "mass_kg": mp["mass_kg"],
                        "com_m": mp["com_m"],
                        "inertia_kgm2": mp["inertia_kgm2"],
                    }
                )

            urdf_xml = _build_assembly_urdf(robot_name=robot_name, links=links, joints=joints)
            with open(output_path, "w", encoding="utf-8") as f:  # noqa: PTH123
                f.write(urdf_xml)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported assembly URDF",
                robot_name=robot_name,
                output_path=output_path,
                link_count=len(links),
                joint_count=len(joints),
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "mesh_files": mesh_files,
                "robot_name": robot_name,
                "link_names": [link["name"] for link in links],
                "joint_names": [joint.get("name", "") for joint in joints],
            }

    def export_sdf(
        self,
        input_file: str,
        model_name: str = "model",
        link_name: str = "link",
        material: str = "",
        density_kg_m3: float | None = None,
        mesh_format: str = "stl",
        mesh_uri: str = "",
        static: bool = False,
        world_name: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a single-link SDFormat model (Gazebo) for a STEP file.

        Schema grounded directly against the primary source
        (gazebosim/sdformat's ``sdf/1.11/{model,link,inertial,collision,
        visual,mesh_shape}.sdf``), not guessed: a ``<model>`` requires a
        ``name`` and contains ``<link>`` elements; a ``<link>`` contains
        ``<inertial>`` (``<mass>``, ``<pose>`` for the center-of-mass frame,
        ``<inertia>`` with ``ixx/ixy/ixz/iyy/iyz/izz``) plus ``<collision>``/
        ``<visual>`` (each ``<geometry><mesh><uri>...</uri></mesh></geometry>``).
        SDF's units are already SI (m, kg, kg*m^2, radians) -- same
        convention as URDF, so no extra unit-mismatch to handle beyond what
        ``_compute_mass_properties`` already does.

        Tier-1 only: one model, one link, no ``<joint>`` -- same scope note
        as ``export_urdf``. ``world_name``, when given, wraps the model in
        a ``<world>`` (producing a ``.world`` file); otherwise this writes a
        standalone ``.sdf`` model file (``<sdf><model>...</model></sdf>``),
        includable into a world separately via SDF's own ``<include>``.
        """
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.export_sdf") as span:
            span.set_attribute("input.file", input_file)
            span.set_attribute("model.name", model_name)

            start = time.monotonic()

            shape = cq.importers.importStep(input_file)
            mp = self._compute_mass_properties(shape, material, density_kg_m3)

            if not output_path:
                stem = Path(input_file).stem
                ext = "world" if world_name else "sdf"
                output_path = os.path.join(self.work_dir, f"{stem}.{ext}")
            self._ensure_output_dir(output_path)

            out_dir = os.path.dirname(output_path) or self.work_dir
            mesh_stem = Path(output_path).stem
            mesh_path = os.path.join(out_dir, f"{mesh_stem}.{mesh_format}")
            cq.exporters.export(shape, mesh_path, exportType=mesh_format.upper())
            resolved_mesh_uri = mesh_uri or os.path.basename(mesh_path)

            sdf_xml = _build_single_link_sdf(
                model_name=model_name,
                link_name=link_name,
                mesh_uri=resolved_mesh_uri,
                mass_kg=mp["mass_kg"],
                com_m=mp["com_m"],
                inertia_kgm2=mp["inertia_kgm2"],
                static=static,
                world_name=world_name,
            )
            with open(output_path, "w", encoding="utf-8") as f:  # noqa: PTH123
                f.write(sdf_xml)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported SDF",
                input_file=input_file,
                output_path=output_path,
                mesh_path=mesh_path,
                mass_kg=round(mp["mass_kg"], 6),
                duration_s=round(elapsed, 3),
            )

            ixx, ixy, ixz, iyy, iyz, izz = mp["inertia_kgm2"]
            com_m = mp["com_m"]
            return {
                "output_file": output_path,
                "mesh_file": mesh_path,
                "model_name": model_name,
                "link_name": link_name,
                "density_kg_m3": mp["density_kg_m3"],
                "mass_kg": mp["mass_kg"],
                "center_of_mass_m": {
                    "x": round(com_m[0], 6),
                    "y": round(com_m[1], 6),
                    "z": round(com_m[2], 6),
                },
                "inertia_kgm2": {
                    "ixx": ixx,
                    "ixy": ixy,
                    "ixz": ixz,
                    "iyy": iyy,
                    "iyz": iyz,
                    "izz": izz,
                },
            }

    def export_sdf_assembly(
        self,
        parts: list[dict[str, Any]],
        joints: list[dict[str, Any]],
        model_name: str = "model",
        mesh_format: str = "stl",
        static: bool = False,
        world_name: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a multi-link SDFormat model with real joints.

        Tier-2a (MET-706 session follow-on to ``export_sdf``'s tier-1) --
        same ``parts``/``joints`` shapes as ``export_urdf_assembly``. See
        ``_SDF_JOINT_TYPE_MAP``'s module comment for SDF's (more permissive
        than URDF's) joint-type mapping, and ``_build_assembly_sdf`` for the
        SDF document.
        """
        self._require_cadquery()

        if not parts:
            raise ValueError("parts is required and must be non-empty")

        with tracer.start_as_current_span("cadquery.export_sdf_assembly") as span:
            span.set_attribute("model.name", model_name)
            span.set_attribute("parts.count", len(parts))
            span.set_attribute("joints.count", len(joints))

            start = time.monotonic()

            if not output_path:
                ext = "world" if world_name else "sdf"
                output_path = os.path.join(self.work_dir, f"{model_name}.{ext}")
            self._ensure_output_dir(output_path)
            out_dir = os.path.dirname(output_path) or self.work_dir

            links: list[dict[str, Any]] = []
            mesh_files: list[str] = []
            for part in parts:
                link_name = part["link_name"]
                shape = cq.importers.importStep(part["input_file"])
                mp = self._compute_mass_properties(
                    shape, part.get("material", ""), part.get("density_kg_m3")
                )
                mesh_path = os.path.join(out_dir, f"{link_name}.{mesh_format}")
                cq.exporters.export(shape, mesh_path, exportType=mesh_format.upper())
                mesh_files.append(mesh_path)
                links.append(
                    {
                        "name": link_name,
                        "mesh_uri": os.path.basename(mesh_path),
                        "mass_kg": mp["mass_kg"],
                        "com_m": mp["com_m"],
                        "inertia_kgm2": mp["inertia_kgm2"],
                    }
                )

            sdf_xml = _build_assembly_sdf(
                model_name=model_name,
                links=links,
                joints=joints,
                static=static,
                world_name=world_name,
            )
            with open(output_path, "w", encoding="utf-8") as f:  # noqa: PTH123
                f.write(sdf_xml)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported assembly SDF",
                model_name=model_name,
                output_path=output_path,
                link_count=len(links),
                joint_count=len(joints),
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "mesh_files": mesh_files,
                "model_name": model_name,
                "link_names": [link["name"] for link in links],
                "joint_names": [joint.get("name", "") for joint in joints],
            }

    def export_usd(
        self,
        input_file: str,
        prim_name: str = "model",
        material: str = "",
        density_kg_m3: float | None = None,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a plain-text ``.usda`` (USD) file for a STEP file.

        Tier-1 only (MET-713): axis-aligned parts, where the inertia
        tensor's off-diagonal terms are negligible -- see
        ``usd_export.py``'s module docstring for why the general
        (rotated/asymmetric) case needs real 3x3 eigendecomposition rather
        than being silently approximated, and why this hand-authors
        ``.usda`` text instead of depending on ``usd-core``/``pxr``.

        Unlike ``export_urdf``/``export_sdf`` (which point at an external
        mesh file), USD wants geometry authored as explicit ``UsdGeomMesh``
        point/face arrays -- so the mesh is exported to STL and then
        re-parsed back into those arrays (``usd_export.parse_stl_mesh``)
        rather than referenced by path.
        """
        self._require_cadquery()

        with tracer.start_as_current_span("cadquery.export_usd") as span:
            span.set_attribute("input.file", input_file)
            span.set_attribute("prim.name", prim_name)

            start = time.monotonic()

            shape = cq.importers.importStep(input_file)
            mp = self._compute_mass_properties(shape, material, density_kg_m3)

            if not output_path:
                stem = Path(input_file).stem
                output_path = os.path.join(self.work_dir, f"{stem}.usda")
            self._ensure_output_dir(output_path)

            out_dir = os.path.dirname(output_path) or self.work_dir
            mesh_stem = Path(output_path).stem
            mesh_path = os.path.join(out_dir, f"{mesh_stem}.stl")
            cq.exporters.export(shape, mesh_path, exportType="STL")

            points, face_vertex_indices, face_vertex_counts = parse_stl_mesh(mesh_path)

            usda_text = build_usda(
                prim_name=prim_name,
                points=points,
                face_vertex_indices=face_vertex_indices,
                face_vertex_counts=face_vertex_counts,
                mass_kg=mp["mass_kg"],
                com_m=mp["com_m"],
                inertia_kgm2=mp["inertia_kgm2"],
            )
            with open(output_path, "w", encoding="utf-8") as f:  # noqa: PTH123
                f.write(usda_text)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Exported USD",
                input_file=input_file,
                output_path=output_path,
                mesh_path=mesh_path,
                triangle_count=len(face_vertex_counts),
                mass_kg=round(mp["mass_kg"], 6),
                duration_s=round(elapsed, 3),
            )

            ixx, ixy, ixz, iyy, iyz, izz = mp["inertia_kgm2"]
            com_m = mp["com_m"]
            return {
                "output_file": output_path,
                "mesh_file": mesh_path,
                "prim_name": prim_name,
                "triangle_count": len(face_vertex_counts),
                "density_kg_m3": mp["density_kg_m3"],
                "mass_kg": mp["mass_kg"],
                "center_of_mass_m": {
                    "x": round(com_m[0], 6),
                    "y": round(com_m[1], 6),
                    "z": round(com_m[2], 6),
                },
                "inertia_kgm2": {
                    "ixx": ixx,
                    "ixy": ixy,
                    "ixz": ixz,
                    "iyy": iyy,
                    "iyz": iyz,
                    "izz": izz,
                },
            }

    def generate_ros2_launch(
        self,
        robot_name: str,
        default_urdf_path: str,
        output_path: str = "",
        include_joint_state_publisher_gui: bool = True,
        include_rviz: bool = True,
    ) -> dict[str, Any]:
        """Generate a standalone ROS 2 launch file for an exported URDF
        (MET-706 session, 4th and final robotics-simulation-file slice
        after export_urdf/export_sdf/export_usd).

        Pure text generation -- no CadQuery/geometry kernel involved,
        unlike this class's other methods, so ``_require_cadquery()`` is
        deliberately not called here. See ``ros_launch.py``'s module
        docstring for the real-source grounding (``ros/urdf_launch``) and
        why the URDF path is a launch-time-resolved argument, not baked in.
        """
        with tracer.start_as_current_span("cadquery.generate_ros2_launch") as span:
            span.set_attribute("robot.name", robot_name)

            start = time.monotonic()

            launch_text = build_ros2_launch_py(
                robot_name=robot_name,
                default_urdf_path=default_urdf_path,
                include_joint_state_publisher_gui=include_joint_state_publisher_gui,
                include_rviz=include_rviz,
            )

            if not output_path:
                output_path = os.path.join(self.work_dir, f"{robot_name}.launch.py")
            self._ensure_output_dir(output_path)
            with open(output_path, "w", encoding="utf-8") as f:  # noqa: PTH123
                f.write(launch_text)

            elapsed = time.monotonic() - start
            span.set_attribute("operation.duration_s", round(elapsed, 3))

            logger.info(
                "Generated ROS 2 launch file",
                robot_name=robot_name,
                output_path=output_path,
                duration_s=round(elapsed, 3),
            )

            return {
                "output_file": output_path,
                "robot_name": robot_name,
                "default_urdf_path": default_urdf_path,
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

            # Check for blocked names using word-boundary matching to avoid
            # false positives (e.g. "os" matching "close", "position", etc.)
            if self.sandbox_enabled:
                for blocked in _BLOCKED_NAMES:
                    if re.search(r"\b" + re.escape(blocked) + r"\b", script):
                        raise ScriptSandboxError(f"Script contains blocked name: '{blocked}'")

            # Strip import lines for modules already injected into the sandbox
            # namespace.  Scripts (both LLM-generated and deterministic fallbacks)
            # commonly start with ``import cadquery as cq`` or ``import math``,
            # but the sandbox restricts __builtins__ (no __import__), so bare
            # import statements would raise a NameError at exec() time.
            script = _strip_sandbox_imports(script)

            if not output_path:
                output_path = os.path.join(self.work_dir, "script_result.step")
            self._ensure_output_dir(output_path)

            # Build sandboxed namespace
            import builtins as _builtins_module
            import functools

            safe_builtins: dict[str, Any] = {}
            for k in _SAFE_BUILTINS:
                if hasattr(_builtins_module, k):
                    safe_builtins[k] = getattr(_builtins_module, k)
            # MET-645 follow-up: a restricted __import__ so any import/from-
            # import syntax _strip_sandbox_imports' regex doesn't catch
            # (dotted submodules, unusual formatting) still resolves safely.
            safe_builtins["__import__"] = _sandboxed_import

            namespace: dict[str, Any] = {
                "__builtins__": safe_builtins,
                "cq": cq,
                "cadquery": cq,
                "math": math,
                "reduce": functools.reduce,
                # MET-688: `from cadquery import exporters` is stripped by
                # _strip_sandbox_imports (its root "cadquery" matches
                # _SANDBOX_MODULES) but nothing rebinds `exporters` as a bare
                # name afterward -- the same class of gap MET-645/649 already
                # fixed for FreeCAD's Vector/Rotation/Placement/Matrix/math
                # convenience names. Confirmed live: a script wrote
                # `exporters.export(...)` and hit "name 'exporters' is not
                # defined". `cq.exporters` is already used internally
                # throughout this file with no extra import, so it's safe to
                # pre-bind the same way.
                "exporters": cq.exporters,
                # MET-649: a no-op stub for the common CQ-editor/CQGI
                # `show_object(shape)` convention -- not part of this
                # headless execution context, but common enough in
                # model-generated scripts (hallucinated from generic
                # CadQuery scripting knowledge) that silently accepting
                # it beats a NameError.
                "show_object": lambda *_a, **_k: None,
                **{
                    name: getattr(math, name)
                    for name in _MATH_CONVENIENCE_NAMES
                    if hasattr(math, name)
                },
                # MET-702: `from cadquery import Workplane, Vector, ...` is
                # stripped by _strip_sandbox_imports (root "cadquery" matches
                # _SANDBOX_MODULES) but nothing rebinds those names bare --
                # the same gap MET-645/649/688 already hit for a handful of
                # individual names, one at a time. Found this time via a real
                # external dataset (CAD-Coder, 8.8K CadQuery scripts): ~1.3%
                # bare-imported a name outside the already-fixed set (mostly
                # `Workplane`/`Vector`, plus one-off `Solid`/`Assembly`/
                # `Sketch`/`Plane`/`Location`). Rather than add names one at a
                # time as the next script happens to use a new one, pre-bind
                # cadquery's ENTIRE public top-level API -- every one of these
                # is already fully reachable via `cq.<Name>` (see `"cq": cq`
                # above), so this grants no new capability, only convenience.
                # `cq` itself is excluded: `dir(cq)` includes the internal
                # `cadquery.cq` submodule under that same name, which would
                # silently replace the `cq` alias for the top-level package
                # every script actually expects.
                **{
                    name: getattr(cq, name)
                    for name in dir(cq)
                    if not name.startswith("_") and name != "cq"
                },
            }

            # Execute with timeout
            exec_timeout = timeout or self.timeout
            is_main_thread = threading.current_thread() is threading.main_thread()
            old_handler = None

            def _timeout_handler(signum: int, frame: Any) -> None:
                raise ScriptTimeoutError(f"Script execution exceeded {exec_timeout}s timeout")

            try:
                # Use signal.alarm for timeout when running in the main thread
                # (works inside Docker on Linux). When running in a worker thread
                # (e.g. via asyncio.to_thread), fall back to wall-clock checking
                # after exec() returns -- the asyncio.wait_for on the caller side
                # provides the hard timeout in that case.
                if is_main_thread and hasattr(signal, "SIGALRM"):
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
                if is_main_thread and hasattr(signal, "SIGALRM"):
                    signal.alarm(0)
                    if old_handler is not None:
                        signal.signal(signal.SIGALRM, old_handler)

            # Wall-clock timeout check for worker threads where signal.alarm
            # is not available.
            if not is_main_thread:
                elapsed_so_far = time.monotonic() - start
                if elapsed_so_far > exec_timeout:
                    raise ScriptTimeoutError(f"Script execution exceeded {exec_timeout}s timeout")

            # Extract result
            result_obj = namespace.get("result")
            if result_obj is None:
                raise ValueError("Script must assign its output to a variable named 'result'")

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

            result: dict[str, Any] = {
                "cad_file": output_path,
                "script_text": script,
                **props,
            }
            # MET-648: without step_base64, CadQuery output had no path into
            # twin.commit_geometry at all (unlike freecad.export_model, which
            # already returns step_base64) -- the model would generate correct
            # geometry via CadQuery and then have nothing valid to commit,
            # silently falling back to committing an earlier, worse attempt
            # from a different tool. Only populate it for an actual STEP
            # export -- commit_geometry's step_base64 is STEP-specific.
            if output_path.lower().endswith((".step", ".stp")):
                with open(output_path, "rb") as f:  # noqa: PTH123
                    result["step_base64"] = base64.b64encode(f.read()).decode("ascii")
            return result

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
                        enclosure.faces(face_sel)
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
                        enclosure.faces("<Z").workplane().center(h_x, h_y).hole(h_dia, post_height)
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
