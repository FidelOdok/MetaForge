"""Proposed feature histories: parametric reconstruction of an accepted concept.

Spec section 4B: an accepted concept is translated into a *proposed* editable
feature history per component (sketch profile, extrude or revolve, shell,
openings, bosses, ribs). It is an inference, never a recovery of original
features, so every number says where it came from:

``estimated``
    read from the concept's L1 block layout (approximate sizes in mm) or from
    a reference fact whose basis is estimated;
``confirmed``
    stated by an authoritative reference (a specification or a manufacturing
    rule), carried from an accepted parametric sketch, or given by a person
    when accepting;
``unknown``
    nothing says it. It stays a question (``required_input`` once accepted).

Nothing here reads a size from pixels: the only numbers are the L1 layout's
estimated dimensions, stated or estimated reference facts, a parametric
sketch's own values and what a person types. An optional model pass may name
and order features and suggest missing ones, never supply a number
(:func:`apply_llm_pass`).

The DFM strategy note (DFM-005) names the process rule profile and the rules
that shaped the strategy (a wall at least the profile's minimum, openings at
least its smallest hole, a printable split when the part exceeds the
machine's build volume), each with its limit, kind and source. A rule limit
is a bound a person must respect, never a dimension: a shell's thickness
stays unknown with the minimum beside it.

:func:`accept_features` turns accepted features into parametric sketches
(:class:`~twin_core.design_ir.sketch_compiler.ParametricSketch`) whose
feature plan keeps every parameter a person has not confirmed as
``required_input``. Features the exact path cannot build yet (shell,
openings, bosses, ribs) are kept as accepted intent on the sketch.

Pure: Pydantic and the standard library only. The gateway gathers the inputs
and stores the proposal (``api_gateway.twin.reconstruction``).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from twin_core.design_ir.models import SketchCircle, SketchElement, SketchLine, SketchRectangle
from twin_core.design_ir.sketch_compiler import (
    ClosedProfile,
    FeatureParameter,
    FeaturePlanItem,
    ParametricSketch,
)
from twin_core.dfm.profiles import DfmProfile, DfmRule, profile_for_process

SCHEMA = "metaforge.feature_history_proposal/1"
#: Metadata key on a reconstructed parametric sketch: the principal plane
#: (``{"plane", "offset"}``) the person accepted with the proposal. The exact
#: path and the L2 preview place a sketch with no plane item there.
PRINCIPAL_PLACEMENT_KEY = "principal_placement"

FeatureKind = Literal[
    "sketch_profile", "extrude", "revolve", "loft", "shell", "opening", "boss", "rib"
]
ParamStatus = Literal["estimated", "confirmed", "unknown"]
SourceKind = Literal[
    "l1_layout",
    "parametric_sketch",
    "reference_fact",
    "manufacturing_context",
    "dfm_profile",
    "shape",
    "person",
    "llm",
]
PrincipalPlane = Literal["XY", "YZ", "XZ"]

#: Kinds the exact path builds (one profile and one extrude or revolve).
BODY_KINDS: frozenset[str] = frozenset({"extrude", "revolve"})
#: Kinds kept as accepted intent until the exact path builds them.
PENDING_KINDS: frozenset[str] = frozenset({"loft", "shell", "opening", "boss", "rib"})
#: Order of feature kinds in a history (a profile first, details last).
_KIND_ORDER: dict[str, int] = {
    "sketch_profile": 0,
    "extrude": 1,
    "revolve": 1,
    "loft": 1,
    "shell": 2,
    "opening": 3,
    "boss": 4,
    "rib": 5,
}

#: How sure the concept layout is of a part (its provenance tag).
_LAYOUT_CONFIDENCE: dict[str, float] = {
    "verified": 0.9,
    "intended": 0.7,
    "assumption": 0.5,
    "open": 0.3,
}

_ENCLOSURE_WORDS = (
    "enclosure",
    "housing",
    "case",
    "casing",
    "shell",
    "cover",
    "shade",
    "hollow",
    "cabinet",
)
_OPENING_WORDS = (
    "opening",
    "hole",
    "port",
    "cutout",
    "cut out",
    "slot",
    "window",
    "vent",
    "grille",
    "aperture",
    "socket",
    "usb",
    "jack",
    "connector",
    "button",
    "driver",
)
_FASTENER_WORDS = ("screw", "bolt", "boss", "thread", "fastener", "insert", "rivet")
_LENGTH_TO_MM: dict[str, float] = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _words(text: str | None) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip() + " "


def _has_word(text: str | None, words: tuple[str, ...]) -> bool:
    hay = _words(text)
    return any(_words(w) in hay for w in words)


def _fmt(value: float) -> str:
    out = f"{value:.2f}".rstrip("0").rstrip(".")
    return out or "0"


# ---------------------------------------------------------------------------
# The proposal record
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Where a value or a feature came from."""

    kind: SourceKind
    #: ``KEY@n``, a node id, a reference id with its region, a rule id.
    ref: str | None = Field(default=None, max_length=300)
    detail: str = Field(default="", max_length=400)


class ProposedParameter(BaseModel):
    """One parameter of a proposed feature, with how far it can be trusted."""

    value: float | str | None = None
    unit: str | None = Field(default=None, max_length=16)
    status: ParamStatus = "unknown"
    source: Provenance | None = None
    question: str = Field(default="", max_length=300)
    #: A bound from a DFM rule (a wall at least this thick). Never a value.
    minimum: float | None = None
    minimum_rule: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _status_matches_value(self) -> ProposedParameter:
        if self.status == "unknown":
            self.value = None
        elif self.value in (None, ""):
            raise ValueError(f"a {self.status} parameter needs a value")
        return self


class SketchPlacement(BaseModel):
    """The principal plane a profile is drawn on, and its offset along the normal (mm)."""

    plane: PrincipalPlane
    offset: float = 0.0
    status: ParamStatus = "estimated"
    source: Provenance | None = None


class ProposedFeature(BaseModel):
    """One step of a proposed feature history."""

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    kind: FeatureKind
    #: Engineering name (``Main profile``, ``Extrude``, ``Speaker opening``).
    name: str = Field(min_length=1, max_length=120)
    #: Plain words for Create (``a round base``).
    plain: str = Field(default="", max_length=200)
    depends_on: list[str] = Field(default_factory=list)
    parameters: dict[str, ProposedParameter] = Field(default_factory=dict)
    #: A sketch profile's plane.
    placement: SketchPlacement | None = None
    #: A sketch profile's elements (sketch space, y down, millimetres).
    profile: list[SketchElement] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    suggested_by: Literal["deterministic", "llm"] = "deterministic"
    provenance: list[Provenance] = Field(default_factory=list)
    #: DFM rule ids that shaped this feature (DFM-005).
    dfm_rules: list[str] = Field(default_factory=list)
    #: The exact path builds it (a profile and an extrude or revolve).
    buildable: bool = False
    #: Carried from an existing parametric sketch (``PSK-...@n``).
    existing_ref: str | None = None
    status: Literal["proposed", "accepted", "rejected"] = "proposed"
    note: str = Field(default="", max_length=400)


class Assumption(BaseModel):
    id: str
    text: str = Field(max_length=400)
    feature_id: str | None = None
    source: Provenance | None = None


class ChoiceOption(BaseModel):
    id: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)


class Ambiguity(BaseModel):
    """An interpretation a person must settle (asked just in time in Create)."""

    id: str
    question: str = Field(max_length=300)
    options: list[ChoiceOption] = Field(default_factory=list)
    feature_id: str | None = None
    #: The answer changes the geometry or the route, so it is never assumed.
    consequential: bool = True
    #: The option the proposal was drawn with, when there is one.
    default: str | None = None
    choice: str | None = None


class DfmRuleUse(BaseModel):
    """One rule that shaped the strategy, with its number and where it came from."""

    rule_id: str
    title: str
    kind: str
    limit: float | None = None
    unit: str = ""
    source: str
    applied_as: str = Field(max_length=400)


class DfmStrategy(BaseModel):
    """Why the chosen feature strategy matches the manufacturing route (DFM-005)."""

    profile_id: str | None = None
    profile_version: str | None = None
    process: str | None = None
    process_authority: str = "unknown"
    context_ref: str | None = None
    rules: list[DfmRuleUse] = Field(default_factory=list)
    text: str = Field(max_length=2000)


class LlmPass(BaseModel):
    """What the optional model pass did. It never supplies a number."""

    requested: bool = False
    outcome: Literal["not_requested", "used", "unavailable", "invalid"] = "not_requested"
    provider: str | None = None
    model: str | None = None
    renamed: int = 0
    reordered: bool = False
    suggestions: int = 0
    ignored: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=400)


class ProposalInputs(BaseModel):
    """The revisions and records a proposal read (its ``depends_on``)."""

    assembly_decision_id: str | None = None
    layout_ref: str | None = None
    layout_node_id: str | None = None
    mesh_node_id: str | None = None
    reference_ids: list[str] = Field(default_factory=list)
    sketch_refs: list[str] = Field(default_factory=list)
    context_ref: str | None = None


class FeatureHistoryProposal(BaseModel):
    """A proposed editable feature history for one component."""

    schema_version: str = SCHEMA
    component_id: str
    component_name: str
    status: Literal["proposed", "accepted", "partially_accepted"] = "proposed"
    features: list[ProposedFeature] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    dfm_strategy: DfmStrategy
    #: Plain words for Create: ``a round base, hollowed out``.
    summary: str = Field(max_length=400)
    inputs: ProposalInputs = Field(default_factory=ProposalInputs)
    llm: LlmPass = Field(default_factory=LlmPass)
    inputs_sha256: str = ""
    created_at: str = Field(default_factory=_now)
    #: Set on acceptance: ``{feature_id: PSK-...@n}``.
    accepted_sketches: dict[str, str] = Field(default_factory=dict)

    def feature(self, feature_id: str) -> ProposedFeature | None:
        return next((f for f in self.features if f.id == feature_id), None)


# ---------------------------------------------------------------------------
# Inputs the gateway gathers
# ---------------------------------------------------------------------------


@dataclass
class FactInput:
    """One reference fact that may apply to the component."""

    reference_id: str
    role: str
    parameter: str
    value: float | None
    unit: str
    basis: str
    region: str = ""
    text: str = ""
    #: The reference is associated with this component (not only the project).
    about_component: bool = False

    @property
    def cite(self) -> str:
        return f"{self.reference_id} ({self.region})" if self.region else self.reference_id


@dataclass
class ContextFacts:
    """What the manufacturing context says, as reconstruction reads it."""

    process: str | None = None
    process_authority: str = "unknown"
    material: str | None = None
    material_authority: str = "unknown"
    intended_use: str | None = None
    production_goal: str | None = None
    fastening: str | None = None
    context_ref: str | None = None
    #: The selected machine's build volume, with its library source.
    build_volume_mm: tuple[float, float, float] | None = None
    build_volume_source: str = ""


@dataclass
class ReconstructionInputs:
    component_id: str
    component_name: str
    #: The component's block in the concept layout (``size_mm``, ``position_mm``,
    #: ``shape``, ``axis``, ``provenance``), or ``None``.
    part: dict[str, Any] | None = None
    layout_ref: str | None = None
    #: ``(PSK-...@n, sketch)`` bound to the component.
    sketches: list[tuple[str, ParametricSketch]] = field(default_factory=list)
    context: ContextFacts = field(default_factory=ContextFacts)
    facts: list[FactInput] = field(default_factory=list)
    #: Joints this component takes part in (``{"other", "joint"}``).
    connections: list[dict[str, str]] = field(default_factory=list)
    #: The only made component of the product (project-level facts apply to it).
    only_component: bool = False


def inputs_hash(inputs: ReconstructionInputs, *, use_llm: bool) -> str:
    raw = {
        "component": [inputs.component_id, inputs.component_name],
        "part": inputs.part,
        "layout": inputs.layout_ref,
        "sketches": [ref for ref, _ in inputs.sketches],
        "context": inputs.context.__dict__,
        "facts": [f.__dict__ for f in inputs.facts],
        "connections": inputs.connections,
        "llm": use_llm,
    }
    blob = json.dumps(raw, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Geometry from an L1 block (estimated)
# ---------------------------------------------------------------------------


def _vec(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, list | tuple) and len(value) == 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None
    return None


def part_box(part: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """``(position, size)`` in mm of a layout block (``size_mm`` or ``bbox_mm``)."""
    size = _vec(part.get("size_mm"))
    pos = _vec(part.get("position_mm")) or (0.0, 0.0, 0.0)
    bbox = part.get("bbox_mm")
    if size is None and isinstance(bbox, list | tuple) and len(bbox) == 2:
        lo, hi = _vec(bbox[0]), _vec(bbox[1])
        if lo and hi:
            size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
            pos = lo
    if size is None or min(size) <= 0:
        raise ValueError("the layout block has no usable size")
    return pos, size


#: ``axis -> (plane, u index, v index, along index)``.
_AXIS_PLANES: dict[str, tuple[PrincipalPlane, int, int, int]] = {
    "z": ("XY", 0, 1, 2),
    "x": ("YZ", 1, 2, 0),
    "y": ("XZ", 0, 2, 1),
}


def _plane_offset(axis: str, pos: tuple[float, ...], size: tuple[float, ...]) -> float:
    if axis == "y":
        # CadQuery's XZ normal is -Y: sketch on the far face, extrude back.
        return -(pos[1] + size[1])
    _, _, _, along = _AXIS_PLANES[axis]
    return pos[along]


def _rect_on_plane(u0: float, v0: float, w: float, h: float, *, estimated: bool) -> SketchRectangle:
    # Sketch space has y down; ParametricSketch.to_sketch_entity mirrors v.
    return SketchRectangle(
        id="profile-rect", origin=(u0, -(v0 + h)), width=w, height=h, estimated=estimated
    )


def _circle_on_plane(cu: float, cv: float, r: float, *, estimated: bool) -> SketchCircle:
    return SketchCircle(id="profile-circle", center=(cu, -cv), radius=r, estimated=estimated)


def _revolve_profile(r: float, v0: float, h: float, *, estimated: bool) -> list[SketchElement]:
    """A half section on XZ about the vertical axis (``u = 0``), and the axis itself."""
    return [
        SketchRectangle(
            id="profile-rect", origin=(0.0, -(v0 + h)), width=r, height=h, estimated=estimated
        ),
        SketchLine(id="axis", start=(0.0, -v0), end=(0.0, -(v0 + h)), construction=True),
    ]


# ---------------------------------------------------------------------------
# Reference facts
# ---------------------------------------------------------------------------


def _fact_mm(fact: FactInput) -> float | None:
    scale = _LENGTH_TO_MM.get(fact.unit.strip().lower())
    if fact.value is None or scale is None or fact.value <= 0:
        return None
    return float(fact.value) * scale


def _param_for_fact(fact: FactInput) -> str | None:
    words = _words(fact.parameter)
    if " wall " in words:
        return "wall"
    if " diameter " in words or " radius " in words:
        return "diameter"
    if " width " in words:
        return "width"
    for w in (" height ", " length ", " depth ", " thickness ", " thick ", " tall "):
        if w in words:
            return "depth"
    return None


def _fact_param(fact: FactInput, *, unit: str = "mm") -> ProposedParameter | None:
    mm = _fact_mm(fact)
    if mm is None:
        return None
    stated = fact.basis == "stated"
    return ProposedParameter(
        value=round(mm, 4),
        unit=unit,
        status="confirmed" if stated else "estimated",
        source=Provenance(
            kind="reference_fact",
            ref=fact.cite,
            detail=(
                f"{'stated' if stated else 'estimated'} in a {fact.role} reference: "
                f"{fact.parameter} {_fmt(float(fact.value or 0))} {fact.unit}"
            ),
        ),
    )


# ---------------------------------------------------------------------------
# DFM strategy (DFM-005)
# ---------------------------------------------------------------------------

#: Quantities each feature kind is shaped by.
_KIND_QUANTITIES: dict[str, tuple[str, ...]] = {
    "shell": ("wall_thickness", "thinnest_section"),
    "opening": ("hole_diameter",),
    "boss": ("feature_size", "rib_thickness"),
    "rib": ("rib_thickness", "feature_size"),
    "extrude": ("envelope", "draft_angle"),
    "revolve": ("envelope",),
}


def _rule_applies(rule: DfmRule, quantities: tuple[str, ...]) -> bool:
    if rule.check == "envelope":
        return "envelope" in quantities
    return rule.quantity in quantities or any(q in quantities for q in rule.also_reads)


def _applied_as(rule: DfmRule, kind: str) -> str:
    limit = (
        f"{_fmt(rule.limit)} {rule.unit}"
        if rule.limit is not None
        else "the selected machine's recorded limit"
    )
    if rule.check == "envelope":
        return "split the part where it exceeds the machine's build volume (a printable split)"
    if kind == "shell":
        return f"shell wall at least {limit}"
    if kind == "opening":
        return f"openings at least {limit} across, cut through accessible faces"
    if kind in ("boss", "rib"):
        return f"{kind}s at least {limit}"
    if rule.check == "min":
        return f"{rule.title.lower()} at least {limit}"
    if rule.check == "max":
        return f"{rule.title.lower()} at most {limit}"
    return f"{rule.title.lower()}: {limit}"


def dfm_strategy(
    profile: DfmProfile | None,
    context: ContextFacts,
    features: list[ProposedFeature],
    *,
    component_name: str,
) -> DfmStrategy:
    """The rules that shaped this strategy, and why it fits the route."""
    if profile is None:
        reason = (
            "no process is chosen yet"
            if not context.process
            else f"no rule profile covers {context.process}"
        )
        return DfmStrategy(
            process=context.process,
            process_authority=context.process_authority,
            context_ref=context.context_ref,
            text=(
                f"No manufacturing rules shaped this strategy: {reason}. {component_name} is "
                "proposed as a sketch and a single extrusion so its sizes stay editable; "
                "choose a process to check wall, hole and size rules."
            ),
        )
    uses: dict[str, DfmRuleUse] = {}
    for feat in features:
        quantities = _KIND_QUANTITIES.get(feat.kind, ())
        if not quantities:
            continue
        for rule in profile.rules:
            if not _rule_applies(rule, quantities):
                continue
            if rule.check == "envelope" and context.build_volume_mm is None:
                continue
            feat.dfm_rules.append(rule.id)
            uses.setdefault(
                rule.id,
                DfmRuleUse(
                    rule_id=rule.id,
                    title=rule.title,
                    kind=rule.kind.value,
                    limit=rule.limit,
                    unit=rule.unit,
                    source=rule.source,
                    applied_as=_applied_as(rule, feat.kind),
                ),
            )
        feat.dfm_rules = list(dict.fromkeys(feat.dfm_rules))
    body = next((f for f in features if f.kind in BODY_KINDS), None)
    how = (
        "a sketch and a single extrusion"
        if body is not None and body.kind == "extrude"
        else "a turned (revolved) section"
        if body is not None
        else "editable features"
    )
    authority = context.process_authority.replace("_", " ")
    lines = [
        f"Strategy for {profile.process} ({profile.id} {profile.version}, process {authority}): "
        f"{component_name} is built from {how} so its sizes stay editable."
    ]
    if uses:
        lines.append(
            "Shaped by: "
            + "; ".join(
                f"{u.applied_as} ({u.rule_id}, {u.kind.replace('_', ' ')})" for u in uses.values()
            )
            + "."
        )
    else:
        lines.append("No rule of the profile bears on these features yet.")
    lines.append(
        "These rules shaped the strategy; they are not checks of this geometry, which "
        "come once exact geometry exists."
    )
    return DfmStrategy(
        profile_id=profile.id,
        profile_version=profile.version,
        process=profile.process,
        process_authority=context.process_authority,
        context_ref=context.context_ref,
        rules=list(uses.values()),
        text=" ".join(lines),
    )


# ---------------------------------------------------------------------------
# The deterministic pass
# ---------------------------------------------------------------------------


def _layout_param(value: float, unit: str, ref: str | None, what: str) -> ProposedParameter:
    return ProposedParameter(
        value=round(value, 4),
        unit=unit,
        status="estimated",
        source=Provenance(kind="l1_layout", ref=ref, detail=f"{what} of the concept layout block"),
    )


def _matching_facts(inputs: ReconstructionInputs) -> list[FactInput]:
    """Facts that name this component, or project facts when it is the only one."""
    key = _words(inputs.component_name)
    out: list[FactInput] = []
    for fact in inputs.facts:
        names_it = key.strip() and key in _words(fact.parameter)
        if names_it or fact.about_component or inputs.only_component:
            out.append(fact)
    return out


def _is_enclosure(inputs: ReconstructionInputs, profile: DfmProfile | None) -> str | None:
    ctx = inputs.context
    if _has_word(inputs.component_name, _ENCLOSURE_WORDS):
        return f"its name ({inputs.component_name}) says it is a housing"
    if ctx.intended_use and _has_word(ctx.intended_use, _ENCLOSURE_WORDS):
        return f"the intended use says it is an enclosure ({ctx.intended_use})"
    if (
        profile is not None
        and profile.id == "injection_moulding"
        and ctx.production_goal in ("pilot", "production")
    ):
        return "moulded production parts are made with even walls"
    return None


def _shape_word(shape: str, size: tuple[float, float, float], axis: str) -> str:
    _, ui, vi, along = _AXIS_PLANES.get(axis, _AXIS_PLANES["z"])
    if shape == "cylinder":
        d = min(size[ui], size[vi])
        return "a round disc" if size[along] < d / 2 else "a round rod"
    small = min(size)
    return "a flat plate" if small < max(size) / 4 else "a block"


def _psk_features(
    ref: str, sketch: ParametricSketch, n: int
) -> tuple[list[ProposedFeature], list[Assumption]]:
    """Features carried from an accepted parametric sketch (already editable)."""
    feats: list[ProposedFeature] = []
    notes: list[Assumption] = []
    prof_id = f"psk{n}-profile"
    feats.append(
        ProposedFeature(
            id=prof_id,
            kind="sketch_profile",
            name="Sketch profile",
            plain="your sketch",
            confidence=1.0,
            provenance=[Provenance(kind="parametric_sketch", ref=ref, detail="accepted sketch")],
            profile=list(sketch.elements),
            buildable=True,
            existing_ref=ref,
            note=f"From {ref}: its plane is {sketch.plane_id or 'not placed yet'}.",
        )
    )
    for item in sketch.feature_plan:
        params: dict[str, ProposedParameter] = {}
        for name, p in item.parameters.items():
            if p.status == "provided" and p.value not in (None, ""):
                params[name] = ProposedParameter(
                    value=p.value,
                    unit=p.unit,
                    status="confirmed",
                    source=Provenance(kind="parametric_sketch", ref=ref, detail="provided"),
                )
            else:
                params[name] = ProposedParameter(unit=p.unit, question=p.question)
        feats.append(
            ProposedFeature(
                id=f"psk{n}-{item.id}",
                kind=item.op,
                name="Extrude" if item.op == "extrude" else "Revolve",
                plain="pulled to its depth" if item.op == "extrude" else "turned about its axis",
                depends_on=[prof_id],
                parameters=params,
                confidence=1.0,
                provenance=[Provenance(kind="parametric_sketch", ref=ref, detail=item.note)],
                buildable=True,
                existing_ref=ref,
            )
        )
    if sketch.unit != "mm":
        notes.append(
            Assumption(
                id=f"psk{n}-unit",
                text=f"{ref} is in {sketch.unit} units: its sizes are proportions until it is "
                "placed on a plane in millimetres.",
                feature_id=prof_id,
            )
        )
    return feats, notes


def deterministic_proposal(inputs: ReconstructionInputs) -> FeatureHistoryProposal:
    """The deterministic pass: L1 blocks and parametric sketches to features.

    A box is a rectangle profile and an extrude; a cylinder a circle and an
    extrude (or, asked as a question, a revolve). A shell when the component
    or its intended use says it is an enclosure, openings from reference
    facts, bosses from fastened joints. Every number is the layout's estimate
    or a reference fact, labelled; anything else is unknown.
    """
    profile = profile_for_process(inputs.context.process)
    features: list[ProposedFeature] = []
    assumptions: list[Assumption] = []
    ambiguities: list[Ambiguity] = []
    facts = _matching_facts(inputs)
    part = inputs.part
    layout_ref = inputs.layout_ref
    body_id: str | None = None
    plain_parts: list[str] = []

    for n, (ref, sketch) in enumerate(inputs.sketches, start=1):
        feats, notes = _psk_features(ref, sketch, n)
        features += feats
        assumptions += notes
        body_id = body_id or next((f.id for f in feats if f.kind in BODY_KINDS), None)
        plain_parts.append("your editable sketch")

    if part is not None and not inputs.sketches:
        pos, size = part_box(part)
        shape = str(part.get("shape") or "box")
        axis = str(part.get("axis") or "z")
        if axis not in _AXIS_PLANES:
            axis = "z"
        plane, ui, vi, along = _AXIS_PLANES[axis]
        tag = str(part.get("provenance") or "assumption")
        conf = _LAYOUT_CONFIDENCE.get(tag, 0.5)
        layout_src = Provenance(
            kind="l1_layout", ref=layout_ref, detail=f"concept layout block ({tag})"
        )
        offset = _plane_offset(axis, pos, size)
        placement = SketchPlacement(
            plane=plane,
            offset=round(offset, 4),
            status="estimated",
            source=Provenance(
                kind="l1_layout", ref=layout_ref, detail="the block's position, estimated"
            ),
        )
        prof_params: dict[str, ProposedParameter] = {}
        if shape == "cylinder":
            d_u, d_v = size[ui], size[vi]
            diameter = min(d_u, d_v)
            prof_params["diameter"] = _layout_param(diameter, "mm", layout_ref, "diameter")
            if abs(d_u - d_v) > 1e-6:
                assumptions.append(
                    Assumption(
                        id="layout-not-round",
                        text=(
                            f"The layout's round block is {_fmt(d_u)} by {_fmt(d_v)} mm; the "
                            f"smaller, {_fmt(diameter)} mm, is used as its diameter."
                        ),
                        feature_id="profile",
                        source=layout_src,
                    )
                )
        else:
            prof_params["width"] = _layout_param(size[ui], "mm", layout_ref, "width")
            prof_params["length"] = _layout_param(size[vi], "mm", layout_ref, "length")
        depth = _layout_param(size[along], "mm", layout_ref, "extent along its axis")
        wall: ProposedParameter | None = None
        for fact in facts:
            if _has_word(fact.parameter, _OPENING_WORDS):
                continue  # an opening's size, not the body's
            which = _param_for_fact(fact)
            got = _fact_param(fact) if which else None
            if got is None or which is None:
                continue
            if which == "wall":
                wall = got
            elif which == "depth":
                depth = got
            elif which == "diameter" and shape == "cylinder":
                prof_params["diameter"] = got
            elif which == "width" and shape != "cylinder":
                prof_params["width"] = got
        firm = all(p.status == "confirmed" for p in prof_params.values())
        if shape == "cylinder":
            d = float(prof_params["diameter"].value or 0)
            cu = pos[ui] + size[ui] / 2
            cv = pos[vi] + size[vi] / 2
            elements: list[SketchElement] = [_circle_on_plane(cu, cv, d / 2, estimated=not firm)]
        else:
            w = float(prof_params["width"].value or 0)
            h = float(prof_params["length"].value or 0)
            elements = [_rect_on_plane(pos[ui], pos[vi], w, h, estimated=not firm)]
        word = _shape_word(shape, size, axis)
        features.append(
            ProposedFeature(
                id="profile",
                kind="sketch_profile",
                name="Main profile",
                plain=f"{'a circle' if shape == 'cylinder' else 'a rectangle'} on {plane}",
                parameters=prof_params,
                placement=placement,
                profile=elements,
                confidence=conf,
                provenance=[layout_src],
                buildable=True,
            )
        )
        material = _material_param(inputs.context)
        features.append(
            ProposedFeature(
                id="body",
                kind="extrude",
                name="Extrude",
                plain=word,
                depends_on=["profile"],
                parameters={"depth": depth, "material": material},
                confidence=conf,
                provenance=[layout_src],
                buildable=True,
            )
        )
        body_id = "body"
        plain_parts.append(word)
        assumptions.append(
            Assumption(
                id="layout-sizes",
                text=(
                    f"Sizes come from the concept layout ({_fmt(size[0])} x {_fmt(size[1])} x "
                    f"{_fmt(size[2])} mm) and are estimated; confirm them before exact CAD."
                ),
                feature_id="profile",
                source=layout_src,
            )
        )
        assumptions.append(
            Assumption(
                id="layout-plane",
                text=(
                    f"Sketched on the {plane} plane at {_fmt(offset)} mm along its normal, "
                    "where the layout places the block (estimated)."
                ),
                feature_id="profile",
                source=layout_src,
            )
        )
        if tag == "open":
            assumptions.append(
                Assumption(
                    id="layout-open",
                    text=f"The concept marks {inputs.component_name} as an open question.",
                    feature_id="body",
                    source=layout_src,
                )
            )
        if shape == "cylinder" and axis == "z":
            ambiguities.append(
                Ambiguity(
                    id="body-op",
                    question=(
                        f"Should {inputs.component_name} be made as a straight extrusion or as "
                        "a turned (revolved) shape?"
                    ),
                    options=[
                        ChoiceOption(id="extrude", label="Straight extrusion"),
                        ChoiceOption(id="revolve", label="Turned shape"),
                    ],
                    feature_id="body",
                    consequential=False,
                    default="extrude",
                )
            )

        enclosure = _is_enclosure(inputs, profile)
        if enclosure:
            min_wall = _min_rule(profile, "wall_thickness")
            if wall is None:
                wall = ProposedParameter(
                    unit="mm",
                    question=(
                        "How thick should the walls be?"
                        + (
                            f" At least {_fmt(min_wall.limit)} mm for {profile.process}."
                            if min_wall is not None and min_wall.limit is not None and profile
                            else ""
                        )
                    ),
                )
            if min_wall is not None and min_wall.limit is not None:
                wall.minimum = min_wall.limit
                wall.minimum_rule = min_wall.id
            features.append(
                ProposedFeature(
                    id="shell",
                    kind="shell",
                    name="Shell",
                    plain="hollowed out with even walls",
                    depends_on=["body"],
                    parameters={"thickness": wall},
                    confidence=0.6,
                    provenance=[Provenance(kind="shape", detail=f"proposed because {enclosure}")],
                )
            )
            plain_parts.append("hollowed out")
            ambiguities.append(
                Ambiguity(
                    id="shell-hollow",
                    question=f"Should {inputs.component_name} be hollow, or solid?",
                    options=[
                        ChoiceOption(id="hollow", label="Hollow"),
                        ChoiceOption(id="solid", label="Solid"),
                    ],
                    feature_id="shell",
                    consequential=True,
                    default="hollow",
                )
            )

        if (
            profile is not None
            and context_exceeds(inputs.context, size)
            and inputs.context.build_volume_mm is not None
        ):
            bv = inputs.context.build_volume_mm
            ambiguities.append(
                Ambiguity(
                    id="printable-split",
                    question=(
                        f"{inputs.component_name} is about {_fmt(max(size))} mm, larger than the "
                        f"machine's {_fmt(max(bv))} mm build volume. Split it into parts?"
                    ),
                    options=[
                        ChoiceOption(id="split", label="Split it"),
                        ChoiceOption(id="keep", label="Keep it whole"),
                    ],
                    feature_id="body",
                    consequential=True,
                )
            )

    # Openings named by reference facts.
    openings = [f for f in facts if _has_word(f.parameter, _OPENING_WORDS)]
    for i, fact in enumerate(openings[:8], start=1):
        fid = f"opening-{i}"
        opening_size = _fact_param(fact) if _fact_mm(fact) is not None else None
        label = re.sub(r"\s+", " ", fact.parameter).strip()[:80] or "Opening"
        features.append(
            ProposedFeature(
                id=fid,
                kind="opening",
                name=label[:1].upper() + label[1:],
                plain=f"an opening for the {label.lower()}",
                depends_on=[body_id] if body_id else [],
                parameters={
                    "size": opening_size
                    or ProposedParameter(unit="mm", question=f"How big is the {label.lower()}?")
                },
                confidence=0.6 if fact.basis == "stated" else 0.4,
                provenance=[
                    Provenance(
                        kind="reference_fact", ref=fact.cite, detail=fact.text or fact.parameter
                    )
                ],
            )
        )
        ambiguities.append(
            Ambiguity(
                id=f"{fid}-where",
                question=f"Where does the {label.lower()} go on {inputs.component_name}?",
                options=[
                    ChoiceOption(id=o, label=o.capitalize())
                    for o in ("front", "back", "top", "bottom", "side")
                ],
                feature_id=fid,
                consequential=True,
            )
        )
    if openings:
        plain_parts.append(f"{len(openings[:8])} opening{'s' if len(openings) > 1 else ''}")

    # Bosses for fastened joints.
    fastened = [c for c in inputs.connections if _has_word(c.get("joint"), _FASTENER_WORDS)]
    if (
        not fastened
        and inputs.context.fastening
        and _has_word(inputs.context.fastening, _FASTENER_WORDS)
    ):
        fastened = [{"other": "", "joint": str(inputs.context.fastening)}]
    if fastened and body_id:
        min_feat = _min_rule(profile, "feature_size")
        size_p = ProposedParameter(unit="mm", question="How big are the screw bosses?")
        if min_feat is not None and min_feat.limit is not None:
            size_p.minimum = min_feat.limit
            size_p.minimum_rule = min_feat.id
        joint = fastened[0]
        features.append(
            ProposedFeature(
                id="bosses",
                kind="boss",
                name="Mounting bosses",
                plain="mounting bosses",
                depends_on=[body_id],
                parameters={"diameter": size_p, "count": ProposedParameter(question="How many?")},
                confidence=0.5,
                provenance=[
                    Provenance(
                        kind="l1_layout",
                        ref=layout_ref,
                        detail=f"joint {joint.get('joint')}"
                        + (f" with {joint['other']}" if joint.get("other") else ""),
                    )
                ],
            )
        )
        plain_parts.append("mounting bosses")

    features.sort(key=lambda f: _KIND_ORDER.get(f.kind, 9))
    strategy = dfm_strategy(profile, inputs.context, features, component_name=inputs.component_name)
    if profile is None:
        ambiguities.append(
            Ambiguity(
                id="process",
                question=f"How will {inputs.component_name} be made?",
                options=[
                    ChoiceOption(id="FDM", label="3D printing (FDM)"),
                    ChoiceOption(id="SLA resin printing", label="Resin printing"),
                    ChoiceOption(id="CNC machining", label="CNC machining"),
                    ChoiceOption(id="injection moulding", label="Injection moulding"),
                    ChoiceOption(id="sheet metal", label="Sheet metal"),
                ],
                consequential=True,
            )
        )
    if not features:
        assumptions.append(
            Assumption(
                id="nothing-to-map",
                text=(
                    f"{inputs.component_name} has no block in the concept layout and no "
                    "parametric sketch, so no feature could be proposed. Sketch it or add it "
                    "to the layout."
                ),
            )
        )
    summary = (
        f"{_article(inputs.component_name)}: " + ", ".join(dict.fromkeys(plain_parts))
        if plain_parts
        else f"{_article(inputs.component_name)}: nothing to build from yet"
    )
    return FeatureHistoryProposal(
        component_id=inputs.component_id,
        component_name=inputs.component_name,
        features=features,
        assumptions=assumptions,
        ambiguities=ambiguities,
        dfm_strategy=strategy,
        summary=summary[:400],
    )


def _article(name: str) -> str:
    return f"the {name.strip().lower()}" if name.strip() else "the part"


def context_exceeds(context: ContextFacts, size: tuple[float, float, float]) -> bool:
    """The block does not fit the machine's build volume in any orientation."""
    if context.build_volume_mm is None:
        return False
    return any(
        a > b + 1e-9 for a, b in zip(sorted(size), sorted(context.build_volume_mm), strict=True)
    )


def _min_rule(profile: DfmProfile | None, quantity: str) -> DfmRule | None:
    if profile is None:
        return None
    for rule in profile.rules:
        if rule.check == "min" and (rule.quantity == quantity or quantity in rule.also_reads):
            return rule
    return None


def _material_param(context: ContextFacts) -> ProposedParameter:
    src = Provenance(
        kind="manufacturing_context", ref=context.context_ref, detail="selected material"
    )
    if context.material and context.material_authority in ("user_stated", "decision"):
        return ProposedParameter(value=context.material, status="confirmed", source=src)
    if context.material:
        return ProposedParameter(
            value=context.material,
            status="estimated",
            source=Provenance(
                kind="manufacturing_context",
                ref=context.context_ref,
                detail=f"material {context.material_authority.replace('_', ' ')}",
            ),
            question="What material is it made of?",
        )
    return ProposedParameter(question="What material is it made of?")


# ---------------------------------------------------------------------------
# The optional model pass: names, order and missing features, never numbers
# ---------------------------------------------------------------------------

_DIGIT = re.compile(r"\d")
_LLM_KINDS: frozenset[str] = frozenset({"shell", "opening", "boss", "rib"})


def llm_prompt(proposal: FeatureHistoryProposal) -> str:
    """The prompt for the model pass. It carries no numbers to echo back."""
    listing = [
        {"id": f.id, "kind": f.kind, "name": f.name, "depends_on": f.depends_on}
        for f in proposal.features
    ]
    return (
        "You review a proposed CAD feature history for one hardware component.\n"
        f"Component: {proposal.component_name}\n"
        f"Features: {json.dumps(listing)}\n"
        "Reply with JSON only, with these keys:\n"
        '  "names": {feature id: a short engineering name with no numbers},\n'
        '  "order": [every feature id, in build order, profiles before what uses them],\n'
        '  "suggestions": [{"kind": one of shell, opening, boss, rib, "name": short name, '
        '"reason": why}] for features that are clearly missing.\n'
        "Never give a dimension, a count or any other number."
    )


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _valid_order(order: list[str], features: list[ProposedFeature]) -> bool:
    ids = [f.id for f in features]
    if sorted(order) != sorted(ids):
        return False
    seen: set[str] = set()
    deps = {f.id: f.depends_on for f in features}
    for fid in order:
        if any(d not in seen for d in deps.get(fid, []) if d in ids):
            return False
        seen.add(fid)
    return True


def apply_llm_pass(
    proposal: FeatureHistoryProposal,
    reply_text: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> FeatureHistoryProposal:
    """Merge the model's names, order and suggestions. Every number it gives is dropped.

    Suggestions are new features marked ``suggested_by: llm`` with every
    parameter unknown, and an assumption saying a model proposed them.
    """
    out = proposal.model_copy(deep=True)
    out.llm = LlmPass(requested=True, provider=provider, model=model)
    data = _parse_json(reply_text or "")
    if data is None:
        out.llm.outcome = "invalid"
        out.llm.note = "the model's reply was not JSON; nothing was taken from it"
        return out
    ignored: list[str] = []
    names = data.get("names")
    if isinstance(names, dict):
        for fid, name in names.items():
            feat = out.feature(str(fid))
            if feat is None or not isinstance(name, str) or not name.strip():
                continue
            if _DIGIT.search(name):
                ignored.append(f"name for {fid} carried a number")
                continue
            if name.strip() != feat.name:
                feat.name = name.strip()[:120]
                out.llm.renamed += 1
    order = data.get("order")
    if isinstance(order, list) and all(isinstance(o, str) for o in order):
        if _valid_order(order, out.features):
            rank = {fid: i for i, fid in enumerate(order)}
            before = [f.id for f in out.features]
            out.features.sort(key=lambda f: rank[f.id])
            out.llm.reordered = before != [f.id for f in out.features]
        else:
            ignored.append("order did not keep every feature after what it depends on")
    suggestions = data.get("suggestions")
    body = next((f.id for f in out.features if f.kind in BODY_KINDS), None)
    have = {f.kind for f in out.features}
    if isinstance(suggestions, list):
        for i, s in enumerate(suggestions[:6], start=1):
            if not isinstance(s, dict):
                continue
            kind = str(s.get("kind") or "")
            name = str(s.get("name") or "").strip()
            reason = str(s.get("reason") or "").strip()
            if kind not in _LLM_KINDS or not name:
                ignored.append(f"suggestion {i} is not a shell, opening, boss or rib")
                continue
            if kind == "shell" and "shell" in have:
                continue
            if _DIGIT.search(name) or _DIGIT.search(reason):
                ignored.append(f"suggestion {i} carried a number")
                name = _DIGIT.sub("", name).strip() or kind
                reason = _DIGIT.sub("", reason).strip()
            fid = f"llm-{kind}-{i}"
            out.features.append(
                ProposedFeature(
                    id=fid,
                    kind=kind,  # type: ignore[arg-type]
                    name=name[:120],
                    plain=name.lower()[:200],
                    depends_on=[body] if body else [],
                    parameters={},
                    suggested_by="llm",
                    provenance=[
                        Provenance(
                            kind="llm",
                            ref=model,
                            detail=("suggested by a model: " + reason)[:400],
                        )
                    ],
                    note="Suggested by a model, not read from the concept. Every size is open.",
                )
            )
            out.assumptions.append(
                Assumption(
                    id=f"{fid}-suggested",
                    text=f"{name[:120]} was suggested by a model; keep it only if you want it.",
                    feature_id=fid,
                    source=Provenance(kind="llm", ref=model),
                )
            )
            out.llm.suggestions += 1
    out.llm.outcome = "used"
    out.llm.ignored = ignored[:20]
    return out


def mark_llm_unavailable(proposal: FeatureHistoryProposal, reason: str) -> FeatureHistoryProposal:
    out = proposal.model_copy(deep=True)
    out.llm = LlmPass(requested=True, outcome="unavailable", note=reason[:400])
    return out


# ---------------------------------------------------------------------------
# Acceptance: accepted features to parametric sketches
# ---------------------------------------------------------------------------


class AcceptanceError(ValueError):
    """An acceptance that cannot be applied (a 409 or 422 at the route)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class SketchToMake:
    """One parametric sketch to write for an accepted profile and body."""

    profile_id: str
    body_id: str
    sketch: ParametricSketch
    placement: dict[str, Any] | None
    estimated_parameters: list[str]
    pending: list[dict[str, Any]]


@dataclass
class AcceptancePlan:
    proposal: FeatureHistoryProposal
    accepted: list[str]
    rejected: list[str]
    added_dependencies: list[str]
    sketches: list[SketchToMake]
    #: Features carried from an existing parametric sketch (nothing new written).
    existing: dict[str, str]
    #: ``selected_process`` chosen by answering the process question.
    process_choice: str | None = None


_NUMERIC_PARAMS = frozenset({"depth", "angle", "width", "length", "diameter", "thickness", "size"})


def _edited(param: ProposedParameter, value: Any, name: str) -> ProposedParameter:
    if name in _NUMERIC_PARAMS or name == "count":
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise AcceptanceError(f"{name} must be a number, not {value!r}") from exc
        if num <= 0:
            raise AcceptanceError(f"{name} must be above zero")
        if param.minimum is not None and num < param.minimum:
            raise AcceptanceError(
                f"{name} {_fmt(num)} is below the {param.minimum_rule or 'rule'} minimum of "
                f"{_fmt(param.minimum)} {param.unit or ''}".strip()
            )
        value = num
    elif not isinstance(value, str) or not value.strip():
        raise AcceptanceError(f"{name} needs a value")
    return param.model_copy(
        update={
            "value": value,
            "status": "confirmed",
            "source": Provenance(kind="person", detail="given when accepting the proposal"),
        }
    )


def _plan_param(param: ProposedParameter, question: str) -> FeatureParameter:
    """A feature plan parameter: provided only when confirmed, else required input."""
    if param.status == "confirmed" and param.value not in (None, ""):
        return FeatureParameter(status="provided", value=param.value, unit=param.unit)
    hint = ""
    if param.status == "estimated" and param.value is not None:
        what = _fmt(float(param.value)) if isinstance(param.value, int | float) else param.value
        hint = f" The concept suggests about {what} {param.unit or ''}".rstrip() + " (estimated)."
    return FeatureParameter(
        status="required_input", unit=param.unit, question=(param.question or question) + hint
    )


def _profile_elements(
    profile: ProposedFeature, body: ProposedFeature
) -> tuple[list[SketchElement], list[str]]:
    """The profile's elements with any edited sizes applied."""
    params = profile.parameters
    estimated = [n for n, p in params.items() if p.status != "confirmed"]
    firm = not estimated
    out: list[SketchElement] = []
    if body.kind == "revolve":
        rect = next((e for e in profile.profile if isinstance(e, SketchRectangle)), None)
        circle = next((e for e in profile.profile if isinstance(e, SketchCircle)), None)
        d = float(params["diameter"].value or 0) if "diameter" in params else 0.0
        if circle is not None and d > 0 and profile.placement is not None:
            depth = body.parameters.get("depth") or body.parameters.get("height")
            h = float(depth.value) if depth is not None and depth.value is not None else 0.0
            if h <= 0:
                raise AcceptanceError(
                    "a turned shape needs its height: confirm the depth before accepting"
                )
            return _revolve_profile(
                d / 2, profile.placement.offset, h, estimated=not firm
            ), estimated
        if rect is not None:
            return list(profile.profile), estimated
    for el in profile.profile:
        if isinstance(el, SketchRectangle) and "width" in params and "length" in params:
            w = float(params["width"].value or el.width)
            h = float(params["length"].value or el.height)
            # Keep the plane-space corner: origin.y is -(v0 + height).
            v0 = -el.origin[1] - el.height
            out.append(
                el.model_copy(
                    update={
                        "width": w,
                        "height": h,
                        "origin": (el.origin[0], -(v0 + h)),
                        "estimated": not firm,
                    }
                )
            )
        elif isinstance(el, SketchCircle) and "diameter" in params:
            d = float(params["diameter"].value or el.radius * 2)
            out.append(el.model_copy(update={"radius": d / 2, "estimated": not firm}))
        else:
            out.append(el)
    return out, estimated


def accept_features(
    proposal: FeatureHistoryProposal,
    feature_ids: list[str],
    *,
    edits: dict[str, dict[str, Any]] | None = None,
    choices: dict[str, str] | None = None,
) -> AcceptancePlan:
    """Apply a person's acceptance: statuses, edits, choices, and the sketches to write.

    A feature's dependencies are accepted with it. Every edited parameter
    becomes ``confirmed`` (source: the person). A body whose profile and
    body are both accepted becomes one parametric sketch whose feature plan
    keeps every unconfirmed parameter ``required_input``.
    """
    out = proposal.model_copy(deep=True)
    edits = dict(edits or {})
    choices = dict(choices or {})
    known = {f.id for f in out.features}
    unknown = [fid for fid in [*feature_ids, *edits] if fid not in known]
    if unknown:
        raise AcceptanceError(f"no feature {unknown[0]!r} in this proposal")
    if not feature_ids:
        raise AcceptanceError("choose at least one feature to accept")
    for aid, choice in choices.items():
        amb = next((a for a in out.ambiguities if a.id == aid), None)
        if amb is None:
            raise AcceptanceError(f"no question {aid!r} in this proposal")
        if amb.options and choice not in {o.id for o in amb.options}:
            raise AcceptanceError(f"{choice!r} is not an answer to {aid!r}")
        amb.choice = choice

    wanted: list[str] = []
    added: list[str] = []

    def take(fid: str, *, implied: bool) -> None:
        if fid in wanted:
            return
        feat = out.feature(fid)
        if feat is None:
            return
        for dep in feat.depends_on:
            take(dep, implied=True)
        wanted.append(fid)
        if implied and fid not in feature_ids:
            added.append(fid)

    for fid in feature_ids:
        take(fid, implied=False)

    # Answers that change the strategy.
    shell_choice = next((a.choice for a in out.ambiguities if a.id == "shell-hollow"), None)
    op_choice = next((a.choice for a in out.ambiguities if a.id == "body-op"), None)
    process_choice = next((a.choice for a in out.ambiguities if a.id == "process"), None)
    if shell_choice == "solid":
        wanted = [w for w in wanted if out.feature(w) is None or out.feature(w).kind != "shell"]  # type: ignore[union-attr]
    for fid, changes in edits.items():
        feat = out.feature(fid)
        assert feat is not None
        for name, value in (changes or {}).items():
            if name not in feat.parameters:
                raise AcceptanceError(f"{fid} has no parameter {name!r}")
            feat.parameters[name] = _edited(feat.parameters[name], value, name)
    if op_choice == "revolve":
        body = out.feature("body")
        if body is not None and body.kind == "extrude":
            body.kind = "revolve"
            body.name = "Revolve"
            body.plain = "a turned shape"
            body.parameters["angle"] = ProposedParameter(
                value=360.0,
                unit="deg",
                status="confirmed",
                source=Provenance(kind="shape", detail="a round part is a full turn"),
            )
            out.assumptions.append(
                Assumption(
                    id="revolve-axis",
                    text="A turned shape is drawn about the model's vertical axis; move it into "
                    "place in the assembly.",
                    feature_id="body",
                )
            )

    for feat in out.features:
        feat.status = "accepted" if feat.id in wanted else "rejected"
    accepted = [f.id for f in out.features if f.status == "accepted"]
    rejected = [f.id for f in out.features if f.status == "rejected"]

    sketches: list[SketchToMake] = []
    existing: dict[str, str] = {}
    pending_all = [f for f in out.features if f.status == "accepted" and f.kind in PENDING_KINDS]
    for body in [f for f in out.features if f.status == "accepted" and f.kind in BODY_KINDS]:
        if body.existing_ref:
            existing[body.id] = body.existing_ref
            continue
        prof = next(
            (
                out.feature(d)
                for d in body.depends_on
                if (pf := out.feature(d)) and pf.kind == "sketch_profile"
            ),
            None,
        )
        if prof is None:
            raise AcceptanceError(f"{body.id} has no sketch profile to build from")
        elements, estimated = _profile_elements(prof, body)
        plan_params: dict[str, FeatureParameter] = {}
        amount = "angle" if body.kind == "revolve" else "depth"
        plan_params[amount] = _plan_param(
            body.parameters.get(amount)
            or ProposedParameter(unit="deg" if amount == "angle" else "mm"),
            "How far round should it turn?" if amount == "angle" else "How deep should it be?",
        )
        plan_params["material"] = _plan_param(
            body.parameters.get("material") or ProposedParameter(), "What material is it made of?"
        )
        estimated += [n for n, p in body.parameters.items() if p.status != "confirmed"]
        ids = [e.id for e in elements if e.id and not getattr(e, "construction", False)]
        axis_id = next(
            (e.id for e in elements if isinstance(e, SketchLine) and e.construction), None
        )
        item = FeaturePlanItem(
            id=body.id,
            op=body.kind,  # type: ignore[arg-type]
            profile_primitive_ids=ids,
            axis_id=axis_id if body.kind == "revolve" else None,
            parameters=plan_params,
            note="From a reconstruction proposal. Unconfirmed values are required input.",
        )
        sketch = ParametricSketch(
            interpretation_ref=None,
            sketch_ref=None,
            plane_id=None,
            unit="mm",
            elements=elements,
            constraints=[],
            profiles=[ClosedProfile(primitive_ids=ids)] if ids else [],
            feature_plan=[item],
        )
        placement = (
            {
                "plane": "XZ" if body.kind == "revolve" else prof.placement.plane,
                "offset": 0.0 if body.kind == "revolve" else float(prof.placement.offset),
                "status": prof.placement.status,
            }
            if prof.placement is not None
            else None
        )
        sketches.append(
            SketchToMake(
                profile_id=prof.id,
                body_id=body.id,
                sketch=sketch,
                placement=placement,
                estimated_parameters=list(dict.fromkeys(estimated)),
                pending=[
                    {
                        "feature_id": f.id,
                        "kind": f.kind,
                        "name": f.name,
                        "parameters": {
                            k: v.model_dump(mode="json") for k, v in f.parameters.items()
                        },
                        "placement": next(
                            (
                                a.choice
                                for a in out.ambiguities
                                if a.feature_id == f.id and a.choice
                            ),
                            None,
                        ),
                        "note": "Accepted intent; the exact path does not build this feature yet.",
                    }
                    for f in pending_all
                    if body.id in f.depends_on
                ],
            )
        )
    out.status = "accepted" if not rejected else "partially_accepted"
    return AcceptancePlan(
        proposal=out,
        accepted=accepted,
        rejected=rejected,
        added_dependencies=added,
        sketches=sketches,
        existing=existing,
        process_choice=process_choice,
    )


def principal_placement(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """``{"plane", "offset"}`` a reconstructed sketch was accepted on, or ``None``."""
    raw = (meta or {}).get(PRINCIPAL_PLACEMENT_KEY)
    if not isinstance(raw, dict) or raw.get("plane") not in ("XY", "YZ", "XZ"):
        return None
    try:
        return {"plane": str(raw["plane"]), "offset": float(raw.get("offset") or 0.0)}
    except (TypeError, ValueError):
        return None


__all__ = [
    "BODY_KINDS",
    "PENDING_KINDS",
    "PRINCIPAL_PLACEMENT_KEY",
    "SCHEMA",
    "AcceptanceError",
    "AcceptancePlan",
    "Ambiguity",
    "Assumption",
    "ChoiceOption",
    "ContextFacts",
    "DfmRuleUse",
    "DfmStrategy",
    "FactInput",
    "FeatureHistoryProposal",
    "LlmPass",
    "ProposalInputs",
    "ProposedFeature",
    "ProposedParameter",
    "Provenance",
    "ReconstructionInputs",
    "SketchPlacement",
    "SketchToMake",
    "accept_features",
    "apply_llm_pass",
    "deterministic_proposal",
    "dfm_strategy",
    "inputs_hash",
    "llm_prompt",
    "mark_llm_unavailable",
    "part_box",
    "principal_placement",
]
