"""USD (.usda) export -- hand-authored plain-text USD, no ``pxr``/``usd-core``.

``usd-core`` (the real OpenUSD Python library) has PyPI wheels, but adding
it means an untested ~150MB dependency plus a Docker image rebuild for the
cadquery-adapter -- the same risk class as the ``pythonocc-core`` situation
avoided elsewhere this session (see the DeepCAD ground-truth pipeline in
``evals/cad_bench/``). USD's plain-text ASCII format (``.usda``) is a
documented, stable, hand-authorable format; this module builds one directly.

Schema grounded against the primary source
(``PixarAnimationStudios/OpenUSD``'s ``pxr/usd/usdPhysics/schema.usda``,
fetched via ``gh api`` while building this, not guessed): ``PhysicsMassAPI``
exposes ``physics:mass`` (float), ``physics:centerOfMass`` (point3f),
``physics:diagonalInertia`` (float3), and ``physics:principalAxes``
(quatf) -- the *diagonalized* inertia tensor in its own principal-axis
frame, not the raw symmetric matrix URDF/SDF both accept directly. Tier 1
here (MET-713) only handles the axis-aligned case (negligible off-diagonal
terms, so the principal frame IS the link frame, identity quaternion) --
general diagonalization needs real 3x3 eigendecomposition, explicit
follow-on work, not silently approximated here.
"""

from __future__ import annotations

import math
import struct
from typing import Any

from tool_registry.tools.cadquery.joints import (
    MissingJointLimitsError,
    UnsupportedJointTypeError,
)

_BINARY_STL_HEADER_SIZE = 80
_BINARY_STL_TRIANGLE_RECORD_SIZE = 50  # 12 (normal) + 36 (3 vertices) + 2 (attr count)

# How close to zero an off-diagonal inertia term must be, relative to the
# largest diagonal term, to be treated as "axis-aligned" (see module
# docstring -- the alternative is silently wrong principal-axis physics).
_OFF_DIAGONAL_TOLERANCE = 1e-6


class NonAxisAlignedInertiaError(ValueError):
    """Raised when a part's inertia tensor has non-negligible off-diagonal
    terms -- USD's principal-axis inertia representation would need real
    3x3 eigendecomposition to represent it correctly, which is explicit
    tier-2 follow-on work (MET-713), not silently approximated as identity.
    """


def parse_stl_mesh(path: str) -> tuple[list[tuple[float, float, float]], list[int], list[int]]:
    """Parse an STL file (binary or ASCII) into USD mesh arrays.

    Returns ``(points, face_vertex_indices, face_vertex_counts)`` matching
    ``UsdGeomMesh``'s attribute shapes. STL has no shared-vertex topology --
    every triangle owns 3 unique vertices even where two triangles meet at
    a geometrically coincident point -- so this is an unwelded (but valid)
    mesh: ``points`` is the flat sequence of all triangle vertices in
    order, ``face_vertex_indices`` is simply ``0, 1, 2, 3, 4, 5, ...``, and
    ``face_vertex_counts`` is ``3`` repeated once per triangle.
    """
    with open(path, "rb") as f:  # noqa: PTH123
        data = f.read()

    if _looks_like_binary_stl(data):
        return _parse_binary_stl(data)
    return _parse_ascii_stl(data.decode("ascii", errors="replace"))


def _looks_like_binary_stl(data: bytes) -> bool:
    """Binary STL: an 80-byte header, a uint32 triangle count, then exactly
    that many 50-byte records -- so the total file size is fully
    determined by the count field. ASCII STL never matches this by
    construction (its per-triangle text is far longer than 50 bytes), so
    checking the size against the declared count is a reliable format
    detector -- more reliable than sniffing for a leading ``b"solid"``,
    since some binary STL writers put a "solid ..." string in the header
    too.
    """
    if len(data) < _BINARY_STL_HEADER_SIZE + 4:
        return False
    (triangle_count,) = struct.unpack_from("<I", data, _BINARY_STL_HEADER_SIZE)
    expected_size = _BINARY_STL_HEADER_SIZE + 4 + triangle_count * _BINARY_STL_TRIANGLE_RECORD_SIZE
    return len(data) == expected_size


def _parse_binary_stl(
    data: bytes,
) -> tuple[list[tuple[float, float, float]], list[int], list[int]]:
    (triangle_count,) = struct.unpack_from("<I", data, _BINARY_STL_HEADER_SIZE)
    points: list[tuple[float, float, float]] = []
    offset = _BINARY_STL_HEADER_SIZE + 4
    for _ in range(triangle_count):
        # Skip the 12-byte normal; recompute face normals downstream if
        # ever needed rather than trusting a possibly-degenerate one.
        v1 = struct.unpack_from("<3f", data, offset + 12)
        v2 = struct.unpack_from("<3f", data, offset + 24)
        v3 = struct.unpack_from("<3f", data, offset + 36)
        points.extend((v1, v2, v3))
        offset += _BINARY_STL_TRIANGLE_RECORD_SIZE

    face_vertex_indices = list(range(len(points)))
    face_vertex_counts = [3] * triangle_count
    return points, face_vertex_indices, face_vertex_counts


def _parse_ascii_stl(
    text: str,
) -> tuple[list[tuple[float, float, float]], list[int], list[int]]:
    points: list[tuple[float, float, float]] = []
    triangle_count = 0
    current_face: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("vertex"):
            parts = stripped.split()
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            current_face.append((x, y, z))
        elif stripped.startswith("endfacet"):
            if len(current_face) == 3:
                points.extend(current_face)
                triangle_count += 1
            current_face = []

    face_vertex_indices = list(range(len(points)))
    face_vertex_counts = [3] * triangle_count
    return points, face_vertex_indices, face_vertex_counts


def _check_axis_aligned(
    ixx: float, ixy: float, ixz: float, iyy: float, iyz: float, izz: float
) -> None:
    """Raise :class:`NonAxisAlignedInertiaError` if off-diagonal terms are
    not negligible relative to the diagonal magnitude (see module
    docstring for why this can't just be silently approximated)."""
    scale = max(abs(ixx), abs(iyy), abs(izz), 1e-30)
    if any(abs(t) > _OFF_DIAGONAL_TOLERANCE * scale for t in (ixy, ixz, iyz)):
        raise NonAxisAlignedInertiaError(
            "Part's inertia tensor has non-negligible off-diagonal terms "
            f"(ixy={ixy:.6g}, ixz={ixz:.6g}, iyz={iyz:.6g} vs. diagonal scale "
            f"{scale:.6g}) -- USD's principal-axis inertia representation needs "
            "real 3x3 eigendecomposition for this part, which is explicit "
            "tier-2 follow-on work (MET-713), not silently approximated here."
        )


def build_usda(
    *,
    prim_name: str,
    points: list[tuple[float, float, float]],
    face_vertex_indices: list[int],
    face_vertex_counts: list[int],
    mass_kg: float,
    com_m: tuple[float, float, float],
    inertia_kgm2: tuple[float, float, float, float, float, float],
) -> str:
    """Build a plain-text ``.usda`` document: one ``Xform`` prim containing
    one ``Mesh``, with ``PhysicsRigidBodyAPI``/``PhysicsCollisionAPI``/
    ``PhysicsMassAPI`` applied to the Xform (rigid-body physics attaches to
    the transformable prim, not the mesh geometry itself, per the
    UsdPhysics schema's own examples).

    Raises :class:`NonAxisAlignedInertiaError` if the inertia tensor isn't
    (nearly) diagonal -- see ``_check_axis_aligned``.
    """
    ixx, ixy, ixz, iyy, iyz, izz = inertia_kgm2
    _check_axis_aligned(ixx, ixy, ixz, iyy, iyz, izz)

    points_str = ", ".join(f"({x:.9g}, {y:.9g}, {z:.9g})" for x, y, z in points)
    indices_str = ", ".join(str(i) for i in face_vertex_indices)
    counts_str = ", ".join(str(c) for c in face_vertex_counts)

    return f'''#usda 1.0
(
    defaultPrim = "{prim_name}"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "{prim_name}" (
    prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]
)
{{
    float physics:mass = {mass_kg:.9g}
    point3f physics:centerOfMass = ({com_m[0]:.9g}, {com_m[1]:.9g}, {com_m[2]:.9g})
    float3 physics:diagonalInertia = ({ixx:.9g}, {iyy:.9g}, {izz:.9g})
    quatf physics:principalAxes = (1, 0, 0, 0)

    def Mesh "geometry"
    {{
        point3f[] points = [{points_str}]
        int[] faceVertexIndices = [{indices_str}]
        int[] faceVertexCounts = [{counts_str}]
    }}
}}
'''


# Tier-2a (MET-706 session follow-on to MET-713's single-body tier-1):
# grounded against the same schema.usda's PhysicsRevoluteJoint/
# PhysicsPrismaticJoint/PhysicsSphericalJoint/PhysicsFixedJoint classes.
# `fixed`->PhysicsFixedJoint and `ball`->PhysicsSphericalJoint are direct,
# faithful matches (USD has a native ball/spherical joint, same as SDF and
# unlike URDF). `revolute`->PhysicsRevoluteJoint and `slider`->
# PhysicsPrismaticJoint both take a `physics:lowerLimit`/`upperLimit` pair
# that DEFAULTS to -inf/inf when omitted (per the schema's own defaults),
# so -- same honesty rule as URDF's `continuous` and SDF's `continuous` --
# `revolute` omits limits entirely rather than fabricating a range, while
# `slider` requires the caller to supply one explicitly (a truly unbounded
# prismatic joint is physically unusual, so silence there is more likely a
# missing input than an intentional one). `cylindrical` has no matching
# UsdPhysics joint type (no single 2-DOF translate+rotate-about-same-axis
# joint exists in the schema) and is rejected, same as URDF/SDF.
_USD_JOINT_TYPE_MAP = {
    "fixed": "PhysicsFixedJoint",
    "slider": "PhysicsPrismaticJoint",
    "revolute": "PhysicsRevoluteJoint",
    "ball": "PhysicsSphericalJoint",
}
_USD_UNSUPPORTED_JOINT_TYPES = {"cylindrical"}


def _quat_align_x_to(axis: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """Shortest-arc quaternion (w, x, y, z) rotating the +X axis onto the
    given (not necessarily unit-length) ``axis`` vector.

    Needed because UsdPhysics's ``physics:axis`` is a canonical token
    (``"X"``/``"Y"``/``"Z"``), not a free vector like URDF's/SDF's `<axis>`
    -- an arbitrary joint axis has to be expressed by rotating the joint
    frame (``physics:localRot0``/``localRot1``) so its local X aligns with
    the real axis instead. Pure Python (no numpy) -- standard "quaternion
    between two unit vectors" construction.
    """
    ax, ay, az = axis
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    if norm < 1e-12:
        raise ValueError("joint axis must be a non-zero vector")
    ax, ay, az = ax / norm, ay / norm, az / norm

    dot = ax  # dot((1,0,0), (ax,ay,az))
    if dot > 1.0 - 1e-9:
        return (1.0, 0.0, 0.0, 0.0)
    if dot < -1.0 + 1e-9:
        # 180 degrees about any axis perpendicular to +X -- +Y is as good
        # as any and sends (1,0,0) to (-1,0,0) as required.
        return (0.0, 0.0, 1.0, 0.0)

    # cross((1,0,0), (ax,ay,az)) = (0, -az, ay)
    cx, cy, cz = 0.0, -az, ay
    w = 1.0 + dot
    mag = math.sqrt(w * w + cx * cx + cy * cy + cz * cz)
    return (w / mag, cx / mag, cy / mag, cz / mag)


def build_usda_assembly(
    *,
    robot_name: str,
    links: list[dict[str, Any]],
    joints: list[dict[str, Any]],
) -> str:
    """Build a multi-body ``.usda`` document with real UsdPhysics joints.

    ``links``: each ``{name, points, face_vertex_indices, face_vertex_counts,
    mass_kg, com_m, inertia_kgm2}`` -- the per-part mesh + mass-property
    shape ``build_usda`` takes, just N of them, each becoming its own
    ``def Xform`` (physics APIs applied directly to it, per the same
    UsdPhysics convention ``build_usda`` already follows).
    ``joints``: the same FreeCAD-shaped records ``_build_assembly_urdf``/
    ``_build_assembly_sdf`` take (``{name, type, base, follower, axis,
    anchor, limits?}``) -- see ``_USD_JOINT_TYPE_MAP``'s comment above for
    the type mapping.

    Raises :class:`NonAxisAlignedInertiaError` per-link (same as
    ``build_usda``), :class:`UnsupportedJointTypeError` for ``cylindrical``,
    and :class:`MissingJointLimitsError` for a ``slider`` joint with no
    ``limits`` supplied.
    """
    link_blocks: list[str] = []
    for link in links:
        ixx, ixy, ixz, iyy, iyz, izz = link["inertia_kgm2"]
        _check_axis_aligned(ixx, ixy, ixz, iyy, iyz, izz)
        com_m = link["com_m"]
        points_str = ", ".join(f"({x:.9g}, {y:.9g}, {z:.9g})" for x, y, z in link["points"])
        indices_str = ", ".join(str(i) for i in link["face_vertex_indices"])
        counts_str = ", ".join(str(c) for c in link["face_vertex_counts"])
        link_blocks.append(f'''
    def Xform "{link["name"]}" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]
    )
    {{
        float physics:mass = {link["mass_kg"]:.9g}
        point3f physics:centerOfMass = ({com_m[0]:.9g}, {com_m[1]:.9g}, {com_m[2]:.9g})
        float3 physics:diagonalInertia = ({ixx:.9g}, {iyy:.9g}, {izz:.9g})
        quatf physics:principalAxes = (1, 0, 0, 0)

        def Mesh "geometry"
        {{
            point3f[] points = [{points_str}]
            int[] faceVertexIndices = [{indices_str}]
            int[] faceVertexCounts = [{counts_str}]
        }}
    }}''')

    joint_blocks: list[str] = []
    for joint in joints:
        fc_type = joint["type"].lower()
        if fc_type in _USD_UNSUPPORTED_JOINT_TYPES:
            raise UnsupportedJointTypeError(
                f"joint {joint.get('name', '?')!r} has type {fc_type!r}, which has no "
                "matching UsdPhysics joint type (see _USD_JOINT_TYPE_MAP's module comment)"
            )
        usd_type = _USD_JOINT_TYPE_MAP[fc_type]
        name = joint.get("name", f"{joint['base']}_to_{joint['follower']}")
        anchor = joint.get("anchor") or (0.0, 0.0, 0.0)
        pos = f"({anchor[0] * 1e-3:.9g}, {anchor[1] * 1e-3:.9g}, {anchor[2] * 1e-3:.9g})"

        body_lines = [
            f"    rel physics:body0 = </{robot_name}/{joint['base']}>",
            f"    rel physics:body1 = </{robot_name}/{joint['follower']}>",
            f"    point3f physics:localPos0 = {pos}",
            f"    point3f physics:localPos1 = {pos}",
        ]

        extra_lines: list[str] = []
        if usd_type in ("PhysicsRevoluteJoint", "PhysicsPrismaticJoint"):
            axis = joint.get("axis") or (0.0, 0.0, 1.0)
            w, x, y, z = _quat_align_x_to(axis)
            body_lines.append(f"    quatf physics:localRot0 = ({w:.9g}, {x:.9g}, {y:.9g}, {z:.9g})")
            body_lines.append(f"    quatf physics:localRot1 = ({w:.9g}, {x:.9g}, {y:.9g}, {z:.9g})")
            extra_lines.append('    uniform token physics:axis = "X"')
            if usd_type == "PhysicsPrismaticJoint":
                limits = joint.get("limits")
                if not limits:
                    raise MissingJointLimitsError(
                        f"joint {name!r} is a prismatic (slider) joint -- no 'limits' "
                        "({'lower','upper'}) was supplied for it, and MetaForge's joint "
                        "metadata never captures one, so it must be passed explicitly "
                        "rather than fabricated"
                    )
                extra_lines.append(f"    float physics:lowerLimit = {limits['lower']:.9g}")
                extra_lines.append(f"    float physics:upperLimit = {limits['upper']:.9g}")

        block = "\n".join([f'def {usd_type} "{name}"', "{", *body_lines, *extra_lines, "}"])
        joint_blocks.append("\n    " + block.replace("\n", "\n    "))

    links_str = "\n".join(link_blocks)
    joints_str = "\n".join(joint_blocks)

    return f'''#usda 1.0
(
    defaultPrim = "{robot_name}"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "{robot_name}"
{{
{links_str}
{joints_str}
}}
'''
