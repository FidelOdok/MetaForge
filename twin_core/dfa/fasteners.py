"""Bought fasteners placed in their holes, and joints left without one.

The desk lamp's release had no hinge pin, no screw and no nut anywhere: the
pin holes were empty and the cover and diffuser were held by nothing. Each
part was checked on its own; the assembly never held the parts that join
them. An assembly built with ``twin.build_assembly`` can now hold fasteners
by designation (``ISO 4762 M2.5x8``, ``M2.5x6``, ``ISO 2338 4x30``,
``ISO 7089 M4``), each placed in its holes, and the check holds each one to
the holes it goes through:

**Placement.** The fastener's axis passes through the centre of each hole
it names, parallel to the hole. Its own frame: the origin is the underside
of the head (a pin's end), the shank runs along its -Z.

**Shank against the hole.** A clearance hole at least the ISO 273 fine
clearance for the size (smaller is tight, smaller than the shank does not
pass); a tapped hole smaller than the screw and not far below its tap drill
(the nominal diameter less one pitch); a pilot for a thread-forming screw
in plastic 0.7 to 0.9 of the nominal diameter; a pin in a clearance (it
turns) or a press fit.

**Length against the stack.** The grip is the clamped thickness (each
clearance hole's ``length_mm``, less a counterbore's depth, plus washers).
Into a tapped or thread-forming hole the screw must engage at least
``min_engagement_mm`` (one diameter tapped, 1.5 thread-forming) and not
bottom out in a blind hole's ``depth_mm``; through a nut it must pass the
nut by a pitch. A pin must reach through every member it joins.

**Head against the counterbore or face.** A counterbore at least the
head's diameter, deep enough for the head when ``flush``; with no
counterbore, the face or boss it bears on (``face_diameter_mm``) at least
the head's diameter, and the first hole smaller than the head.

**Unfastened joints.** A hole pattern a mating contract lists (a board on
its bosses, a cover on its base), or a revolute joint, with no fastener on
it is a medium finding "unfastened joint", unless it is marked ``retention:
press_fit`` or ``snap``.

Pure: the standard part dimensions come through a lookup.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from twin_core.dfa.geometry import (
    Matrix,
    Vec,
    apply,
    apply_dir,
    dot,
    location_matrix,
    matrix_from,
    norm,
    sub,
    unit,
)

#: ISO 273 clearance holes for metric fasteners (fine, medium, coarse), mm.
ISO273: dict[float, tuple[float, float, float]] = {
    1.6: (1.7, 1.8, 2.0),
    2.0: (2.2, 2.4, 2.6),
    2.5: (2.7, 2.9, 3.1),
    3.0: (3.2, 3.4, 3.6),
    4.0: (4.3, 4.5, 4.8),
    5.0: (5.3, 5.5, 5.8),
    6.0: (6.4, 6.6, 7.0),
    8.0: (8.4, 9.0, 10.0),
    10.0: (10.5, 11.0, 12.0),
    12.0: (13.0, 13.5, 14.5),
}
ISO273_SOURCE = "ISO 273 clearance holes for bolts and screws (fine series)"

#: Retention that needs no fastener: a press fit or a snap.
NO_FASTENER_RETENTION: frozenset[str] = frozenset({"press_fit", "press", "snap", "snap_fit"})

HOLE_FITS: tuple[str, ...] = ("clearance", "tapped", "thread_forming", "press")
DEFAULT_PLACEMENT_TOLERANCE_MM = 0.3
#: A fastener and a joint axis this close are the same axis.
DEFAULT_JOINT_AXIS_TOLERANCE_MM = 0.5
MAX_AXIS_ANGLE_DEG = 2.0
#: A pin in a clearance hole looser than this turns sloppily (low).
PIN_LOOSE_MM = 0.5
DEFAULT_PRESS_INTERFERENCE_MM = 0.1

#: ``designation -> library entry`` (``twin_core.library.standard_parts``).
DesignationLookup = Callable[[str], dict[str, Any]]


class FastenerSpecError(ValueError):
    """A fastener that cannot be read; the message names the field."""


def retention_word(raw: Any) -> str:
    return str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def exempt(retention: Any) -> bool:
    """A joint held by a press fit or a snap needs no fastener."""
    return retention_word(retention) in NO_FASTENER_RETENTION


def clearance_series(d: float) -> tuple[float, float, float] | None:
    return ISO273.get(round(d, 2))


@dataclass
class HoleSpec:
    """One hole a fastener passes through, in the order from its head."""

    part: str
    diameter_mm: float
    fit: str = "clearance"
    #: Material it clamps along the axis (clearance holes; a pin's member).
    length_mm: float | None = None
    #: The blind depth there is to thread into (tapped, thread-forming).
    depth_mm: float | None = None
    #: A point on the hole's axis and its direction, in the part's frame.
    center: Vec | None = None
    axis: Vec = (0.0, 0.0, 1.0)
    counterbore_diameter_mm: float | None = None
    counterbore_depth_mm: float | None = None
    #: The face or boss the head bears on.
    face_diameter_mm: float | None = None
    #: The head must sit at or below the face (in its counterbore).
    flush: bool = False

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"part": self.part, "diameter_mm": self.diameter_mm, "fit": self.fit}
        for key in (
            "length_mm",
            "depth_mm",
            "counterbore_diameter_mm",
            "counterbore_depth_mm",
            "face_diameter_mm",
        ):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.center is not None:
            out["center"] = list(self.center)
            out["axis"] = list(self.axis)
        if self.flush:
            out["flush"] = True
        return out


@dataclass
class StandardItem:
    """A washer or a nut in a fastener's stack."""

    designation: str
    title: str
    thickness_mm: float
    family: str


@dataclass
class FastenerSpec:
    name: str
    designation: str
    title: str
    #: ``screw`` or ``pin``.
    kind: str
    diameter_mm: float
    length_mm: float
    head_diameter_mm: float | None = None
    head_height_mm: float | None = None
    pitch_mm: float | None = None
    matrix: Matrix | None = None
    holes: list[HoleSpec] = field(default_factory=list)
    washers: list[StandardItem] = field(default_factory=list)
    nut: StandardItem | None = None
    min_engagement_mm: float | None = None
    max_interference_mm: float = DEFAULT_PRESS_INTERFERENCE_MM
    geometry: dict[str, Any] = field(default_factory=dict)

    def axis_world(self) -> tuple[Vec, Vec] | None:
        """``(origin, direction)`` of the shank in the assembly, or ``None``."""
        if self.matrix is None:
            return None
        return apply(self.matrix, (0.0, 0.0, 0.0)), unit(apply_dir(self.matrix, (0.0, 0.0, -1.0)))

    def purchased(self) -> list[str]:
        """The designations bought for it: itself, its washers and its nut."""
        return [self.designation, *(w.designation for w in self.washers)] + (
            [self.nut.designation] if self.nut else []
        )


def _num(raw: dict[str, Any], key: str, where: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise FastenerSpecError(f"{where}: '{key}' must be a number")
    return float(value)


def _vec(raw: Any, where: str) -> Vec:
    if isinstance(raw, list | tuple) and len(raw) == 2:
        raw = [raw[0], raw[1], 0.0]
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise FastenerSpecError(f"{where} must be [x, y, z]")
    try:
        x, y, z = (float(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise FastenerSpecError(f"{where} must be three numbers") from exc
    return (x, y, z)


def _spec(entry: dict[str, Any], name: str) -> float | None:
    spec = (entry.get("specs") or {}).get(name)
    if isinstance(spec, dict) and isinstance(spec.get("value"), int | float):
        return float(spec["value"])
    return None


def _standard(lookup: DesignationLookup, text: str, where: str) -> dict[str, Any]:
    try:
        return lookup(text)
    except ValueError as exc:
        raise FastenerSpecError(f"{where}: {exc}") from exc


def _item(lookup: DesignationLookup, text: Any, where: str) -> StandardItem:
    if not isinstance(text, str) or not text.strip():
        raise FastenerSpecError(f"{where} is a designation such as 'ISO 7089 M4'")
    entry = _standard(lookup, text.strip(), where)
    thick = _spec(entry, "thickness_mm") or _spec(entry, "height_mm")
    if thick is None:
        raise FastenerSpecError(f"{where}: {text} has no thickness or height")
    return StandardItem(text.strip(), str(entry.get("title") or text), thick, str(entry["family"]))


def parse_fastener(
    raw: dict[str, Any],
    resolve: Callable[[str], str],
    lookup: DesignationLookup,
    where: str,
    index: int = 0,
) -> FastenerSpec:
    """One fastener as ``twin.build_assembly`` and ``twin.check_assembly`` take it."""
    if not isinstance(raw, dict):
        raise FastenerSpecError(f"{where} must be an object")
    designation = raw.get("designation")
    if not isinstance(designation, str) or not designation.strip():
        raise FastenerSpecError(
            f"{where}: 'designation' is required, e.g. 'ISO 4762 M4x25', 'M2.5x6' or "
            "'ISO 2338 4x30'"
        )
    designation = designation.strip()
    entry = _standard(lookup, designation, where)
    family = str(entry.get("family") or "")
    if family not in ("screw", "pin"):
        raise FastenerSpecError(
            f"{where}: {designation} is a {family or 'part'}; a fastener is a screw or a pin "
            "(washers and nuts go in its 'washers' and 'nut')"
        )
    d = _spec(entry, "nominal_diameter_mm")
    length = _spec(entry, "length_mm")
    if d is None or length is None:
        raise FastenerSpecError(f"{where}: {designation} has no diameter or length")
    matrix: Matrix | None = None
    try:
        if raw.get("matrix") is not None:
            matrix = matrix_from(raw["matrix"])
        elif raw.get("location") is not None:
            if not isinstance(raw["location"], dict):
                raise ValueError("location must be an object")
            matrix = location_matrix(raw["location"])
    except ValueError as exc:
        raise FastenerSpecError(f"{where}: {exc}") from exc
    holes: list[HoleSpec] = []
    raw_holes = raw.get("holes") or []
    if not isinstance(raw_holes, list):
        raise FastenerSpecError(f"{where}.holes must be a list")
    for j, h in enumerate(raw_holes):
        hw = f"{where}.holes[{j}]"
        if not isinstance(h, dict):
            raise FastenerSpecError(f"{hw} must be an object")
        fit = str(h.get("fit") or "clearance").strip().lower().replace("-", "_")
        if fit == "press_fit":
            fit = "press"
        if fit not in HOLE_FITS:
            raise FastenerSpecError(f"{hw}: 'fit' is one of {', '.join(HOLE_FITS)}")
        diameter = _num(h, "diameter_mm", hw)
        if diameter is None:
            raise FastenerSpecError(f"{hw}: 'diameter_mm' is required")
        cb = h.get("counterbore") if isinstance(h.get("counterbore"), dict) else {}
        holes.append(
            HoleSpec(
                part=resolve(str(h.get("part"))),
                diameter_mm=diameter,
                fit=fit,
                length_mm=_num(h, "length_mm", hw),
                depth_mm=_num(h, "depth_mm", hw),
                center=_vec(h["center"], f"{hw}.center") if h.get("center") is not None else None,
                axis=_vec(h.get("axis") or [0, 0, 1], f"{hw}.axis"),
                counterbore_diameter_mm=_num(cb, "diameter_mm", f"{hw}.counterbore")
                if cb
                else _num(h, "counterbore_diameter_mm", hw),
                counterbore_depth_mm=_num(cb, "depth_mm", f"{hw}.counterbore")
                if cb
                else _num(h, "counterbore_depth_mm", hw),
                face_diameter_mm=_num(h, "face_diameter_mm", hw),
                flush=bool(h.get("flush")),
            )
        )
    washers_raw = raw.get("washers") or []
    if not isinstance(washers_raw, list):
        raise FastenerSpecError(f"{where}.washers must be a list of designations")
    washers = [_item(lookup, w, f"{where}.washers[{k}]") for k, w in enumerate(washers_raw)]
    nut = _item(lookup, raw["nut"], f"{where}.nut") if raw.get("nut") else None
    if nut is not None and nut.family != "nut":
        raise FastenerSpecError(f"{where}.nut: {nut.designation} is not a nut")
    pitch = _spec(entry, "thread_pitch_mm")
    return FastenerSpec(
        name=str(raw.get("name") or f"{designation} #{index + 1}"),
        designation=designation,
        title=str(entry.get("title") or designation),
        kind=family,
        diameter_mm=d,
        length_mm=length,
        head_diameter_mm=_spec(entry, "head_diameter_mm"),
        head_height_mm=_spec(entry, "head_height_mm"),
        pitch_mm=pitch,
        matrix=matrix,
        holes=holes,
        washers=washers,
        nut=nut,
        min_engagement_mm=_num(raw, "min_engagement_mm", where),
        max_interference_mm=_num(raw, "max_interference_mm", where)
        or DEFAULT_PRESS_INTERFERENCE_MM,
        geometry=dict(entry.get("geometry") or {}),
    )


# ---------------------------------------------------------------------------
# What is wrong with one fastener
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str
    what: str
    message: str
    values: dict[str, Any] = field(default_factory=dict)


def _r(v: float) -> float:
    return round(v, 3)


def _lateral(point: Vec, origin: Vec, direction: Vec) -> float:
    d = sub(point, origin)
    along = dot(d, direction)
    return norm(
        (d[0] - along * direction[0], d[1] - along * direction[1], d[2] - along * direction[2])
    )


def _angle(a: Vec, b: Vec) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(dot(unit(a), unit(b)))))))


def placement_issues(
    f: FastenerSpec, frames: dict[str, Matrix], tolerance_mm: float
) -> tuple[list[Issue], int]:
    """Is the fastener in each hole that names a centre; and how many were checked."""
    axis = f.axis_world()
    if axis is None:
        return [], 0
    origin, direction = axis
    issues: list[Issue] = []
    checked = 0
    for h in f.holes:
        if h.center is None or h.part not in frames:
            continue
        checked += 1
        centre = apply(frames[h.part], h.center)
        hole_dir = apply_dir(frames[h.part], h.axis)
        off = _lateral(centre, origin, direction)
        angle = _angle(direction, hole_dir)
        if off > tolerance_mm or angle > MAX_AXIS_ANGLE_DEG:
            words = []
            if off > tolerance_mm:
                words.append(f"{off:.2f} mm off its centre")
            if angle > MAX_AXIS_ANGLE_DEG:
                words.append(f"{angle:.0f} degrees off its axis")
            issues.append(
                Issue(
                    "high",
                    f"place:{h.part}",
                    f"{f.name} ({f.designation}) is not in its hole in {h.part}: "
                    + " and ".join(words),
                    {"offset_mm": _r(off), "angle_deg": _r(angle), "tolerance_mm": tolerance_mm},
                )
            )
    return issues, checked


def _diameter_issues(f: FastenerSpec) -> list[Issue]:
    d = f.diameter_mm
    out: list[Issue] = []
    first_clear = next((h for h in f.holes if h.fit == "clearance"), None)
    for h in f.holes:
        hd = h.diameter_mm
        tag = f"hole:{h.part}"
        if f.kind == "pin":
            if h.fit == "press":
                interference = d - hd
                if interference < 0:
                    out.append(
                        Issue(
                            "high",
                            tag,
                            f"{f.name}: the {hd:g} mm hole in {h.part} is larger than the "
                            f"{d:g} mm pin, so a press fit does not hold it",
                            {"hole_mm": hd, "pin_mm": d},
                        )
                    )
                elif interference > f.max_interference_mm + 1e-9:
                    out.append(
                        Issue(
                            "medium",
                            tag,
                            f"{f.name}: {interference:g} mm of press fit in {h.part} is over "
                            f"{f.max_interference_mm:g} mm and may split the part",
                            {"interference_mm": _r(interference)},
                        )
                    )
            elif hd < d - 1e-9:
                out.append(
                    Issue(
                        "high",
                        tag,
                        f"{f.name}: the {d:g} mm pin does not pass the {hd:g} mm hole in {h.part}",
                        {"hole_mm": hd, "pin_mm": d},
                    )
                )
            elif hd - d > PIN_LOOSE_MM:
                out.append(
                    Issue(
                        "low",
                        tag,
                        f"{f.name}: the {hd:g} mm hole in {h.part} is {hd - d:g} mm over the pin",
                        {"hole_mm": hd, "pin_mm": d},
                    )
                )
            continue
        if h.fit == "clearance":
            series = clearance_series(d)
            if hd < d - 1e-9:
                out.append(
                    Issue(
                        "high",
                        tag,
                        f"{f.name}: the M{d:g} shank does not pass the {hd:g} mm hole in {h.part}",
                        {"hole_mm": hd, "nominal_mm": d},
                    )
                )
            elif series is not None and hd < series[0] - 1e-9:
                out.append(
                    Issue(
                        "medium",
                        tag,
                        f"{f.name}: the {hd:g} mm hole in {h.part} is under the {series[0]:g} mm "
                        f"fine clearance for M{d:g}",
                        {"hole_mm": hd, "fine_mm": series[0], "source": ISO273_SOURCE},
                    )
                )
            if (
                h is first_clear
                and not f.washers
                and h.counterbore_diameter_mm is None
                and f.head_diameter_mm is not None
                and hd >= f.head_diameter_mm - 1e-9
            ):
                out.append(
                    Issue(
                        "high",
                        tag,
                        f"{f.name}: the {f.head_diameter_mm:g} mm head pulls through the "
                        f"{hd:g} mm hole in {h.part}",
                        {"hole_mm": hd, "head_mm": f.head_diameter_mm},
                    )
                )
        elif h.fit == "tapped":
            pitch = f.pitch_mm or 0.0
            if hd >= d - 1e-9:
                out.append(
                    Issue(
                        "high",
                        tag,
                        f"{f.name}: the {hd:g} mm hole in {h.part} is not smaller than M{d:g}; "
                        "there is nothing to tap a thread into",
                        {"hole_mm": hd, "nominal_mm": d},
                    )
                )
            elif pitch and hd < d - 1.3 * pitch:
                out.append(
                    Issue(
                        "medium",
                        tag,
                        f"{f.name}: the {hd:g} mm hole in {h.part} is under the M{d:g} tap "
                        f"drill ({d - pitch:g} mm)",
                        {"hole_mm": hd, "tap_drill_mm": _r(d - pitch)},
                    )
                )
        elif h.fit == "thread_forming":
            ratio = hd / d
            if hd >= d - 1e-9:
                out.append(
                    Issue(
                        "high",
                        tag,
                        f"{f.name}: the {hd:g} mm pilot in {h.part} is not smaller than M{d:g}; "
                        "the screw forms no thread",
                        {"hole_mm": hd, "nominal_mm": d},
                    )
                )
            elif ratio > 0.9:
                out.append(
                    Issue(
                        "medium",
                        tag,
                        f"{f.name}: the {hd:g} mm pilot in {h.part} is {ratio:.2f} of M{d:g}; "
                        "over 0.9 the thread barely engages",
                        {"hole_mm": hd, "ratio": _r(ratio)},
                    )
                )
            elif ratio < 0.7:
                out.append(
                    Issue(
                        "medium",
                        tag,
                        f"{f.name}: the {hd:g} mm pilot in {h.part} is {ratio:.2f} of M{d:g}; "
                        "under 0.7 the screw may split the boss",
                        {"hole_mm": hd, "ratio": _r(ratio)},
                    )
                )
        elif h.fit == "press":
            out.append(
                Issue(
                    "high",
                    tag,
                    f"{f.name}: a screw does not go in a press-fit hole ({h.part}); use "
                    "clearance, tapped or thread_forming",
                )
            )
    return out


def _length_issues(f: FastenerSpec) -> tuple[list[Issue], str | None]:
    """Issues with the length, and why it was not checked when it was not."""
    out: list[Issue] = []
    if not f.holes:
        return out, f"{f.name}: no holes named"
    if f.kind == "pin":
        spans = [h.length_mm for h in f.holes]
        if any(s is None for s in spans):
            return out, f"{f.name}: a hole has no length_mm"
        span = sum(s for s in spans if s is not None)
        if f.length_mm + 1e-9 < span:
            out.append(
                Issue(
                    "high",
                    "length",
                    f"{f.name}: the {f.length_mm:g} mm pin does not reach through the "
                    f"{span:g} mm of members it joins",
                    {"length_mm": f.length_mm, "span_mm": _r(span)},
                )
            )
        return out, None
    clamped = [h for h in f.holes if h.fit == "clearance"]
    last = f.holes[-1]
    if any(h.length_mm is None for h in clamped):
        return out, f"{f.name}: a clearance hole has no length_mm (the thickness it clamps)"
    grip = sum(h.length_mm or 0.0 for h in clamped) + sum(w.thickness_mm for w in f.washers)
    first = f.holes[0]
    if first.counterbore_depth_mm:
        grip -= first.counterbore_depth_mm
    d = f.diameter_mm
    if last.fit in ("tapped", "thread_forming"):
        need = (
            f.min_engagement_mm
            if f.min_engagement_mm is not None
            else d * (1.0 if last.fit == "tapped" else 1.5)
        )
        engaged = f.length_mm - grip
        if engaged + 1e-9 < need:
            out.append(
                Issue(
                    "high",
                    "length",
                    f"{f.name}: {f.length_mm:g} mm long through a {grip:g} mm grip leaves "
                    f"{engaged:g} mm in the thread in {last.part}; it needs {need:g} mm",
                    {"length_mm": f.length_mm, "grip_mm": _r(grip), "engaged_mm": _r(engaged)},
                )
            )
        if last.depth_mm is not None and engaged > last.depth_mm + 1e-9:
            out.append(
                Issue(
                    "high",
                    "bottoms_out",
                    f"{f.name}: {engaged:g} mm past the grip, but the hole in {last.part} is "
                    f"{last.depth_mm:g} mm deep; the screw bottoms out",
                    {"engaged_mm": _r(engaged), "depth_mm": last.depth_mm},
                )
            )
        return out, None
    if f.nut is not None:
        through = grip + f.nut.thickness_mm
        if f.length_mm + 1e-9 < through:
            out.append(
                Issue(
                    "high",
                    "length",
                    f"{f.name}: {f.length_mm:g} mm does not reach through the {grip:g} mm grip "
                    f"and the {f.nut.thickness_mm:g} mm nut",
                    {"length_mm": f.length_mm, "needed_mm": _r(through)},
                )
            )
        elif f.pitch_mm and f.length_mm + 1e-9 < through + f.pitch_mm:
            out.append(
                Issue(
                    "medium",
                    "length",
                    f"{f.name}: less than one thread passes the nut",
                    {"length_mm": f.length_mm, "needed_mm": _r(through + f.pitch_mm)},
                )
            )
        return out, None
    out.append(
        Issue(
            "high",
            "held",
            f"{f.name}: every hole is a clearance hole and there is no nut; nothing holds it "
            "(name a tapped or thread_forming hole, or a nut)",
        )
    )
    return out, None


def _head_issues(f: FastenerSpec) -> list[Issue]:
    if f.kind != "screw" or not f.holes or f.head_diameter_mm is None:
        return []
    out: list[Issue] = []
    first = f.holes[0]
    dk, k = f.head_diameter_mm, f.head_height_mm or 0.0
    if first.counterbore_diameter_mm is not None:
        cb = first.counterbore_diameter_mm
        if cb < dk - 1e-9:
            out.append(
                Issue(
                    "high",
                    "head",
                    f"{f.name}: the {dk:g} mm head does not fit the {cb:g} mm counterbore in "
                    f"{first.part}",
                    {"head_mm": dk, "counterbore_mm": cb},
                )
            )
        depth = first.counterbore_depth_mm or 0.0
        if first.flush and depth + 1e-9 < k:
            out.append(
                Issue(
                    "medium",
                    "head",
                    f"{f.name}: the head stands {k - depth:g} mm proud of {first.part} (the "
                    f"counterbore is {depth:g} mm deep, the head {k:g} mm)",
                    {"head_height_mm": k, "counterbore_depth_mm": depth},
                )
            )
    elif first.face_diameter_mm is not None and first.face_diameter_mm < dk - 1e-9:
        out.append(
            Issue(
                "medium",
                "head",
                f"{f.name}: the {dk:g} mm head overhangs the {first.face_diameter_mm:g} mm face "
                f"of {first.part}",
                {"head_mm": dk, "face_mm": first.face_diameter_mm},
            )
        )
    return out


def fastener_issues(
    f: FastenerSpec, frames: dict[str, Matrix], tolerance_mm: float
) -> tuple[list[Issue], list[str]]:
    """Every issue with one fastener, and notes on what was not checked."""
    notes: list[str] = []
    placed, checked = placement_issues(f, frames, tolerance_mm)
    if f.matrix is None:
        notes.append(f"{f.name}: no placement")
    elif not checked:
        notes.append(f"{f.name}: no hole centre to check its placement against")
    length, why = _length_issues(f)
    if why:
        notes.append(why)
    return placed + _diameter_issues(f) + length + _head_issues(f), notes


def on_axis(
    fasteners: list[FastenerSpec], point: Vec, direction: Vec, tolerance_mm: float
) -> FastenerSpec | None:
    """The first fastener whose shank lies along the line ``point`` + t ``direction``."""
    for f in fasteners:
        axis = f.axis_world()
        if axis is None:
            continue
        origin, fdir = axis
        if _angle(fdir, direction) > MAX_AXIS_ANGLE_DEG * 2.5:
            continue
        if _lateral(origin, point, unit(direction)) <= tolerance_mm:
            return f
    return None


__all__ = [
    "DEFAULT_JOINT_AXIS_TOLERANCE_MM",
    "DEFAULT_PLACEMENT_TOLERANCE_MM",
    "HOLE_FITS",
    "ISO273",
    "NO_FASTENER_RETENTION",
    "FastenerSpec",
    "FastenerSpecError",
    "HoleSpec",
    "Issue",
    "StandardItem",
    "exempt",
    "fastener_issues",
    "on_axis",
    "parse_fastener",
    "retention_word",
]
