"""Design for assembly: does every part of an assembly fit the others.

Each check reads the assembly's placed bodies and what the caller (or the
interface contracts) says about the features that mate, and records findings,
each with a severity and the part revisions involved:

``revisions``
    Every body is the current revision of its part. A body from an older
    revision is high: the check is about parts that are no longer made.
``interference``
    No two bodies overlap by more than a small tolerance. High.
``clearance``
    A minimum gap where a contract or the caller says so. Medium.
``hole_pattern``
    Holes on two parts line up (a board's mounting holes on the bosses or
    cover holes), each within a tolerance in the plane across the fastener
    axis. A hole with no partner is a miss too. High.
``outline_fit``
    A part's outline fits the pocket, lip or opening it sits in, with the
    stated clearance per side. High.
``connector``
    An edge connector's mating face is within ``max_setback_mm`` of its
    board's edge, and it reaches its slot: its face within ``max_gap_mm`` of
    the slot, centred within the tolerance and facing it. Medium.
``stack``
    A stack of parts (a tongue and its washers in a clevis, washers under a
    screw head) fits the gap it goes in, and a fastener is long enough for
    its grip. Thicknesses of standard washers, nuts and screw lengths come
    from the standard part tables. High.
``motion``
    A revolute joint with stated limits is swept in steps and every sample
    checked for interference between the moving and the fixed bodies.
    Medium.
``seat``
    A part seated in another (a diffuser in a head) is smaller than its seat
    by the stated clearance, and has a retention feature or fastener on
    record. Oversize is high, no retention medium.

A check with nothing to check reads ``not_assessed``. It blocks only when it
is required: the revisions and interference checks always are, and the
gateway makes the hole pattern and outline checks required when a board sits
in the assembly.

Pure apart from the :class:`~twin_core.dfa.geometry.GeometryEngine` it is
given.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from observability.tracing import get_tracer
from twin_core.dfa.geometry import (
    DEFAULT_TOLERANCE_MM3,
    Body,
    EngineResult,
    GeometryEngine,
    Matrix,
    Pose,
    Vec,
    apply,
    apply_dir,
    as_vec,
    axis_rotation,
    compose,
    dot,
    norm,
    sub,
    unit,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.dfa.checks")

Severity = Literal["high", "medium", "low"]
CheckStatus = Literal["passed", "failed", "not_assessed"]

#: Severities that block the release until fixed or accepted.
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"high", "medium"})

CHECK_LABELS: dict[str, str] = {
    "revisions": "Every body is the current revision of its part",
    "interference": "No two bodies overlap",
    "clearance": "Stated minimum gaps are kept",
    "hole_pattern": "Hole patterns line up",
    "outline_fit": "Outlines fit their pockets",
    "connector": "Edge connectors reach their slots",
    "stack": "Fastener and washer stacks fit their gaps",
    "motion": "Joints move through their range without collision",
    "seat": "Seated parts fit and are retained",
}
CHECK_ORDER: tuple[str, ...] = tuple(CHECK_LABELS)
#: Checks that always have to run.
ALWAYS_REQUIRED: frozenset[str] = frozenset({"revisions", "interference"})

#: Default limits when the caller gives none.
DEFAULT_HOLE_TOLERANCE_MM = 0.3
DEFAULT_MAX_SETBACK_MM = 1.0
DEFAULT_MAX_GAP_MM = 1.0
DEFAULT_SLOT_TOLERANCE_MM = 0.5
DEFAULT_MOTION_STEPS = 12
MAX_MOTION_STEPS = 72
#: A connector facing more than this many degrees off its slot does not reach it.
MAX_CONNECTOR_ANGLE_DEG = 5.0


class AssemblySpecError(ValueError):
    """A check specification that cannot be read; the message says which field."""


@dataclass
class Finding:
    id: str
    check: str
    severity: Severity
    message: str
    #: ``KEY@n`` of each part revision involved.
    parts: list[str]
    values: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "parts": list(self.parts),
            "values": dict(self.values),
        }


@dataclass
class CheckResult:
    check: str
    status: CheckStatus
    required: bool
    detail: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def label(self) -> str:
        return CHECK_LABELS.get(self.check, self.check)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "label": self.label,
            "status": self.status,
            "required": self.required,
            "detail": self.detail,
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass
class AssemblyReport:
    """Every check of one assembly revision."""

    assembly_ref: str
    part_refs: list[str]
    checks: list[CheckResult]

    def findings(self) -> list[Finding]:
        return [f for c in self.checks for f in c.findings]

    def open_findings(self, accepted: set[str] | frozenset[str] = frozenset()) -> list[Finding]:
        """High and medium findings nobody accepted."""
        return [
            f for f in self.findings() if f.severity in BLOCKING_SEVERITIES and f.id not in accepted
        ]

    def missing_required(self) -> list[CheckResult]:
        return [c for c in self.checks if c.required and c.status == "not_assessed"]

    def outcome(self, accepted: set[str] | frozenset[str] = frozenset()) -> str:
        if self.open_findings(accepted):
            return "issues_found"
        if self.missing_required():
            return "incomplete"
        return "passed"

    def counts(self) -> dict[str, int]:
        out = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings():
            out[f.severity] += 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "assembly_ref": self.assembly_ref,
            "part_refs": list(self.part_refs),
            "outcome": self.outcome(),
            "counts": self.counts(),
            "checks": [c.as_dict() for c in self.checks],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AssemblyReport:
        checks = []
        for c in raw.get("checks") or []:
            findings = [
                Finding(
                    id=str(f.get("id")),
                    check=str(f.get("check")),
                    severity=f.get("severity") or "low",
                    message=str(f.get("message") or ""),
                    parts=[str(p) for p in f.get("parts") or []],
                    values=dict(f.get("values") or {}),
                )
                for f in c.get("findings") or []
            ]
            checks.append(
                CheckResult(
                    check=str(c.get("check")),
                    status=c.get("status") or "not_assessed",
                    required=bool(c.get("required")),
                    detail=str(c.get("detail") or ""),
                    findings=findings,
                )
            )
        return cls(
            assembly_ref=str(raw.get("assembly_ref") or ""),
            part_refs=[str(p) for p in raw.get("part_refs") or []],
            checks=checks,
        )


# ---------------------------------------------------------------------------
# The specification
# ---------------------------------------------------------------------------


def _num(raw: dict[str, Any], key: str, where: str, default: float | None = None) -> float:
    value = raw.get(key, default)
    if value is None:
        raise AssemblySpecError(f"{where}: '{key}' is required")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AssemblySpecError(f"{where}: '{key}' must be a number")
    if not math.isfinite(float(value)):
        raise AssemblySpecError(f"{where}: '{key}' must be finite")
    return float(value)


def _opt_num(raw: dict[str, Any], key: str, where: str) -> float | None:
    return _num(raw, key, where) if raw.get(key) is not None else None


def _vec_of(raw: Any, where: str) -> Vec:
    try:
        return as_vec(raw, where)
    except ValueError as exc:
        raise AssemblySpecError(str(exc)) from exc


def _pair(raw: Any, where: str) -> tuple[float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise AssemblySpecError(f"{where} must be [width, height] in mm")
    try:
        w, h = float(raw[0]), float(raw[1])
    except (TypeError, ValueError) as exc:
        raise AssemblySpecError(f"{where} must be two numbers") from exc
    return w, h


def _points(raw: Any, where: str) -> list[Vec]:
    if not isinstance(raw, list) or not raw:
        raise AssemblySpecError(f"{where} must be a non-empty list of [x, y, z] (or [x, y])")
    out: list[Vec] = []
    for i, p in enumerate(raw):
        if isinstance(p, list | tuple) and len(p) == 2:
            p = [p[0], p[1], 0.0]
        out.append(_vec_of(p, f"{where}[{i}]"))
    return out


@dataclass
class ClearanceSpec:
    a: str
    b: str
    min_gap_mm: float


@dataclass
class HolePatternSpec:
    a: str
    holes_a: list[Vec]
    b: str
    holes_b: list[Vec]
    tolerance_mm: float = DEFAULT_HOLE_TOLERANCE_MM
    #: The fastener axis in part ``a``'s frame.
    axis: Vec = (0.0, 0.0, 1.0)
    source: str = ""


@dataclass
class OutlineSpec:
    part: str
    size_mm: tuple[float, float]
    host: str
    opening_mm: tuple[float, float]
    clearance_mm: float = 0.0
    allow_rotation: bool = False
    source: str = ""


@dataclass
class ConnectorSpec:
    board: str
    name: str
    face: Vec | None = None
    direction: Vec | None = None
    #: The board outline in the board's frame, ``[[xmin, ymin], [xmax, ymax]]``.
    board_outline: tuple[tuple[float, float], tuple[float, float]] | None = None
    edge_setback_mm: float | None = None
    max_setback_mm: float = DEFAULT_MAX_SETBACK_MM
    slot_part: str | None = None
    slot_center: Vec | None = None
    slot_normal: Vec | None = None
    max_gap_mm: float = DEFAULT_MAX_GAP_MM
    tolerance_mm: float = DEFAULT_SLOT_TOLERANCE_MM
    source: str = ""


@dataclass
class StackItem:
    label: str
    thickness_mm: float
    count: int = 1


@dataclass
class StackSpec:
    name: str
    gap_mm: float
    items: list[StackItem]
    min_clearance_mm: float = 0.0
    parts: list[str] = field(default_factory=list)
    fastener_label: str | None = None
    fastener_length_mm: float | None = None
    min_engagement_mm: float = 0.0
    max_length_mm: float | None = None


@dataclass
class SeatSpec:
    part: str
    seat_part: str
    part_diameter_mm: float
    seat_diameter_mm: float
    min_clearance_mm: float = 0.0
    retention: str | None = None
    max_interference_mm: float = 0.2


@dataclass
class JointSpec:
    name: str
    joint_type: str
    moving: list[str]
    axis_point: Vec
    axis_dir: Vec
    parent: str | None = None
    min_deg: float | None = None
    max_deg: float | None = None
    steps: int = DEFAULT_MOTION_STEPS


@dataclass
class AssemblySpec:
    tolerance_mm3: float = DEFAULT_TOLERANCE_MM3
    allowed_overlaps: dict[tuple[str, str], str] = field(default_factory=dict)
    clearances: list[ClearanceSpec] = field(default_factory=list)
    hole_patterns: list[HolePatternSpec] = field(default_factory=list)
    outlines: list[OutlineSpec] = field(default_factory=list)
    connectors: list[ConnectorSpec] = field(default_factory=list)
    stacks: list[StackSpec] = field(default_factory=list)
    seats: list[SeatSpec] = field(default_factory=list)
    joints: list[JointSpec] = field(default_factory=list)


#: ``(family, size, length_mm) -> library entry``; ``twin_core.library.standard_parts``.
StandardPartLookup = Callable[[str, str, float | None], dict[str, Any]]


def _standard_lookup() -> StandardPartLookup:
    from twin_core.library.standard_parts import build_standard_part

    return build_standard_part


def _spec_value(entry: dict[str, Any], *names: str) -> float | None:
    specs = entry.get("specs") or {}
    for name in names:
        spec = specs.get(name)
        if isinstance(spec, dict) and isinstance(spec.get("value"), int | float):
            return float(spec["value"])
    return None


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _list(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise AssemblySpecError(f"'{key}' must be a list of objects")
    return value


def parse_spec(
    raw: dict[str, Any] | None,
    resolve: Callable[[str], str],
    *,
    standard_part: StandardPartLookup | None = None,
) -> AssemblySpec:
    """Read a check specification; part references go through ``resolve``.

    ``resolve`` turns what the caller wrote (a part's name in the assembly, or
    its ``KEY``/``KEY@n``) into the body name, and raises ``AssemblySpecError``
    for a part that is not in the assembly.
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise AssemblySpecError("checks must be an object")
    lookup = standard_part or _standard_lookup()
    spec = AssemblySpec(
        tolerance_mm3=_num(raw, "interference_tolerance_mm3", "checks", DEFAULT_TOLERANCE_MM3)
    )
    for i, o in enumerate(_list(raw, "allowed_overlaps")):
        where = f"allowed_overlaps[{i}]"
        reason = str(o.get("reason") or "").strip()
        if not reason:
            raise AssemblySpecError(f"{where}: 'reason' is required")
        spec.allowed_overlaps[_key(resolve(str(o.get("a"))), resolve(str(o.get("b"))))] = reason
    for i, c in enumerate(_list(raw, "clearances")):
        where = f"clearances[{i}]"
        spec.clearances.append(
            ClearanceSpec(
                resolve(str(c.get("a"))),
                resolve(str(c.get("b"))),
                _num(c, "min_gap_mm", where),
            )
        )
    for i, h in enumerate(_list(raw, "hole_patterns")):
        where = f"hole_patterns[{i}]"
        spec.hole_patterns.append(
            HolePatternSpec(
                a=resolve(str(h.get("a"))),
                holes_a=_points(h.get("holes_a"), f"{where}.holes_a"),
                b=resolve(str(h.get("b"))),
                holes_b=_points(h.get("holes_b"), f"{where}.holes_b"),
                tolerance_mm=_num(h, "tolerance_mm", where, DEFAULT_HOLE_TOLERANCE_MM),
                axis=_vec_of(h.get("axis") or [0, 0, 1], f"{where}.axis"),
                source=str(h.get("source") or ""),
            )
        )
    for i, o in enumerate(_list(raw, "outlines")):
        where = f"outlines[{i}]"
        spec.outlines.append(
            OutlineSpec(
                part=resolve(str(o.get("part"))),
                size_mm=_pair(o.get("size_mm"), f"{where}.size_mm"),
                host=resolve(str(o.get("host"))),
                opening_mm=_pair(o.get("opening_mm"), f"{where}.opening_mm"),
                clearance_mm=_num(o, "clearance_mm", where, 0.0),
                allow_rotation=bool(o.get("allow_rotation")),
                source=str(o.get("source") or ""),
            )
        )
    for i, c in enumerate(_list(raw, "connectors")):
        where = f"connectors[{i}]"
        outline = c.get("board_outline")
        board_outline = None
        if outline is not None:
            if not (isinstance(outline, list) and len(outline) == 2):
                raise AssemblySpecError(
                    f"{where}.board_outline must be [[xmin, ymin], [xmax, ymax]]"
                )
            board_outline = (
                _pair(outline[0], f"{where}.board_outline[0]"),
                _pair(outline[1], f"{where}.board_outline[1]"),
            )
        spec.connectors.append(
            ConnectorSpec(
                board=resolve(str(c.get("board"))),
                name=str(c.get("name") or "connector"),
                face=_vec_of(c["face"], f"{where}.face") if c.get("face") is not None else None,
                direction=_vec_of(c["direction"], f"{where}.direction")
                if c.get("direction") is not None
                else None,
                board_outline=board_outline,
                edge_setback_mm=_opt_num(c, "edge_setback_mm", where),
                max_setback_mm=_num(c, "max_setback_mm", where, DEFAULT_MAX_SETBACK_MM),
                slot_part=resolve(str(c["slot_part"])) if c.get("slot_part") else None,
                slot_center=_vec_of(c["slot_center"], f"{where}.slot_center")
                if c.get("slot_center") is not None
                else None,
                slot_normal=_vec_of(c["slot_normal"], f"{where}.slot_normal")
                if c.get("slot_normal") is not None
                else None,
                max_gap_mm=_num(c, "max_gap_mm", where, DEFAULT_MAX_GAP_MM),
                tolerance_mm=_num(c, "tolerance_mm", where, DEFAULT_SLOT_TOLERANCE_MM),
                source=str(c.get("source") or ""),
            )
        )
    for i, s in enumerate(_list(raw, "stacks")):
        where = f"stacks[{i}]"
        items: list[StackItem] = []
        for j, it in enumerate(_list(s, "items")):
            iw = f"{where}.items[{j}]"
            count = it.get("count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise AssemblySpecError(f"{iw}: 'count' must be a whole number from 1")
            if it.get("thickness_mm") is not None:
                thickness = _num(it, "thickness_mm", iw)
                label = str(it.get("name") or it.get("part") or f"item {j + 1}")
            elif it.get("standard"):
                family, size = str(it["standard"]), str(it.get("size") or "")
                try:
                    entry = lookup(family, size, None)
                except ValueError as exc:
                    raise AssemblySpecError(f"{iw}: {exc}") from exc
                value = _spec_value(entry, "thickness_mm", "height_mm")
                if value is None:
                    raise AssemblySpecError(f"{iw}: {family} {size} has no thickness")
                thickness = value
                label = str(entry.get("title") or f"{family} {size}")
            else:
                raise AssemblySpecError(f"{iw}: give 'thickness_mm', or 'standard' and 'size'")
            items.append(StackItem(label=label, thickness_mm=thickness, count=count))
        if not items:
            raise AssemblySpecError(f"{where}: 'items' is required")
        fastener = s.get("fastener")
        f_label = f_length = f_max = None
        f_engage = 0.0
        if fastener is not None:
            if not isinstance(fastener, dict):
                raise AssemblySpecError(f"{where}.fastener must be an object")
            fw = f"{where}.fastener"
            f_length = _opt_num(fastener, "length_mm", fw)
            if fastener.get("standard"):
                family, size = str(fastener["standard"]), str(fastener.get("size") or "")
                try:
                    entry = lookup(family, size, f_length)
                except ValueError as exc:
                    raise AssemblySpecError(f"{fw}: {exc}") from exc
                f_length = _spec_value(entry, "length_mm") or f_length
                f_label = str(entry.get("title") or f"{family} {size}")
            if f_length is None:
                raise AssemblySpecError(f"{fw}: 'length_mm' is required")
            f_label = f_label or str(fastener.get("name") or "fastener")
            f_engage = _num(fastener, "min_engagement_mm", fw, 0.0)
            f_max = _opt_num(fastener, "max_length_mm", fw)
        spec.stacks.append(
            StackSpec(
                name=str(s.get("name") or f"stack {i + 1}"),
                gap_mm=_num(s, "gap_mm", where),
                items=items,
                min_clearance_mm=_num(s, "min_clearance_mm", where, 0.0),
                parts=[resolve(str(p)) for p in s.get("parts") or []],
                fastener_label=f_label,
                fastener_length_mm=f_length,
                min_engagement_mm=f_engage,
                max_length_mm=f_max,
            )
        )
    for i, s in enumerate(_list(raw, "seats")):
        where = f"seats[{i}]"
        retention = s.get("retention")
        spec.seats.append(
            SeatSpec(
                part=resolve(str(s.get("part"))),
                seat_part=resolve(str(s.get("seat_part"))),
                part_diameter_mm=_num(s, "part_diameter_mm", where),
                seat_diameter_mm=_num(s, "seat_diameter_mm", where),
                min_clearance_mm=_num(s, "min_clearance_mm", where, 0.0),
                retention=str(retention).strip() or None if retention else None,
                max_interference_mm=_num(s, "max_interference_mm", where, 0.2),
            )
        )
    for k, joint in enumerate(_list(raw, "joints")):
        spec.joints.append(parse_joint(joint, resolve, f"joints[{k}]"))
    return spec


def parse_joint(j: dict[str, Any], resolve: Callable[[str], str], where: str) -> JointSpec:
    """One joint, as the assembly records it or the caller gives it."""
    moving_raw = j.get("moving") or ([j["child"]] if j.get("child") else [])
    if not isinstance(moving_raw, list) or not moving_raw:
        raise AssemblySpecError(f"{where}: name the moving part ('child' or 'moving')")
    steps = j.get("steps", DEFAULT_MOTION_STEPS)
    if isinstance(steps, bool) or not isinstance(steps, int) or not 2 <= steps <= MAX_MOTION_STEPS:
        raise AssemblySpecError(
            f"{where}: 'steps' must be a whole number from 2 to {MAX_MOTION_STEPS}"
        )
    lo, hi = _opt_num(j, "min_deg", where), _opt_num(j, "max_deg", where)
    if lo is not None and hi is not None and lo > hi:
        raise AssemblySpecError(f"{where}: 'min_deg' is above 'max_deg'")
    return JointSpec(
        name=str(j.get("name") or f"joint {where}"),
        joint_type=str(j.get("type") or "revolute"),
        moving=[resolve(str(m)) for m in moving_raw],
        axis_point=_vec_of(j.get("axis_point") or [0, 0, 0], f"{where}.axis_point"),
        axis_dir=_vec_of(j.get("axis_dir") or [0, 0, 1], f"{where}.axis_dir"),
        parent=resolve(str(j["parent"])) if j.get("parent") else None,
        min_deg=lo,
        max_deg=hi,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _fid(check: str, refs: Sequence[str], suffix: str = "") -> str:
    base = f"{check}:{'|'.join(sorted(refs))}"
    return f"{base}:{suffix}" if suffix else base


def _r(v: float) -> float:
    return round(v, 3)


def _result(
    check: str, findings: list[Finding], required: bool, ran: int, detail_ok: str, nothing: str
) -> CheckResult:
    if not ran:
        return CheckResult(check, "not_assessed", required, nothing)
    blocking = [f for f in findings if f.severity in BLOCKING_SEVERITIES]
    if blocking:
        return CheckResult(
            check, "failed", required, "; ".join(f.message for f in blocking)[:2000], findings
        )
    return CheckResult(check, "passed", required, detail_ok, findings)


def _span(points: list[Vec]) -> tuple[float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (max(xs) - min(xs), max(ys) - min(ys))


def check_revisions(bodies: Sequence[Body], stale: dict[str, str]) -> CheckResult:
    """``stale``: body name -> the part's current ``KEY@n`` when the body is older."""
    findings = [
        Finding(
            id=_fid("revisions", [b.ref]),
            check="revisions",
            severity="high",
            message=f"{b.name} is {b.ref} in the assembly, but the part is now {stale[b.name]}; "
            "rebuild the assembly with the current revision",
            parts=[b.ref],
            values={"assembled": b.ref, "current": stale[b.name]},
        )
        for b in bodies
        if b.name in stale
    ]
    return _result(
        "revisions",
        findings,
        True,
        len(bodies),
        f"{len(bodies)} bodies, each the current revision",
        "no bodies in the assembly",
    )


def check_interference(
    bodies: Sequence[Body], measured: EngineResult, spec: AssemblySpec
) -> CheckResult:
    refs = {b.name: b.ref for b in bodies}
    findings: list[Finding] = []
    for o in measured.overlaps:
        pair = [refs[o.a], refs[o.b]]
        allowed = spec.allowed_overlaps.get(_key(o.a, o.b))
        findings.append(
            Finding(
                id=_fid("interference", pair),
                check="interference",
                severity="low" if allowed else "high",
                message=(
                    f"{o.a} and {o.b} overlap by {o.volume_mm3:g} mm3"
                    + (f" (intended: {allowed})" if allowed else "")
                ),
                parts=pair,
                values={"volume_mm3": o.volume_mm3, "tolerance_mm3": spec.tolerance_mm3},
            )
        )
    n = len(bodies)
    return _result(
        "interference",
        findings,
        True,
        n if n >= 2 else 0,
        f"no overlap above {spec.tolerance_mm3:g} mm3 between {n} bodies",
        "fewer than two bodies to check",
    )


def check_clearances(
    bodies: Sequence[Body], measured: EngineResult, spec: AssemblySpec, required: bool
) -> CheckResult:
    refs = {b.name: b.ref for b in bodies}
    findings: list[Finding] = []
    ran = 0
    missing: list[str] = []
    for c in spec.clearances:
        gap = measured.distances.get(_key(c.a, c.b))
        if gap is None:
            missing.append(f"{c.a}/{c.b}")
            continue
        ran += 1
        if gap < c.min_gap_mm:
            pair = [refs[c.a], refs[c.b]]
            findings.append(
                Finding(
                    id=_fid("clearance", pair),
                    check="clearance",
                    severity="medium",
                    message=f"{c.a} and {c.b} are {gap:g} mm apart, the minimum is "
                    f"{c.min_gap_mm:g} mm",
                    parts=pair,
                    values={"gap_mm": gap, "min_gap_mm": c.min_gap_mm},
                )
            )
    result = _result(
        "clearance",
        findings,
        required,
        ran,
        f"{ran} stated gap(s) kept",
        "no minimum gap stated" if not missing else "gaps not measured: " + ", ".join(missing),
    )
    if missing and result.status != "not_assessed":
        result.detail += "; not measured: " + ", ".join(missing)
    return result


def check_hole_patterns(bodies: dict[str, Body], spec: AssemblySpec, required: bool) -> CheckResult:
    findings: list[Finding] = []
    for h in spec.hole_patterns:
        ba, bb = bodies[h.a], bodies[h.b]
        axis = unit(apply_dir(ba.matrix, h.axis))
        world_a = [apply(ba.matrix, p) for p in h.holes_a]
        world_b = [apply(bb.matrix, p) for p in h.holes_b]
        pair = [ba.ref, bb.ref]
        misses = []
        for local, wa in zip(h.holes_a, world_a, strict=True):
            best = None
            for local_b, wb in zip(h.holes_b, world_b, strict=True):
                d = sub(wb, wa)
                along = dot(d, axis)
                lateral = norm(
                    (d[0] - along * axis[0], d[1] - along * axis[1], d[2] - along * axis[2])
                )
                if best is None or lateral < best[0]:
                    best = (lateral, local_b)
            assert best is not None
            if best[0] > h.tolerance_mm:
                misses.append(
                    {
                        "hole": [_r(v) for v in local],
                        "nearest": [_r(v) for v in best[1]],
                        "offset_mm": _r(best[0]),
                    }
                )
        extra = len(h.holes_a) - len(h.holes_b)
        if misses or extra > 0:
            span_a, span_b = _span(h.holes_a), _span(h.holes_b)
            message = (
                f"{len(misses)} of {len(h.holes_a)} holes on {h.a} miss the holes on {h.b} by more "
                f"than {h.tolerance_mm:g} mm (patterns {span_a[0]:g} x {span_a[1]:g} mm and "
                f"{span_b[0]:g} x {span_b[1]:g} mm)"
            )
            if extra > 0:
                message += f"; {h.a} has {extra} more hole(s) than {h.b}"
            findings.append(
                Finding(
                    id=_fid("hole_pattern", pair),
                    check="hole_pattern",
                    severity="high",
                    message=message,
                    parts=pair,
                    values={
                        "tolerance_mm": h.tolerance_mm,
                        "misses": misses,
                        "holes_a": len(h.holes_a),
                        "holes_b": len(h.holes_b),
                        "span_a_mm": [_r(v) for v in span_a],
                        "span_b_mm": [_r(v) for v in span_b],
                        "source": h.source,
                    },
                )
            )
    return _result(
        "hole_pattern",
        findings,
        required,
        len(spec.hole_patterns),
        f"{len(spec.hole_patterns)} hole pattern(s) line up",
        "no hole pattern given: pass hole_patterns, or record mounting_holes on the board and "
        "mounting_bosses (or mounting_holes) on the part it mounts to",
    )


def check_outlines(bodies: dict[str, Body], spec: AssemblySpec, required: bool) -> CheckResult:
    findings: list[Finding] = []
    for o in spec.outlines:
        need = (o.size_mm[0] + 2 * o.clearance_mm, o.size_mm[1] + 2 * o.clearance_mm)
        fits = need[0] <= o.opening_mm[0] and need[1] <= o.opening_mm[1]
        if not fits and o.allow_rotation:
            fits = need[1] <= o.opening_mm[0] and need[0] <= o.opening_mm[1]
        if not fits:
            pair = [bodies[o.part].ref, bodies[o.host].ref]
            findings.append(
                Finding(
                    id=_fid("outline_fit", pair),
                    check="outline_fit",
                    severity="high",
                    message=f"{o.part} ({o.size_mm[0]:g} x {o.size_mm[1]:g} mm) needs "
                    f"{need[0]:g} x {need[1]:g} mm with {o.clearance_mm:g} mm a side; the opening "
                    f"in {o.host} is {o.opening_mm[0]:g} x {o.opening_mm[1]:g} mm",
                    parts=pair,
                    values={
                        "size_mm": list(o.size_mm),
                        "opening_mm": list(o.opening_mm),
                        "clearance_mm": o.clearance_mm,
                        "source": o.source,
                    },
                )
            )
    return _result(
        "outline_fit",
        findings,
        required,
        len(spec.outlines),
        f"{len(spec.outlines)} outline(s) fit",
        "no outline and pocket given: pass outlines, or record the board's dimensions and the "
        "host's internal_envelope",
    )


def _setback(c: ConnectorSpec) -> float | None:
    if c.edge_setback_mm is not None:
        return c.edge_setback_mm
    if c.face is None or c.direction is None or c.board_outline is None:
        return None
    d = unit(c.direction)
    (x0, y0), (x1, y1) = c.board_outline
    corners: list[Vec] = [(x, y, c.face[2]) for x in (x0, x1) for y in (y0, y1)]
    return max(dot(p, d) for p in corners) - dot(c.face, d)


def check_connectors(bodies: dict[str, Body], spec: AssemblySpec, required: bool) -> CheckResult:
    findings: list[Finding] = []
    ran = 0
    for c in spec.connectors:
        board = bodies[c.board]
        setback = _setback(c)
        if setback is not None:
            ran += 1
            if setback > c.max_setback_mm:
                findings.append(
                    Finding(
                        id=_fid("connector", [board.ref], f"{c.name}:edge"),
                        check="connector",
                        severity="medium",
                        message=f"{c.name} on {c.board} sits {setback:g} mm inside the board "
                        f"edge; its face must be within {c.max_setback_mm:g} mm of it",
                        parts=[board.ref],
                        values={"setback_mm": _r(setback), "max_setback_mm": c.max_setback_mm},
                    )
                )
        if c.slot_part and c.slot_center is not None and c.face is not None:
            ran += 1
            slot = bodies[c.slot_part]
            face_w = apply(board.matrix, c.face)
            centre_w = apply(slot.matrix, c.slot_center)
            facing = unit(apply_dir(board.matrix, c.direction)) if c.direction else None
            normal = (
                unit(apply_dir(slot.matrix, c.slot_normal)) if c.slot_normal is not None else facing
            )
            d = sub(centre_w, face_w)
            issues: list[str] = []
            values: dict[str, Any] = {}
            if normal is not None:
                gap = abs(dot(d, normal))
                along = dot(d, normal)
                lateral = norm(
                    (d[0] - along * normal[0], d[1] - along * normal[1], d[2] - along * normal[2])
                )
                values.update({"gap_mm": _r(gap), "offset_mm": _r(lateral)})
                if gap > c.max_gap_mm:
                    issues.append(
                        f"its face is {gap:g} mm from the slot (at most {c.max_gap_mm:g})"
                    )
                if lateral > c.tolerance_mm:
                    issues.append(
                        f"it is {lateral:g} mm off the slot centre (at most {c.tolerance_mm:g})"
                    )
                if facing is not None:
                    angle = math.degrees(math.acos(max(-1.0, min(1.0, abs(dot(facing, normal))))))
                    values["angle_deg"] = _r(angle)
                    if angle > MAX_CONNECTOR_ANGLE_DEG:
                        issues.append(f"it faces {angle:.0f} degrees away from the slot")
            else:
                dist = norm(d)
                values["distance_mm"] = _r(dist)
                if dist > c.max_gap_mm:
                    issues.append(f"its face is {dist:g} mm from the slot centre")
            if issues:
                pair = [board.ref, slot.ref]
                findings.append(
                    Finding(
                        id=_fid("connector", pair, f"{c.name}:slot"),
                        check="connector",
                        severity="medium",
                        message=f"{c.name} on {c.board} does not reach its slot in "
                        f"{c.slot_part}: " + "; ".join(issues),
                        parts=pair,
                        values={**values, "source": c.source},
                    )
                )
    return _result(
        "connector",
        findings,
        required,
        ran,
        f"{len(spec.connectors)} connector(s) at the board edge and on their slots",
        "no connector given: pass connectors with the face, its direction and the board outline "
        "(or edge_setback_mm), and the slot",
    )


def check_stacks(bodies: dict[str, Body], spec: AssemblySpec, required: bool) -> CheckResult:
    findings: list[Finding] = []
    for s in spec.stacks:
        total = sum(i.thickness_mm * i.count for i in s.items)
        refs = [bodies[p].ref for p in s.parts]
        stack_words = " + ".join(
            f"{i.count} x {i.thickness_mm:g} mm {i.label}"
            if i.count > 1
            else f"{i.thickness_mm:g} mm {i.label}"
            for i in s.items
        )
        if total + s.min_clearance_mm > s.gap_mm + 1e-9:
            findings.append(
                Finding(
                    id=_fid("stack", refs, s.name),
                    check="stack",
                    severity="high",
                    message=f"{s.name}: {stack_words} is {total:g} mm"
                    + (f" (+{s.min_clearance_mm:g} mm clearance)" if s.min_clearance_mm else "")
                    + f"; the gap is {s.gap_mm:g} mm",
                    parts=refs,
                    values={
                        "stack_mm": _r(total),
                        "gap_mm": s.gap_mm,
                        "min_clearance_mm": s.min_clearance_mm,
                    },
                )
            )
        if s.fastener_length_mm is not None:
            need = total + s.min_engagement_mm
            if s.fastener_length_mm + 1e-9 < need:
                findings.append(
                    Finding(
                        id=_fid("stack", refs, f"{s.name}:short"),
                        check="stack",
                        severity="high",
                        message=f"{s.name}: {s.fastener_label} is {s.fastener_length_mm:g} mm; the "
                        f"grip is {total:g} mm and it needs {s.min_engagement_mm:g} mm of "
                        "engagement",
                        parts=refs,
                        values={"length_mm": s.fastener_length_mm, "needed_mm": _r(need)},
                    )
                )
            if s.max_length_mm is not None and s.fastener_length_mm > s.max_length_mm + 1e-9:
                findings.append(
                    Finding(
                        id=_fid("stack", refs, f"{s.name}:long"),
                        check="stack",
                        severity="high",
                        message=f"{s.name}: {s.fastener_label} is {s.fastener_length_mm:g} mm and "
                        f"bottoms out above {s.max_length_mm:g} mm",
                        parts=refs,
                        values={"length_mm": s.fastener_length_mm, "max_mm": s.max_length_mm},
                    )
                )
    return _result(
        "stack",
        findings,
        required,
        len(spec.stacks),
        f"{len(spec.stacks)} stack(s) fit",
        "no fastener or washer stack given",
    )


def check_seats(bodies: dict[str, Body], spec: AssemblySpec, required: bool) -> CheckResult:
    findings: list[Finding] = []
    for s in spec.seats:
        pair = [bodies[s.part].ref, bodies[s.seat_part].ref]
        press = (s.retention or "").lower().replace("-", "_") == "press_fit"
        over = s.part_diameter_mm - s.seat_diameter_mm
        if press:
            ok = 0.0 <= over <= s.max_interference_mm
            words = (
                f"a press fit of {over:g} mm"
                if ok
                else f"{over:g} mm, outside a press fit of 0 to {s.max_interference_mm:g} mm"
            )
        else:
            ok = s.part_diameter_mm + s.min_clearance_mm <= s.seat_diameter_mm + 1e-9
            words = f"{-over:g} mm smaller"
        if not ok:
            findings.append(
                Finding(
                    id=_fid("seat", pair, "fit"),
                    check="seat",
                    severity="high",
                    message=f"{s.part} ({s.part_diameter_mm:g} mm) does not fit the "
                    f"{s.seat_diameter_mm:g} mm seat in {s.seat_part}"
                    + (
                        f": {words}" if press else f"; it needs {s.min_clearance_mm:g} mm clearance"
                    ),
                    parts=pair,
                    values={
                        "part_diameter_mm": s.part_diameter_mm,
                        "seat_diameter_mm": s.seat_diameter_mm,
                        "min_clearance_mm": s.min_clearance_mm,
                    },
                )
            )
        if not s.retention:
            findings.append(
                Finding(
                    id=_fid("seat", pair, "retention"),
                    check="seat",
                    severity="medium",
                    message=f"{s.part} sits in {s.seat_part} with no retention feature or "
                    "fastener on record (clips, screws, adhesive or a press fit)",
                    parts=pair,
                )
            )
    return _result(
        "seat",
        findings,
        required,
        len(spec.seats),
        f"{len(spec.seats)} seated part(s) fit and are retained",
        "no seated part given",
    )


def _angles(j: JointSpec) -> list[float]:
    assert j.min_deg is not None and j.max_deg is not None
    n = j.steps
    return [j.min_deg + (j.max_deg - j.min_deg) * i / (n - 1) for i in range(n)]


def motion_poses(bodies: dict[str, Body], spec: AssemblySpec) -> list[Pose]:
    """The sampled poses of every revolute joint with limits."""
    poses: list[Pose] = []
    for j in spec.joints:
        if j.joint_type != "revolute" or j.min_deg is None or j.max_deg is None:
            continue
        frame: Matrix | None = bodies[j.parent].matrix if j.parent else None
        point = apply(frame, j.axis_point) if frame else j.axis_point
        direction = apply_dir(frame, j.axis_dir) if frame else j.axis_dir
        moving = set(j.moving)
        pairs = tuple((m, b) for m in sorted(moving) for b in sorted(bodies) if b not in moving)
        for angle in _angles(j):
            if abs(angle) < 1e-9:
                continue
            rot = axis_rotation(point, direction, angle)
            poses.append(
                Pose(
                    id=f"{j.name}@{angle:g}",
                    matrices={m: compose(rot, bodies[m].matrix) for m in moving},
                    pairs=pairs,
                )
            )
    return poses


def check_motion(
    bodies: dict[str, Body], measured: EngineResult, spec: AssemblySpec, required: bool
) -> CheckResult:
    findings: list[Finding] = []
    ran = 0
    skipped: list[str] = []
    for j in spec.joints:
        if j.joint_type != "revolute":
            continue
        if j.min_deg is None or j.max_deg is None:
            skipped.append(f"{j.name} (no limits stated)")
            continue
        ran += 1
        hits: dict[tuple[str, str], list[float]] = {}
        worst: dict[tuple[str, str], float] = {}
        for angle in _angles(j):
            for o in measured.poses.get(f"{j.name}@{angle:g}", []):
                k = _key(o.a, o.b)
                hits.setdefault(k, []).append(_r(angle))
                worst[k] = max(worst.get(k, 0.0), o.volume_mm3)
        for (a, b), angles in sorted(hits.items()):
            pair = [bodies[a].ref, bodies[b].ref]
            if _key(a, b) in spec.allowed_overlaps:
                continue
            findings.append(
                Finding(
                    id=_fid("motion", pair, j.name),
                    check="motion",
                    severity="medium",
                    message=f"{j.name}: {a} hits {b} at {', '.join(f'{x:g}' for x in angles)} "
                    f"degrees (range {j.min_deg:g} to {j.max_deg:g})",
                    parts=pair,
                    values={
                        "angles_deg": angles,
                        "worst_volume_mm3": worst[(a, b)],
                        "range_deg": [j.min_deg, j.max_deg],
                        "steps": j.steps,
                    },
                )
            )
    result = _result(
        "motion",
        findings,
        required,
        ran,
        f"{ran} joint(s) swept through their range without collision",
        "no revolute joint with stated limits" + (f": {', '.join(skipped)}" if skipped else ""),
    )
    if skipped and result.status != "not_assessed":
        result.detail += "; not swept: " + ", ".join(skipped)
    return result


def _clearance_pairs(spec: AssemblySpec) -> list[tuple[str, str]]:
    return [(c.a, c.b) for c in spec.clearances]


async def run_checks(
    bodies: Sequence[Body],
    spec: AssemblySpec,
    engine: GeometryEngine,
    *,
    assembly_ref: str,
    stale: dict[str, str] | None = None,
    required: set[str] | frozenset[str] = frozenset(),
) -> AssemblyReport:
    """Every check on the assembly, one engine call for every solid question.

    ``required`` adds checks to :data:`ALWAYS_REQUIRED`. A check the caller
    gave entries for is required too: what was asked for must be answered.
    """
    by_name = {b.name: b for b in bodies}
    asked = {
        "clearance": bool(spec.clearances),
        "hole_pattern": bool(spec.hole_patterns),
        "outline_fit": bool(spec.outlines),
        "connector": bool(spec.connectors),
        "stack": bool(spec.stacks),
        "seat": bool(spec.seats),
        "motion": any(j.min_deg is not None and j.max_deg is not None for j in spec.joints),
    }
    need = set(ALWAYS_REQUIRED) | set(required) | {k for k, v in asked.items() if v}
    with tracer.start_as_current_span("twin.dfa.run_checks") as span:
        span.set_attribute("assembly.ref", assembly_ref)
        span.set_attribute("assembly.bodies", len(bodies))
        poses = motion_poses(by_name, spec)
        span.set_attribute("assembly.poses", len(poses))
        measured = await engine.measure(
            bodies,
            tolerance_mm3=spec.tolerance_mm3,
            distance_pairs=_clearance_pairs(spec),
            poses=poses,
        )
        checks = [
            check_revisions(bodies, stale or {}),
            check_interference(bodies, measured, spec),
            check_clearances(bodies, measured, spec, "clearance" in need),
            check_hole_patterns(by_name, spec, "hole_pattern" in need),
            check_outlines(by_name, spec, "outline_fit" in need),
            check_connectors(by_name, spec, "connector" in need),
            check_stacks(by_name, spec, "stack" in need),
            check_motion(by_name, measured, spec, "motion" in need),
            check_seats(by_name, spec, "seat" in need),
        ]
        report = AssemblyReport(
            assembly_ref=assembly_ref, part_refs=[b.ref for b in bodies], checks=checks
        )
        span.set_attribute("assembly.outcome", report.outcome())
        logger.info(
            "assembly_checks_run",
            assembly_ref=assembly_ref,
            bodies=len(bodies),
            outcome=report.outcome(),
            **report.counts(),
        )
        return report


__all__ = [
    "ALWAYS_REQUIRED",
    "BLOCKING_SEVERITIES",
    "CHECK_LABELS",
    "CHECK_ORDER",
    "AssemblyReport",
    "AssemblySpec",
    "AssemblySpecError",
    "CheckResult",
    "ConnectorSpec",
    "Finding",
    "HolePatternSpec",
    "JointSpec",
    "OutlineSpec",
    "SeatSpec",
    "StackItem",
    "StackSpec",
    "motion_poses",
    "parse_joint",
    "parse_spec",
    "run_checks",
]
