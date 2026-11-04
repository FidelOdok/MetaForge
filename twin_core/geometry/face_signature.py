"""Face signatures and face remapping for face-attached sketch planes (CDE slice 9).

A sketch plane can sit on a planar face of a CAD revision. When the CAD item
gets a new revision, OCCT's face ids (explorer order) mean nothing on it: a
face index alone must never be trusted (FRS PLN-009). This module is how the
face is found again:

* :class:`FaceSignature`: what was true of the face when the plane was put
  on it. The **semantic tag** (which sketch primitive and feature made the
  face, when the build path knows), the unit outward **normal**, the plane
  **offset**, **area**, **centroid**, the outer-loop **edge count** and the
  multiset of **adjacent face normals**, plus the part size it was measured
  against.
* :func:`score_candidate` scores one planar face of the new revision. A
  matching semantic tag dominates (a face that keeps its tag is the same
  face even when it moved or grew); geometry decides between faces with the
  same tag, and is all there is when no tag is known. Position tolerances are
  relative to the part size, so a small part and a large one behave alike.
* :func:`remap` picks a face only when the best score is at or above
  :data:`CONFIDENCE` and beats the runner-up by at least :data:`MARGIN`
  (``unique``). Two faces too close to call is ``ambiguous``, nothing good
  enough is ``missing``; either way the caller keeps the last valid
  attachment and asks a person (PLN-010). Nothing here ever picks a face
  silently.
* :func:`frame_from_face` is the plane frame a face gives: origin at the
  face centroid projected onto its plane, U along the longest straight edge
  of the outer loop (made canonical, so the same edge gives the same U),
  the normal outward, and ``V = N x U`` so the frame is right-handed.
* :func:`tag_faces` adds semantic tags from a revision's recorded build
  source: the extrude of a sketch tags its end faces ``top`` and
  ``bottom`` and each straight side by the sketch primitive it was swept
  from.

Pure: Pydantic and the standard library only.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from twin_core.models.sketch_plane import PRINCIPAL_FRAMES, PrincipalPlane, Vec3, cross, dot, norm

#: A remap needs at least this score to pick a face.
CONFIDENCE = 0.6
#: ... and must beat the runner-up by at least this much.
MARGIN = 0.1
#: A face whose normal turned by more than this is not the same face.
MAX_NORMAL_DEG = 20.0
#: Angle (degrees) at which the normal term has dropped to a half.
NORMAL_SCALE_DEG = 5.0
#: Relative area difference at which the area term has dropped to a half.
AREA_SCALE = 0.25
#: Centroid and offset distance, as a fraction of the part size, at which
#: their terms have dropped to a half.
POSITION_SCALE = 0.3
#: A tag match scores at least this (so the tag dominates), plus geometry.
TAG_MATCH_FLOOR = 0.7
#: A tag mismatch keeps at most this fraction of the geometry score.
TAG_MISMATCH_FACTOR = 0.3


class RemapOutcome(StrEnum):
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


def _unit(v: Vec3) -> Vec3:
    length = norm(v)
    if length <= 0:
        raise ValueError("a direction needs a non-zero length")
    return (v[0] / length, v[1] / length, v[2] / length)


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _key(v: Any, places: int = 2) -> tuple[float, float, float]:
    out = tuple(0.0 if round(float(c), places) == 0 else round(float(c), places) for c in v)
    return (out[0], out[1], out[2])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PlanarFace(BaseModel):
    """One planar face as ``cadquery.describe_faces`` reports it."""

    model_config = ConfigDict(extra="ignore")

    face_id: str = Field(min_length=1, max_length=200)
    normal: Vec3
    offset: float
    area: float = Field(ge=0)
    centroid: Vec3
    bounding_box: dict[str, float] | None = None
    outer_loop_edges: int = Field(default=0, ge=0)
    longest_edge: dict[str, Any] | None = None
    adjacent: list[dict[str, Any]] = Field(default_factory=list)
    mesh: dict[str, int] | None = None
    #: Which sketch primitive and feature made the face, when known.
    semantic_tag: str | None = None

    def adjacent_normals(self) -> list[Vec3]:
        out: list[Vec3] = []
        for a in self.adjacent:
            n = a.get("normal")
            if isinstance(n, (list, tuple)) and len(n) == 3:
                out.append(_key(n, 6))
        return sorted(out)

    def adjacent_curved(self) -> int:
        return sum(1 for a in self.adjacent if not a.get("normal"))


class FaceSignature(BaseModel):
    """What was true of a face when a plane was attached to it (PLN-009)."""

    model_config = ConfigDict(extra="forbid")

    semantic_tag: str | None = None
    normal: Vec3
    offset: float
    area: float = Field(ge=0)
    centroid: Vec3
    loop_edges: int = Field(default=0, ge=0)
    adjacent_normals: list[Vec3] = Field(default_factory=list)
    adjacent_curved: int = Field(default=0, ge=0)
    #: The part's bounding-box diagonal when the signature was taken.
    part_size: float = Field(default=1.0, gt=0)
    #: The face id it was taken from (meaningful on that revision only).
    face_id: str | None = None

    def summary(self) -> str:
        """A one-line, person-readable summary."""
        n = ", ".join(f"{c:g}" for c in _key(self.normal, 3))
        parts = [
            f"normal ({n})",
            f"area {self.area:.4g}",
            f"{self.loop_edges} edge(s)",
            f"{len(self.adjacent_normals) + self.adjacent_curved} neighbour(s)",
        ]
        if self.semantic_tag:
            parts.insert(0, f"made by {self.semantic_tag}")
        return "; ".join(parts)


def part_size_of(faces: list[PlanarFace], bounding_box: dict[str, Any] | None = None) -> float:
    """The part's bounding-box diagonal (from the tool's box, else the faces')."""
    box = bounding_box or {}
    keys = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    if all(isinstance(box.get(k), (int, float)) for k in keys):
        lo = (float(box["min_x"]), float(box["min_y"]), float(box["min_z"]))
        hi = (float(box["max_x"]), float(box["max_y"]), float(box["max_z"]))
    else:
        pts: list[Vec3] = []
        for f in faces:
            b = f.bounding_box or {}
            if all(isinstance(b.get(k), (int, float)) for k in keys):
                pts.append((float(b["min_x"]), float(b["min_y"]), float(b["min_z"])))
                pts.append((float(b["max_x"]), float(b["max_y"]), float(b["max_z"])))
            else:
                pts.append(f.centroid)
        if not pts:
            return 1.0
        lo = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts))
        hi = (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
    return max(norm(_sub(hi, lo)), 1e-6)


def signature_of(face: PlanarFace, part_size: float) -> FaceSignature:
    return FaceSignature(
        semantic_tag=face.semantic_tag,
        normal=_unit(face.normal),
        offset=face.offset,
        area=face.area,
        centroid=face.centroid,
        loop_edges=face.outer_loop_edges,
        adjacent_normals=face.adjacent_normals(),
        adjacent_curved=face.adjacent_curved(),
        part_size=max(part_size, 1e-6),
        face_id=face.face_id,
    )


# ---------------------------------------------------------------------------
# Scoring and remapping
# ---------------------------------------------------------------------------


@dataclass
class CandidateScore:
    face_id: str
    score: float
    geometry: float
    tag: str  # match | mismatch | unknown
    terms: dict[str, float] = field(default_factory=dict)


def _falloff(x: float, scale: float) -> float:
    return 1.0 / (1.0 + (x / scale) ** 2)


def _adjacency(sig: FaceSignature, face: PlanarFace) -> float:
    a: Counter[Any] = Counter(_key(n) for n in sig.adjacent_normals)
    a["curved"] += sig.adjacent_curved
    b: Counter[Any] = Counter(_key(n) for n in face.adjacent_normals())
    b["curved"] += face.adjacent_curved()
    a, b = +a, +b
    total = max(sum(a.values()), sum(b.values()))
    if total == 0:
        return 1.0
    return sum((a & b).values()) / total


def score_candidate(sig: FaceSignature, face: PlanarFace, scale: float) -> CandidateScore:
    """How well ``face`` matches ``sig``, in ``[0, 1]``. See the module docstring."""
    n_new = _unit(face.normal)
    cos = max(-1.0, min(1.0, dot(sig.normal, n_new)))
    angle = math.degrees(math.acos(cos))
    tag = "unknown"
    if sig.semantic_tag and face.semantic_tag:
        tag = "match" if sig.semantic_tag == face.semantic_tag else "mismatch"
    if angle > MAX_NORMAL_DEG:
        return CandidateScore(face.face_id, 0.0, 0.0, tag, {"normal_deg": round(angle, 3)})
    size = max(scale, 1e-6) * POSITION_SCALE
    biggest = max(sig.area, face.area, 1e-12)
    terms = {
        "normal": _falloff(angle, NORMAL_SCALE_DEG),
        "area": _falloff(abs(sig.area - face.area) / biggest, AREA_SCALE),
        "centroid": _falloff(norm(_sub(sig.centroid, face.centroid)), size),
        "offset": _falloff(abs(sig.offset - face.offset), size),
        "loop": 1.0 / (1.0 + abs(sig.loop_edges - face.outer_loop_edges)),
        "adjacent": _adjacency(sig, face),
    }
    geometry = (
        terms["normal"]
        * terms["area"]
        * (
            0.4 * terms["centroid"]
            + 0.3 * terms["offset"]
            + 0.15 * terms["loop"]
            + 0.15 * terms["adjacent"]
        )
    )
    if tag == "match":
        score = TAG_MATCH_FLOOR + (1.0 - TAG_MATCH_FLOOR) * geometry
    elif tag == "mismatch":
        score = TAG_MISMATCH_FACTOR * geometry
    else:
        score = geometry
    return CandidateScore(
        face.face_id,
        round(score, 6),
        round(geometry, 6),
        tag,
        {k: round(v, 6) for k, v in terms.items()},
    )


@dataclass
class RemapResult:
    outcome: RemapOutcome
    face: PlanarFace | None
    score: float
    runner_up: float | None
    #: Faces a person might mean, best first (for highlighting).
    candidates: list[CandidateScore]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "face_id": self.face.face_id if self.face else None,
            "score": self.score,
            "runner_up": self.runner_up,
            "candidates": [
                {"face_id": c.face_id, "score": c.score, "tag": c.tag} for c in self.candidates
            ],
            "reason": self.reason,
        }


def remap(
    sig: FaceSignature,
    faces: list[PlanarFace],
    *,
    part_size: float | None = None,
    confidence: float = CONFIDENCE,
    margin: float = MARGIN,
) -> RemapResult:
    """Find the face ``sig`` describes among ``faces``. Never guesses."""
    scale = max(sig.part_size, part_size or part_size_of(faces))
    scored = sorted(
        (score_candidate(sig, f, scale) for f in faces),
        key=lambda c: (-c.score, c.face_id),
    )
    by_id = {f.face_id: f for f in faces}
    shown = [c for c in scored if c.score >= confidence * 0.5][:6]
    if not scored or scored[0].score < confidence:
        best = scored[0].score if scored else 0.0
        return RemapResult(
            RemapOutcome.MISSING,
            None,
            best,
            scored[1].score if len(scored) > 1 else None,
            shown,
            f"no face matches well enough (best {best:.2f}, needs {confidence:.2f})",
        )
    best, runner = scored[0], (scored[1] if len(scored) > 1 else None)
    if runner is not None and best.score - runner.score < margin:
        return RemapResult(
            RemapOutcome.AMBIGUOUS,
            None,
            best.score,
            runner.score,
            [c for c in shown if best.score - c.score < margin] or shown,
            f"{best.face_id} and {runner.face_id} match about equally "
            f"({best.score:.2f} and {runner.score:.2f})",
        )
    return RemapResult(
        RemapOutcome.UNIQUE,
        by_id[best.face_id],
        best.score,
        runner.score if runner else None,
        shown,
        f"{best.face_id} matches ({best.score:.2f}"
        + (f", next best {runner.score:.2f})" if runner else ")"),
    )


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaceFrame:
    origin: Vec3
    u_axis: Vec3
    v_axis: Vec3
    normal: Vec3


_WORLD_AXES: tuple[Vec3, ...] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _clean(v: Vec3) -> Vec3:
    out = tuple(0.0 if abs(c) < 1e-12 else c for c in v)
    return (out[0], out[1], out[2])


def frame_from_face(face: PlanarFace) -> FaceFrame:
    """The right-handed plane frame a face gives. See the module docstring."""
    n = _unit(face.normal)
    c = face.centroid
    origin = _sub(c, _mul(n, dot(n, c) - face.offset))
    hints: list[Vec3] = []
    edge = face.longest_edge or {}
    direction = edge.get("direction")
    if isinstance(direction, (list, tuple)) and len(direction) == 3:
        hints.append((float(direction[0]), float(direction[1]), float(direction[2])))
    hints.extend(_WORLD_AXES)
    u: Vec3 | None = None
    for hint in hints:
        projected = _sub(hint, _mul(n, dot(hint, n)))
        if norm(projected) > 1e-6:
            u = _unit(projected)
            break
    if u is None:  # pragma: no cover - three world axes cannot all be parallel to n
        raise ValueError("no in-plane direction for the face")
    v = _unit(cross(n, u))
    # Re-derive U from V and N so the frame is orthonormal to rounding.
    u = _unit(cross(v, n))
    return FaceFrame(_clean(origin), _clean(u), _clean(v), _clean(n))


# ---------------------------------------------------------------------------
# Semantic tags from a revision's build source
# ---------------------------------------------------------------------------


@dataclass
class _Expected:
    tag: str
    normal: Vec3
    point: Vec3
    segment: tuple[Vec3, Vec3] | None = None


def _sketch_frame(sketch: dict[str, Any]) -> tuple[Vec3, Vec3, Vec3, Vec3]:
    frame = sketch.get("frame")
    if isinstance(frame, dict):
        o = tuple(float(c) for c in frame["origin"])
        u = _unit(tuple(float(c) for c in frame["x_dir"]))  # type: ignore[arg-type]
        n = _unit(tuple(float(c) for c in frame["normal"]))  # type: ignore[arg-type]
        return (o[0], o[1], o[2]), u, cross(n, u), n
    plane = PrincipalPlane(str(sketch.get("plane") or "XY"))
    u, v, n = PRINCIPAL_FRAMES[plane]
    return _mul(n, float(sketch.get("offset") or 0.0)), u, v, n


def _segments(el: dict[str, Any]) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    """``(name, a, b)`` straight edges of one plane-space sketch element."""
    kind, eid = el.get("type"), str(el.get("id") or "")
    if not eid or el.get("construction"):
        return []
    if kind == "line":
        return [(eid, tuple(el["start"]), tuple(el["end"]))]  # type: ignore[list-item]
    if kind == "rectangle":
        x, y = float(el["origin"][0]), float(el["origin"][1])
        w, h = float(el["width"]), float(el["height"])
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return [(f"{eid}.{i}", corners[i], corners[(i + 1) % 4]) for i in range(4)]
    return []


def expected_faces(source: dict[str, Any] | None) -> list[_Expected]:
    """The faces a recorded design-IR build is known to make, with their tags."""
    if not isinstance(source, dict) or source.get("kind") != "design_ir":
        return []
    doc = source.get("design_ir") or {}
    entities = [e for e in doc.get("entities") or [] if isinstance(e, dict)]
    by_id = {str(e.get("id")): e for e in entities}
    origin_ref = str(source.get("parametric_sketch_ref") or "")
    prefix = origin_ref.split("@", 1)[0] if origin_ref else ""
    out: list[_Expected] = []
    for pad in entities:
        if pad.get("op") != "pad":
            continue
        sketch = by_id.get(str(pad.get("sketch_ref")))
        if not sketch or sketch.get("op") != "sketch":
            continue
        try:
            o, u, v, n = _sketch_frame(sketch)
        except (KeyError, TypeError, ValueError):
            continue
        depth = float(pad.get("depth") or 0.0)
        if depth <= 0:
            continue
        direction = _mul(n, -1.0 if pad.get("reversed") else 1.0)
        start = _sub(o, _mul(direction, depth / 2.0)) if pad.get("midplane") else o
        end = _add(start, _mul(direction, depth))
        head = f"{prefix or sketch.get('id')}:{source.get('feature_id') or pad.get('id')}"
        out.append(_Expected(f"{head}:bottom", _mul(direction, -1.0), start))
        out.append(_Expected(f"{head}:top", direction, end))
        for el in sketch.get("elements") or []:
            if not isinstance(el, dict):
                continue
            for name, a, b in _segments(el):
                pa = _add(start, _add(_mul(u, float(a[0])), _mul(v, float(a[1]))))
                pb = _add(start, _add(_mul(u, float(b[0])), _mul(v, float(b[1]))))
                side = cross(_sub(pb, pa), direction)
                if norm(side) <= 1e-12:
                    continue
                out.append(_Expected(f"{head}:side:{name}", _unit(side), pa, (pa, pb)))
    return out


def tag_faces(
    faces: list[PlanarFace], source: dict[str, Any] | None, part_size: float | None = None
) -> list[PlanarFace]:
    """``faces`` with ``semantic_tag`` set where the build source says what made them."""
    expected = expected_faces(source)
    if not expected:
        return faces
    size = part_size or part_size_of(faces)
    tol = max(1e-4, 1e-6 * size)
    out: list[PlanarFace] = []
    for face in faces:
        n = _unit(face.normal)
        tag: str | None = None
        for exp in expected:
            if exp.segment is None:
                if dot(n, exp.normal) < 1 - 1e-6:
                    continue
                if abs(dot(n, exp.point) - face.offset) > tol:
                    continue
                tag = exp.tag
                break
            # A side face: perpendicular to the sweep, through the swept edge.
            a, b = exp.segment
            if abs(abs(dot(n, exp.normal)) - 1.0) > 1e-6:
                continue
            if abs(dot(n, a) - face.offset) > tol or abs(dot(n, b) - face.offset) > tol:
                continue
            ab = _sub(b, a)
            t = dot(_sub(face.centroid, a), ab) / max(dot(ab, ab), 1e-12)
            if -1e-6 <= t <= 1 + 1e-6:
                tag = exp.tag
                break
        out.append(face.model_copy(update={"semantic_tag": tag}) if tag else face)
    return out


__all__ = [
    "CONFIDENCE",
    "MARGIN",
    "CandidateScore",
    "FaceFrame",
    "FaceSignature",
    "PlanarFace",
    "RemapOutcome",
    "RemapResult",
    "expected_faces",
    "frame_from_face",
    "part_size_of",
    "remap",
    "score_candidate",
    "signature_of",
    "tag_faces",
]
