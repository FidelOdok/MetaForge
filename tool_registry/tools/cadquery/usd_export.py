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

import struct

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
