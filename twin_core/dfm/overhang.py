"""The FDM overhang check on a face table (FORGE-273), shared by evidence and heuristics.

Moved here from ``api_gateway.twin.dfm_evidence`` so the DFM heuristics in
twin_core can reuse the same check; that module re-exports these names.

A mesh face table's normals have no guaranteed sense (a gmsh face table does
not know which side is outside), so the default check measures the
**undirected** tilt of each face's plane from the vertical build axis and
flags anything past the threshold. That is conservative: it also flags flat
top faces. When the faces come from a B-Rep with outward normals
(``directed``), only downward-facing faces are overhangs.

Pure: the standard library only.
"""

from __future__ import annotations

import math
from typing import Any

#: Build direction (unit vector) when the caller gives none: Z up.
DEFAULT_BUILD_AXIS: tuple[float, float, float] = (0.0, 0.0, 1.0)

#: The FDM "self-supporting" convention: a face tilted up to 45 degrees from
#: a vertical wall prints without support. A face at exactly 45.0 degrees is
#: not flagged (the threshold is the steepest tilt that still prints).
OVERHANG_TILT_THRESHOLD_DEG = 45.0


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    mag = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def compute_face_tilt_from_vertical_deg(
    normal: tuple[float, float, float],
    build_axis: tuple[float, float, float] = DEFAULT_BUILD_AXIS,
) -> float:
    """Undirected tilt (0 to 90 degrees) of a face's plane from the vertical build axis.

    0 is a vertical wall (normal perpendicular to the axis), 90 a horizontal
    face (normal along the axis, either sense). A zero normal returns 0.0:
    there is no signal to flag on.
    """
    n = _normalize(normal)
    if n == (0.0, 0.0, 0.0):
        return 0.0
    b = _normalize(build_axis)
    dot = abs(n[0] * b[0] + n[1] * b[1] + n[2] * b[2])
    dot_clamped = max(-1.0, min(1.0, dot))
    angle_from_axis_deg = math.degrees(math.acos(dot_clamped))
    return 90.0 - angle_from_axis_deg


def compute_overhang_faces(
    faces: list[dict[str, Any]],
    *,
    build_axis: tuple[float, float, float] = DEFAULT_BUILD_AXIS,
    threshold_deg: float = OVERHANG_TILT_THRESHOLD_DEG,
    directed: bool = False,
) -> list[dict[str, Any]]:
    """Per-face overhang check over a face table (``{name, normal, area_mm2}``).

    Faces with no usable normal are skipped (neither flagged nor counted as
    passing). With ``directed``, a face whose outward normal points up or
    sideways is never an overhang.
    """
    b = _normalize(build_axis)
    out: list[dict[str, Any]] = []
    for face in faces:
        normal = face.get("normal")
        if not isinstance(normal, (list, tuple)) or len(normal) != 3:
            continue
        normal_t = (float(normal[0]), float(normal[1]), float(normal[2]))
        unit = _normalize(normal_t)
        if unit == (0.0, 0.0, 0.0):
            continue
        tilt = compute_face_tilt_from_vertical_deg(normal_t, build_axis)
        downward = unit[0] * b[0] + unit[1] * b[1] + unit[2] * b[2] < -1e-9
        flagged = tilt > threshold_deg and (downward or not directed)
        out.append(
            {
                "name": face.get("name"),
                "area_mm2": face.get("area_mm2"),
                "normal": [round(v, 6) for v in normal_t],
                "tilt_from_vertical_deg": round(tilt, 3),
                "flagged": flagged,
            }
        )
    return out


__all__ = [
    "DEFAULT_BUILD_AXIS",
    "OVERHANG_TILT_THRESHOLD_DEG",
    "compute_face_tilt_from_vertical_deg",
    "compute_overhang_faces",
]
