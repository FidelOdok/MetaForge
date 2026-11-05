"""Cheap DFM heuristics against the best geometry each component has (DFM-006).

During interactive refinement nothing exact may exist yet: a component may be
a block in a concept layout (L1, sizes estimated), a parametric sketch whose
depth a person typed, a CAD revision with its generation parameters, or an
exact, validated B-Rep. :func:`evaluate` runs one process profile's rules
against whatever measures the component has, and says so:

* every **finding** names its rule and the profile version, a severity
  (``blocking`` or ``advisory``), an **anchor** (the component id, plus the
  feature or face signature when known, DFM-008), the measured value against
  the limit with units and the measure's **confidence**, the evidence it was
  read from, a plain message and a suggested fix;
* a rule with an unknown input is **not assessed**, with the reason ("not
  assessed for minimum wall thickness because the wall thickness is
  unknown"), never a pass (DFM-010);
* a violation is ``blocking`` only when the limit is a machine limit and the
  measure is exact, confirmed by a person or a design parameter; guidance on
  estimated geometry is always ``advisory``.

**Status** (DFM-013, :func:`finding_set_status`): ``issues_found`` when there
is any finding, ``analysis_pending`` when exact checks are scheduled,
``preliminary_guidance`` when only heuristics ran and found nothing, and
``not_assessed`` when nothing ran. A finding set is never ``analysis_passed``
or later: heuristics cannot pass anything (:class:`DfmFindingSet` refuses).

Pure: Pydantic and the standard library only. The gateway gathers the
geometry and stores the result (``api_gateway.twin.dfm_findings``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, model_validator

from observability.tracing import get_tracer
from twin_core.dfm.overhang import compute_overhang_faces
from twin_core.dfm.profiles import PROFILES, DfmProfile, DfmRule, RuleKind, profile_for_process
from twin_core.dfm.profiles.model import LIMIT_KINDS
from twin_core.manufacturing.context import DFM_STATUS_LABELS, DfmStatus

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.dfm.heuristics")

Confidence = Literal["exact", "human_confirmed", "design_parameter", "estimated"]
#: Measures firm enough for a machine limit's violation to block.
FIRM_CONFIDENCE: frozenset[str] = frozenset({"exact", "human_confirmed", "design_parameter"})
#: Weakest first: a derived measure takes the weakest of its inputs.
_CONFIDENCE_ORDER: tuple[str, ...] = ("estimated", "design_parameter", "human_confirmed", "exact")

GeometryLevel = Literal["none", "L1", "parametric_sketch", "parametric", "exact"]
Severity = Literal["blocking", "advisory"]

#: The statuses a heuristic finding set may carry (never passed or released).
HEURISTIC_STATUSES: frozenset[DfmStatus] = frozenset(
    {
        DfmStatus.NOT_ASSESSED,
        DfmStatus.PRELIMINARY_GUIDANCE,
        DfmStatus.ANALYSIS_PENDING,
        DfmStatus.ISSUES_FOUND,
    }
)
#: The statuses an exact finding set may carry (C3D slice 6). An incomplete
#: exact run with nothing found reads ``analysis_pending``: the analysis has
#: not concluded. Verification and release are product level, never a set's.
EXACT_STATUSES: frozenset[DfmStatus] = frozenset(
    {
        DfmStatus.NOT_ASSESSED,
        DfmStatus.ANALYSIS_PENDING,
        DfmStatus.ISSUES_FOUND,
        DfmStatus.ANALYSIS_PASSED,
    }
)
#: Worst first, for a product made of several components. A product is only
#: as far along as its least assessed component, so a pass ranks last.
_STATUS_RANK: tuple[DfmStatus, ...] = (
    DfmStatus.ISSUES_FOUND,
    DfmStatus.ANALYSIS_PENDING,
    DfmStatus.PRELIMINARY_GUIDANCE,
    DfmStatus.NOT_ASSESSED,
    DfmStatus.VERIFICATION_REQUIRED,
    DfmStatus.ANALYSIS_PASSED,
    DfmStatus.RELEASED,
)

#: How a quantity is named in a "not assessed" reason.
QUANTITY_WORDS: dict[str, str] = {
    "wall_thickness": "the wall thickness",
    "wall_thickness_max": "the thickest wall",
    "thinnest_section": "the thinnest section",
    "feature_size": "the smallest feature size",
    "hole_diameter": "the hole diameter",
    "bridge_length": "the unsupported bridge length",
    "envelope": "the part's overall size",
    "faces": "the part's faces",
    "has_internal_cavity": "whether the part has an enclosed cavity",
    "drain_hole_diameter": "the drain hole size",
    "internal_corner_radius": "the internal corner radius",
    "cavity_depth": "the pocket depth",
    "cavity_width": "the pocket width",
    "draft_angle": "the draft angle",
    "rib_thickness": "the rib thickness",
    "bend_radius": "the bend radius",
    "flange_length": "the flange length",
    "hole_to_bend": "the hole distance from a bend",
    "sheet_thickness": "the sheet thickness",
}

#: Material words, for a rule that applies to one material family.
_FAMILY_WORDS: dict[str, tuple[str, ...]] = {
    "metal": (
        "aluminium",
        "aluminum",
        "steel",
        "stainless",
        "brass",
        "copper",
        "bronze",
        "titanium",
        "zinc",
        "metal",
        "6061",
        "7075",
    ),
    "plastic": (
        "pla",
        "abs",
        "petg",
        "pet",
        "asa",
        "nylon",
        "pa12",
        "pa6",
        "polycarbonate",
        "pc",
        "pom",
        "acetal",
        "delrin",
        "hdpe",
        "pp",
        "polypropylene",
        "tpu",
        "acrylic",
        "pmma",
        "plastic",
        "peek",
    ),
    "resin": ("resin", "photopolymer"),
    "wood": ("wood", "plywood", "mdf", "oak", "birch", "pine", "bamboo"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def material_family(material: str | None) -> str | None:
    """``metal``, ``plastic``, ``resin``, ``wood`` or ``None`` from a material name."""
    if not material:
        return None
    words = set(re.findall(r"[a-z0-9]+", str(material).lower()))
    for family, names in _FAMILY_WORDS.items():
        if words & set(names):
            return family
    return None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class Measure(BaseModel):
    """One measured or stated quantity of a component, with how firm it is."""

    quantity: str = Field(min_length=2, max_length=60)
    #: A length or angle, an envelope ``[x, y, z]``, or a yes/no.
    value: float | bool | list[float]
    unit: str = "mm"
    confidence: Confidence
    #: Where it was read: ``concept layout (L1)``, ``parametric sketch``,
    #: ``CAD parameter``, ``exact B-Rep``.
    source: str = Field(min_length=2, max_length=120)
    #: The feature it belongs to (a parameter name, a feature id), when known.
    feature: str | None = Field(None, max_length=200)
    face_signature: str | None = Field(None, max_length=300)
    #: ``KEY@n`` or ``node:<id>`` of what it was read from.
    evidence_ref: str | None = Field(None, max_length=300)


class FaceSample(BaseModel):
    """One face of a mesh or B-Rep face table."""

    face_id: str = Field(min_length=1, max_length=200)
    normal: tuple[float, float, float]
    area_mm2: float | None = None
    #: A stable face signature or semantic tag, when the source knows one.
    face_signature: str | None = Field(None, max_length=300)


class ComponentGeometry(BaseModel):
    """The best geometry a component has, as measures (and faces, when known)."""

    component_id: str = Field(min_length=1, max_length=200)
    name: str = Field("", max_length=200)
    level: GeometryLevel = "none"
    #: What was read: ``KEY@n`` item revisions and ``node:<id>`` work products.
    geometry_refs: list[str] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    faces: list[FaceSample] | None = None
    faces_confidence: Confidence = "estimated"
    #: True when face normals point outward (a B-Rep), so only downward faces overhang.
    faces_directed: bool = False
    faces_evidence_ref: str | None = None
    build_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)

    def of(self, quantity: str) -> list[Measure]:
        return [m for m in self.measures if m.quantity == quantity]


class MachineCapability(BaseModel):
    value: Any
    #: The library's own source for it (``Prusa MK4S build_volume_mm (url)``).
    source: str


class MachineContext(BaseModel):
    """The selected machine, read from the machine library (never assumed)."""

    id: str
    name: str
    capabilities: dict[str, MachineCapability] = Field(default_factory=dict)


class ProcessContext(BaseModel):
    """What the manufacturing context says, as the evaluator reads it."""

    process: str | None = None
    #: ``user_stated``, ``decision``, ``assumption``, ``candidate`` or ``unknown``.
    process_authority: str = "unknown"
    material: str | None = None
    material_authority: str = "unknown"
    quantity: int | str | None = None
    machine: MachineContext | None = None
    #: ``MFC-...@n`` or ``unresolved``.
    context_ref: str = "unresolved"
    unknowns: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


class Anchor(BaseModel):
    """Where a finding is (DFM-008)."""

    component_id: str
    feature: str | None = None
    face_signature: str | None = None
    #: Every face the finding covers, when there are several.
    faces: list[str] = Field(default_factory=list)


class DfmFinding(BaseModel):
    """One actionable finding."""

    id: str
    rule_id: str
    rule_title: str
    profile_id: str
    profile_version: str
    process: str
    rule_kind: RuleKind
    severity: Severity
    anchor: Anchor
    quantity: str
    measured: float | str | None
    limit: float | str | None
    unit: str
    comparison: str
    confidence: Confidence
    #: Where the number came from (the rule's source, or the machine's).
    limit_source: str
    evidence_refs: list[str] = Field(default_factory=list)
    message: str
    suggested_fix: str
    #: What applying the fix would change.
    fix_changes: list[str] = Field(default_factory=list)
    #: A person's decision on it, carried across recomputes.
    decision: dict[str, Any] | None = None


class NotAssessed(BaseModel):
    """A rule that could not run, and why (DFM-010)."""

    rule_id: str
    title: str
    reason: str

    @property
    def text(self) -> str:
        return f"Not assessed for {self.title.lower()} because {self.reason}"


class VerificationItem(BaseModel):
    """A physical or supplier verification a component needs (DFM-013).

    Never satisfied automatically: a person records evidence (a note or an
    upload), which waits for a reviewer's approval; only the approval makes
    it ``satisfied``.
    """

    id: str
    rule_id: str
    component_id: str
    title: str
    kind: Literal["physical_test", "supplier_confirmation"]
    #: What evidence settles it.
    needed: str
    #: Why it applies to this component.
    reason: str
    source: str = ""
    status: Literal["open", "awaiting_approval", "satisfied", "rejected"] = "open"
    #: ``[{id, note, attachment, recorded_by, recorded_at, approval_id, state}]``.
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    #: ``{by, at, approval_id, evidence_id}`` once a reviewer approved evidence.
    satisfied_by: dict[str, Any] | None = None


class DfmFindingSet(BaseModel):
    """One component's findings under one process profile.

    ``kind`` ``heuristic`` (C3D slice 4): guidance on the best geometry the
    component has, never a pass. ``kind`` ``exact`` (C3D slice 6): the
    profile's exact checks on a validated B-Rep revision; ``analysis_passed``
    only when every check the profile names ran and none found anything.
    """

    component_id: str
    component_name: str = ""
    process: str | None = None
    process_authority: str = "unknown"
    profile_id: str | None = None
    profile_version: str | None = None
    geometry_level: GeometryLevel = "none"
    geometry_refs: list[str] = Field(default_factory=list)
    context_ref: str = "unresolved"
    findings: list[DfmFinding] = Field(default_factory=list)
    not_assessed: list[NotAssessed] = Field(default_factory=list)
    rules_run: list[str] = Field(default_factory=list)
    status: DfmStatus = DfmStatus.NOT_ASSESSED
    note: str = DFM_STATUS_LABELS[DfmStatus.NOT_ASSESSED]
    kind: Literal["heuristic", "exact"] = "heuristic"
    #: True for a heuristic set: nothing in it is an exact check.
    heuristic_only: bool = True
    exact_checks_scheduled: bool = False
    evaluated_at: str = Field(default_factory=_now)
    #: What the evaluation read, hashed: the same inputs make the same set.
    inputs_sha256: str = ""
    # -- exact sets only ---------------------------------------------------
    #: The rules the profile names for exact analysis.
    checks_required: list[str] = Field(default_factory=list)
    #: True when every required check ran.
    complete: bool = False
    #: Profile rules that stay guidance (no exact check measures them).
    guidance_only_rules: list[str] = Field(default_factory=list)
    #: How the geometry was measured: the tool, method, limits, sample sizes,
    #: build direction and the planar faces anchors point at.
    exact_evidence: dict[str, Any] = Field(default_factory=dict)
    verification_items: list[VerificationItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _never_passed(self) -> DfmFindingSet:
        if self.kind == "heuristic":
            if self.status not in HEURISTIC_STATUSES or not self.heuristic_only:
                raise ValueError(
                    f"a heuristic finding set cannot be {self.status.value}: heuristics alone "
                    "never pass, qualify or release anything"
                )
            return self
        if self.heuristic_only:
            raise ValueError("an exact finding set is not heuristic only")
        if self.status not in EXACT_STATUSES:
            raise ValueError(
                f"an exact finding set cannot be {self.status.value}: verification and "
                "release belong to the product, not to one analysis"
            )
        if self.status is DfmStatus.ANALYSIS_PASSED and (
            not self.complete or self.findings or not self.checks_required
        ):
            raise ValueError(
                "an exact finding set passes only when every required check ran and none "
                "found anything"
            )
        return self

    @property
    def status_label(self) -> str:
        return DFM_STATUS_LABELS[self.status]

    def open_findings(self) -> list[DfmFinding]:
        """Findings no person has decided to keep the design for."""
        return [
            f
            for f in self.findings
            if not (f.decision and f.decision.get("decision") == "keep_design")
        ]


# ---------------------------------------------------------------------------
# Status and note
# ---------------------------------------------------------------------------


def finding_set_status(
    findings: list[DfmFinding], rules_run: list[str], *, exact_checks_scheduled: bool = False
) -> DfmStatus:
    """DFM-013 status of a heuristic run. Never ``analysis_passed``."""
    if findings:
        return DfmStatus.ISSUES_FOUND
    if exact_checks_scheduled:
        return DfmStatus.ANALYSIS_PENDING
    if rules_run:
        return DfmStatus.PRELIMINARY_GUIDANCE
    return DfmStatus.NOT_ASSESSED


def product_status(statuses: list[DfmStatus]) -> DfmStatus:
    """The worst of several components' statuses; ``not_assessed`` for none."""
    for status in _STATUS_RANK:
        if status in statuses:
            return status
    return DfmStatus.NOT_ASSESSED


_SUPPORT_RULES = frozenset({"fdm.overhang", "fdm.support_access"})


def finding_note(
    status: DfmStatus,
    *,
    profile: DfmProfile | None,
    findings: list[DfmFinding],
    rules_run: list[str],
    reason: str = "",
) -> str:
    """The short, truthful note a concept card or output shows (section 10).

    Grounded only in rules that ran. "Likely printable" is said only for FDM
    and only when the overhang rule actually ran; nothing says verified.
    """
    process = profile.process if profile else "the process"
    if status is DfmStatus.NOT_ASSESSED:
        label = DFM_STATUS_LABELS[DfmStatus.NOT_ASSESSED]
        return f"{label} ({reason})" if reason else label
    if status is DfmStatus.ANALYSIS_PENDING:
        return DFM_STATUS_LABELS[DfmStatus.ANALYSIS_PENDING]
    fdm_overhang_ran = profile is not None and profile.id == "fdm" and "fdm.overhang" in rules_run
    if status is DfmStatus.PRELIMINARY_GUIDANCE:
        if fdm_overhang_ran:
            return "Likely printable without supports (preliminary)"
        n = len(rules_run)
        return f"No issues in {n} preliminary {process} check{'s' if n != 1 else ''}"
    blocking = [f for f in findings if f.severity == "blocking"]
    if blocking:
        n = len(blocking)
        return (
            f"{n} blocking manufacturing issue{'s' if n != 1 else ''} for {process} (preliminary)"
        )
    if fdm_overhang_ran and findings and all(f.rule_id in _SUPPORT_RULES for f in findings):
        if any(f.rule_id == "fdm.support_access" for f in findings):
            return "Printable only with supports that may be hard to remove (preliminary)"
        return "Likely printable with supports (preliminary)"
    n = len(findings)
    return f"{n} manufacturing suggestion{'s' if n != 1 else ''} for {process} (preliminary)"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def finding_id(component_id: str, rule_id: str, feature: str | None, face: str | None) -> str:
    """Stable across recomputes, so a person's decision follows the finding."""
    raw = f"{component_id}|{rule_id}|{feature or ''}|{face or ''}"
    return "dfmf-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{float(value):.3g}"
    if isinstance(value, (list, tuple)):
        return " x ".join(f"{float(v):.4g}" for v in value)
    return str(value)


def _weakest(measures: list[Measure]) -> Confidence:
    order = {c: i for i, c in enumerate(_CONFIDENCE_ORDER)}
    worst = min(measures, key=lambda m: order[m.confidence]).confidence if measures else "estimated"
    return worst


@dataclass
class _Limit:
    value: float | list[float] | None
    kind: RuleKind
    source: str
    missing: str = ""


def _limit_for(rule: DfmRule, machine: MachineContext | None) -> _Limit:
    """The number a rule compares against, and where it came from."""
    if rule.kind is RuleKind.MACHINE_LIMIT:
        if machine is None:
            return _Limit(None, rule.kind, rule.source, missing="the machine is unknown")
        cap = machine.capabilities.get(str(rule.machine_capability))
        if cap is None or cap.value is None:
            what = str(rule.machine_capability).replace("_", " ")
            return _Limit(None, rule.kind, rule.source, missing=f"{machine.name} records no {what}")
        return _Limit(cap.value, rule.kind, cap.source)
    if rule.machine_override and machine is not None:
        cap = machine.capabilities.get(rule.machine_override)
        if (
            cap is not None
            and isinstance(cap.value, (int, float))
            and not isinstance(cap.value, bool)
        ):
            return _Limit(
                float(cap.value) * rule.machine_override_scale, RuleKind.MACHINE_LIMIT, cap.source
            )
    return _Limit(rule.limit, rule.kind, rule.source)


def _severity(kind: RuleKind, confidence: str) -> Severity:
    return "blocking" if kind in LIMIT_KINDS and confidence in FIRM_CONFIDENCE else "advisory"


def _numbers(measures: list[Measure]) -> list[Measure]:
    return [
        m for m in measures if isinstance(m.value, (int, float)) and not isinstance(m.value, bool)
    ]


def _with_derived(geometry: ComponentGeometry) -> list[Measure]:
    """The measures, plus the thickest wall when several walls are known."""
    measures = list(geometry.measures)
    walls = _numbers(geometry.of("wall_thickness"))
    if not geometry.of("wall_thickness_max") and len(walls) >= 2:
        thickest = max(walls, key=lambda m: float(m.value))  # type: ignore[arg-type]
        measures.append(
            Measure(
                quantity="wall_thickness_max",
                value=float(thickest.value),  # type: ignore[arg-type]
                unit=thickest.unit,
                confidence=_weakest(walls),
                source=thickest.source,
                feature=thickest.feature,
                face_signature=thickest.face_signature,
                evidence_ref=thickest.evidence_ref,
            )
        )
    return measures


class _Run:
    """One profile's evaluation of one component."""

    def __init__(
        self,
        geometry: ComponentGeometry,
        context: ProcessContext,
        profile: DfmProfile,
        decisions: dict[str, dict[str, Any]],
    ) -> None:
        self.geometry = geometry
        self.context = context
        self.profile = profile
        self.decisions = decisions
        self.measures = _with_derived(geometry)
        self.family = material_family(context.material)
        self.findings: list[DfmFinding] = []
        self.not_assessed: list[NotAssessed] = []
        self.rules_run: list[str] = []
        #: Flagged overhang faces, for the support access rule.
        self.flagged_faces: list[dict[str, Any]] | None = None

    def of(self, quantity: str) -> list[Measure]:
        return [m for m in self.measures if m.quantity == quantity]

    def skip(self, rule: DfmRule, reason: str) -> None:
        self.not_assessed.append(NotAssessed(rule_id=rule.id, title=rule.title, reason=reason))

    def unknown(self, rule: DfmRule, quantity: str) -> None:
        self.skip(rule, f"{QUANTITY_WORDS.get(quantity, quantity.replace('_', ' '))} is unknown")

    def add(
        self,
        rule: DfmRule,
        limit: _Limit,
        *,
        measured: Any,
        limit_value: Any,
        confidence: Confidence,
        feature: str | None,
        face: str | None,
        evidence: list[str],
        comparison: str,
        faces: list[str] | None = None,
        count: int = 1,
    ) -> None:
        fid = finding_id(self.geometry.component_id, rule.id, feature, face)
        words = {
            "measured": _fmt(measured),
            "limit": _fmt(limit_value),
            "unit": rule.unit if rule.unit != "ratio" else "",
            "count": str(count),
        }
        self.findings.append(
            DfmFinding(
                id=fid,
                rule_id=rule.id,
                rule_title=rule.title,
                profile_id=self.profile.id,
                profile_version=self.profile.version,
                process=self.profile.process,
                rule_kind=limit.kind,
                severity=_severity(limit.kind, confidence),
                anchor=Anchor(
                    component_id=self.geometry.component_id,
                    feature=feature,
                    face_signature=face,
                    faces=list(faces or [])[:20],
                ),
                quantity=rule.quantity,
                measured=measured if isinstance(measured, (int, float, str)) else _fmt(measured),
                limit=limit_value
                if isinstance(limit_value, (int, float, str))
                else _fmt(limit_value),
                unit=rule.unit,
                comparison=comparison,
                confidence=confidence,
                limit_source=limit.source,
                evidence_refs=[e for e in dict.fromkeys(evidence) if e],
                message=rule.message.format(**words).replace("  ", " ").strip(),
                suggested_fix=rule.fix.format(**words).replace("  ", " ").strip(),
                fix_changes=list(rule.fix_changes),
                decision=self.decisions.get(fid),
            )
        )

    # -- the checks --------------------------------------------------------

    def run(self) -> None:
        for rule in self.profile.rules:
            if rule.material_families:
                if self.family is None:
                    self.skip(rule, "the material is unknown")
                    continue
                if self.family not in rule.material_families:
                    continue  # does not apply to this material
            getattr(self, f"_check_{rule.check}")(rule)

    def _relative(self, rule: DfmRule, measure: Measure, pick_max: bool) -> float | None:
        if not rule.relative_to or rule.factor is None:
            return None
        pool = _numbers(self.of(rule.relative_to))
        if not pool:
            return None
        # Pair with the same feature's base (a pocket's own depth), unless the
        # base is the same kind of measure (the thinnest wall for the thickest).
        pairable = rule.quantity.split("_")[0] != rule.relative_to.split("_")[0]
        same = [m for m in pool if pairable and measure.feature and m.feature == measure.feature]
        values = [float(m.value) for m in (same or pool)]  # type: ignore[arg-type]
        base = max(values) if pick_max else min(values)
        return base * rule.factor

    def _check_min(self, rule: DfmRule) -> None:
        self._bound(rule, "min")

    def _check_max(self, rule: DfmRule) -> None:
        self._bound(rule, "max")

    def _bound(self, rule: DfmRule, side: str) -> None:
        names = [rule.quantity, *(rule.also_reads if side == "min" else [])]
        measures = _numbers([m for n in names for m in self.of(n)])
        if not measures:
            self.unknown(rule, rule.quantity)
            return
        limit = _limit_for(rule, self.context.machine)
        if limit.missing:
            self.skip(rule, limit.missing)
            return
        if rule.relative_to and limit.kind is not RuleKind.MACHINE_LIMIT:
            if not _numbers(self.of(rule.relative_to)):
                self.unknown(rule, rule.relative_to)
                return
        self.rules_run.append(rule.id)
        for m in measures:
            value = float(m.value)  # type: ignore[arg-type]
            bound: float | None
            if rule.relative_to and limit.kind is not RuleKind.MACHINE_LIMIT:
                # A min check against the largest base, a max check against the smallest.
                bound = self._relative(rule, m, pick_max=side == "min")
            else:
                bound = float(limit.value) if isinstance(limit.value, (int, float)) else None
            if bound is None:
                continue
            bad = value < bound - 1e-9 if side == "min" else value > bound + 1e-9
            if not bad:
                continue
            confidence = m.confidence
            evidence = [m.evidence_ref or ""]
            if rule.relative_to:
                base = _numbers(self.of(rule.relative_to))
                confidence = _weakest([m, *base])
                evidence += [b.evidence_ref or "" for b in base]
            self.add(
                rule,
                limit,
                measured=round(value, 4),
                limit_value=round(bound, 4),
                confidence=confidence,
                feature=m.feature,
                face=m.face_signature,
                evidence=evidence,
                comparison=">=" if side == "min" else "<=",
            )

    def _check_envelope(self, rule: DfmRule) -> None:
        envelopes = [
            m for m in self.of("envelope") if isinstance(m.value, list) and len(m.value) == 3
        ]
        if not envelopes:
            self.unknown(rule, "envelope")
            return
        limit = _limit_for(rule, self.context.machine)
        if limit.missing:
            self.skip(rule, limit.missing)
            return
        dims = limit.value
        if not (isinstance(dims, (list, tuple)) and len(dims) == 3):
            self.skip(rule, "the machine's working volume is not three numbers")
            return
        self.rules_run.append(rule.id)
        box = sorted(float(d) for d in dims)
        for m in envelopes:
            part = sorted(float(v) for v in m.value)  # type: ignore[union-attr]
            if all(p <= b + 1e-9 for p, b in zip(part, box, strict=True)):
                continue
            self.add(
                rule,
                limit,
                measured=_fmt(m.value),
                limit_value=_fmt(dims),
                confidence=m.confidence,
                feature=m.feature,
                face=None,
                evidence=[m.evidence_ref or ""],
                comparison="fits within",
            )

    def _check_overhang(self, rule: DfmRule) -> None:
        faces = self.geometry.faces
        if not faces:
            self.unknown(rule, "faces")
            return
        limit = _limit_for(rule, self.context.machine)
        threshold = float(limit.value) if isinstance(limit.value, (int, float)) else 45.0
        table = [
            {"name": f.face_id, "normal": list(f.normal), "area_mm2": f.area_mm2} for f in faces
        ]
        checked = compute_overhang_faces(
            table,
            build_axis=self.geometry.build_axis,
            threshold_deg=threshold,
            directed=self.geometry.faces_directed,
        )
        self.rules_run.append(rule.id)
        flagged = [c for c in checked if c["flagged"]]
        self.flagged_faces = flagged
        if not flagged:
            return
        by_id = {f.face_id: f for f in faces}
        names = [by_id[c["name"]].face_signature or c["name"] for c in flagged]
        largest = max(flagged, key=lambda c: float(c.get("area_mm2") or 0.0))
        anchor = by_id[largest["name"]].face_signature or largest["name"]
        self.add(
            rule,
            limit if limit.value is not None else _Limit(threshold, rule.kind, rule.source),
            measured=max(float(c["tilt_from_vertical_deg"]) for c in flagged),
            limit_value=threshold,
            confidence=self.geometry.faces_confidence,
            feature=None,
            face=anchor,
            faces=names,
            evidence=[self.geometry.faces_evidence_ref or ""],
            comparison="<=",
            count=len(flagged),
        )

    def _check_support_access(self, rule: DfmRule) -> None:
        if self.flagged_faces is None:
            self.unknown(rule, "faces")
            return
        cavity = [m for m in self.of("has_internal_cavity") if isinstance(m.value, bool)]
        if not cavity:
            self.unknown(rule, "has_internal_cavity")
            return
        self.rules_run.append(rule.id)
        if not any(m.value for m in cavity) or not self.flagged_faces:
            return
        m = next(c for c in cavity if c.value)
        self.add(
            rule,
            _Limit(None, rule.kind, rule.source),
            measured="enclosed cavity with overhangs",
            limit_value="supports reachable",
            confidence=_weakest([m])
            if self.geometry.faces_confidence != "estimated"
            else "estimated",
            feature=m.feature,
            face=None,
            evidence=[m.evidence_ref or "", self.geometry.faces_evidence_ref or ""],
            comparison="reachable",
        )

    def _check_requires(self, rule: DfmRule) -> None:
        flags = [m for m in self.of(rule.quantity) if isinstance(m.value, bool)]
        if not flags:
            self.unknown(rule, rule.quantity)
            return
        self.rules_run.append(rule.id)
        trigger = next((m for m in flags if m.value), None)
        if trigger is None:
            return
        limit = _limit_for(rule, self.context.machine)
        need = float(limit.value) if isinstance(limit.value, (int, float)) else 0.0
        have = _numbers(self.of(str(rule.relative_to)))
        if have and max(float(h.value) for h in have) >= need - 1e-9:  # type: ignore[arg-type]
            return
        best = max(have, key=lambda h: float(h.value)) if have else None  # type: ignore[arg-type]
        self.add(
            rule,
            limit,
            measured=round(float(best.value), 4) if best else "none",  # type: ignore[arg-type]
            limit_value=need,
            confidence=_weakest([trigger, *have]),
            feature=trigger.feature,
            face=None,
            evidence=[trigger.evidence_ref or "", *(h.evidence_ref or "" for h in have)],
            comparison=">=",
        )


def _inputs_hash(
    geometry: ComponentGeometry, context: ProcessContext, profile: DfmProfile | None
) -> str:
    raw = {
        "profile": profile.ref if profile else None,
        "geometry": geometry.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }
    import json

    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()


def resolve_profile(context: ProcessContext) -> DfmProfile | None:
    return profile_for_process(context.process)


def evaluate(
    geometry: ComponentGeometry,
    context: ProcessContext,
    *,
    profile: DfmProfile | None = None,
    exact_checks_scheduled: bool = False,
    decisions: dict[str, dict[str, Any]] | None = None,
) -> DfmFindingSet:
    """One component's heuristic findings under its process profile.

    With no process (or one no profile covers) nothing runs: the set is
    ``not_assessed`` with the reason. ``decisions`` are a person's earlier
    decisions by finding id, carried onto findings that recur.
    """
    with tracer.start_as_current_span("twin.dfm.evaluate") as span:
        span.set_attribute("dfm.component_id", geometry.component_id)
        chosen = profile or resolve_profile(context)
        span.set_attribute("dfm.profile", chosen.ref if chosen else "none")
        base: dict[str, Any] = {
            "component_id": geometry.component_id,
            "component_name": geometry.name,
            "process": chosen.process if chosen else context.process,
            "process_authority": context.process_authority,
            "geometry_level": geometry.level,
            "geometry_refs": list(geometry.geometry_refs),
            "context_ref": context.context_ref,
            "exact_checks_scheduled": exact_checks_scheduled,
            "inputs_sha256": _inputs_hash(geometry, context, chosen),
        }
        if chosen is None:
            reason = (
                "the process is unknown"
                if not context.process
                else f"no rule profile covers {context.process}"
            )
            status = finding_set_status([], [], exact_checks_scheduled=exact_checks_scheduled)
            out = DfmFindingSet(
                **base,
                not_assessed=[NotAssessed(rule_id="*", title="manufacturability", reason=reason)],
                status=status,
                note=finding_note(status, profile=None, findings=[], rules_run=[], reason=reason),
            )
            logger.info(
                "dfm_evaluated",
                component_id=geometry.component_id,
                profile=None,
                status=status.value,
                reason=reason,
            )
            return out
        run = _Run(geometry, context, chosen, decisions or {})
        run.run()
        status = finding_set_status(
            run.findings, run.rules_run, exact_checks_scheduled=exact_checks_scheduled
        )
        out = DfmFindingSet(
            **base,
            profile_id=chosen.id,
            profile_version=chosen.version,
            findings=run.findings,
            not_assessed=run.not_assessed,
            rules_run=run.rules_run,
            status=status,
            note=finding_note(
                status, profile=chosen, findings=run.findings, rules_run=run.rules_run
            ),
        )
        span.set_attribute("dfm.findings", len(out.findings))
        span.set_attribute("dfm.status", status.value)
        logger.info(
            "dfm_evaluated",
            component_id=geometry.component_id,
            profile=chosen.ref,
            status=status.value,
            findings=len(out.findings),
            not_assessed=len(out.not_assessed),
            level=geometry.level,
        )
        return out


# ---------------------------------------------------------------------------
# Generation guidance (DFM-003, DFM-005)
# ---------------------------------------------------------------------------


@dataclass
class VariantGuidance:
    """The cue block for one manufacturability-oriented concept variant."""

    profile_id: str
    profile_version: str
    process: str
    text: str
    #: ``[{rule_id, profile_version, kind, source}]``: what shaped the variant.
    rules: list[dict[str, Any]] = field(default_factory=list)


def variant_guidance(profile: DfmProfile, *, material: str | None = None) -> VariantGuidance:
    """Guidance text for a variant shaped by ``profile``'s rules. A proposal, not a constraint."""
    family = material_family(material)
    used = [
        r
        for r in profile.rules
        if r.variant_cue and (not r.material_families or family in r.material_families)
    ]
    cues = list(dict.fromkeys(r.variant_cue for r in used))
    text = (
        f"Manufacturability-oriented variant for {profile.process} (preliminary guidance from "
        f"the {profile.id} rule profile {profile.version}, not a checked constraint set): "
        + "; ".join(cues)
        + ". Keep the person's distinctive features; this is one option among others, "
        "never presented as manufacturable or proven."
    )
    return VariantGuidance(
        profile_id=profile.id,
        profile_version=profile.version,
        process=profile.process,
        text=text,
        rules=[
            {
                "rule_id": r.id,
                "profile_version": profile.version,
                "kind": r.kind.value,
                "source": r.source,
            }
            for r in used
        ],
    )


def alternative_profiles(current: DfmProfile | None) -> list[DfmProfile]:
    """Every other profile, for "explore another process"."""
    return [p for p in PROFILES.values() if current is None or p.id != current.id]


__all__ = [
    "EXACT_STATUSES",
    "FIRM_CONFIDENCE",
    "HEURISTIC_STATUSES",
    "QUANTITY_WORDS",
    "Anchor",
    "ComponentGeometry",
    "Confidence",
    "DfmFinding",
    "DfmFindingSet",
    "FaceSample",
    "MachineCapability",
    "MachineContext",
    "Measure",
    "NotAssessed",
    "ProcessContext",
    "VariantGuidance",
    "VerificationItem",
    "alternative_profiles",
    "evaluate",
    "finding_id",
    "finding_note",
    "finding_set_status",
    "material_family",
    "product_status",
    "resolve_profile",
    "variant_guidance",
]
