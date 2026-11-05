"""One manufacturing context record per product or component (DFM-grounded generation).

Every concept, sketch, image and illustrative 3D operation is generated
against a manufacturing context, even an incomplete one, and never against
none in silence. Before this record the context lived in three places that
never met (the Create brief and route, a design flow's frozen proposal
context, and the latest route decision), and the direct generators saw none
of them. This module is the one record they all feed and all read.

**Fields.** Each field of :class:`ManufacturingContextRecord` is a
:class:`ContextField`: a value, its :class:`Authority` and its
:class:`Provenance` (the source, when, who, and a reference such as the
decision node it came from).

* ``user_stated``: a person said so (the Create brief or route, the
  manufacturing context API, a flow proposal's stated context).
* ``decision``: an approved design decision (a ``manufacturing_route`` with
  its ``process_ranking``).
* ``assumption``: proposed by MetaForge. Never a constraint, always shown as
  "may change".
* ``unknown``: not known. Listed in ``unknowns`` so it reads as a gap, never
  as an empty answer (DFM-010).

**The conflict rule** (:func:`apply_updates`). The most recent explicit human
statement or approved decision wins; an assumption never overrides a stated
value or a decision; an unknown never erases a value unless a person clears
it. A refused write is kept in ``conflicts`` so a reviewer can see what was
proposed and why it did not land.

**What is never assumed** (DFM-014). ``target_cost``, ``lead_time`` and the
``machine_or_supplier_profile`` may only be stated or decided; the model
refuses an assumed one. Nothing here estimates a price, a lead time or a
machine's capability.

**A process change** (3D-RC-013). When ``selected_process`` changes, every
assumption marked ``process_specific`` (material candidates proposed for the
old process, its rule profile) is marked stale, with the reason, but kept.
Stated values and decisions are never touched.

**DFM status and labels** (DFM-013, 3D-RC-012). :class:`DfmStatus` has the
spec's seven states. A generated concept is ``not_assessed`` unless a real
heuristic check ran (then ``preliminary_guidance``); it can never carry a
later state, and :func:`manufacturing_note` never says verified or
production-ready.

Pure data and pure functions: no I/O. The gateway stores the record as an
item (``api_gateway.twin.manufacturing_context``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, model_validator

logger = structlog.get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Authority(StrEnum):
    """How far a field can be trusted, and who it came from."""

    USER_STATED = "user_stated"
    DECISION = "decision"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


#: Authorities that are constraints rather than guidance (DFM-002).
CONFIRMED_AUTHORITIES: frozenset[Authority] = frozenset({Authority.USER_STATED, Authority.DECISION})


class DfmStatus(StrEnum):
    """The spec's DFM status semantics (DFM-013)."""

    #: No applicable DFM evaluation has run.
    NOT_ASSESSED = "not_assessed"
    #: Heuristic, process-candidate recommendations only.
    PRELIMINARY_GUIDANCE = "preliminary_guidance"
    #: Checks scheduled against the selected route and a geometry revision.
    ANALYSIS_PENDING = "analysis_pending"
    #: Actual checks found a blocking or advisory condition.
    ISSUES_FOUND = "issues_found"
    #: The specified checks completed for the process, geometry and rule profile.
    ANALYSIS_PASSED = "analysis_passed"
    #: Acceptance depends on tests, equipment trials or supplier confirmation.
    VERIFICATION_REQUIRED = "verification_required"
    #: The manufacturing package passed its gates and an authorised approval.
    RELEASED = "released"


#: What each status reads as. ``analysis_passed`` says what it is not.
DFM_STATUS_LABELS: dict[DfmStatus, str] = {
    DfmStatus.NOT_ASSESSED: "Manufacturing not assessed",
    DfmStatus.PRELIMINARY_GUIDANCE: "Preliminary manufacturing guidance",
    DfmStatus.ANALYSIS_PENDING: "Manufacturing analysis pending",
    DfmStatus.ISSUES_FOUND: "Manufacturing issues found",
    DfmStatus.ANALYSIS_PASSED: (
        "Manufacturing analysis passed for the selected process (not a guarantee of build success)"
    ),
    DfmStatus.VERIFICATION_REQUIRED: "Physical or supplier confirmation required",
    DfmStatus.RELEASED: "Released for manufacturing",
}

#: The only statuses an illustrative output (a concept image, sketch or
#: illustrative mesh) may carry: nothing exact has been checked.
ILLUSTRATIVE_DFM_STATUSES: frozenset[DfmStatus] = frozenset(
    {DfmStatus.NOT_ASSESSED, DfmStatus.PRELIMINARY_GUIDANCE}
)

NOT_ASSESSED_NOTE = DFM_STATUS_LABELS[DfmStatus.NOT_ASSESSED]
SPECULATIVE_LABEL = "Speculative concept"
#: The ``manufacturing_context_ref`` of an output generated with no record.
UNRESOLVED_REF = "unresolved"

PRODUCTION_GOALS: tuple[str, ...] = (
    "concept",
    "appearance_prototype",
    "functional_prototype",
    "pilot",
    "production",
)

#: Every field of the record, in the order it is shown.
CONTEXT_FIELDS: tuple[str, ...] = (
    "intended_use",
    "production_goal",
    "route",
    "process_candidates",
    "selected_process",
    "material_candidates",
    "selected_material",
    "quantity",
    "target_cost",
    "lead_time",
    "machine_or_supplier_profile",
    "dimensional_envelope",
    "tolerances",
    "finish",
    "assembly",
    "fastening",
    "applicable_rules",
    "evidence",
    "print_orientation",
)

#: Fields MetaForge never fills by assumption (DFM-014).
NEVER_ASSUMED: frozenset[str] = frozenset(
    {"target_cost", "lead_time", "machine_or_supplier_profile"}
)

#: How a field is named in a prompt or a note.
FIELD_WORDS: dict[str, str] = {
    "intended_use": "intended use",
    "production_goal": "production goal",
    "route": "route",
    "process_candidates": "process candidates",
    "selected_process": "process",
    "material_candidates": "material candidates",
    "selected_material": "material",
    "quantity": "quantity",
    "target_cost": "target cost",
    "lead_time": "lead time",
    "machine_or_supplier_profile": "machine or supplier",
    "dimensional_envelope": "dimensional envelope",
    "tolerances": "tolerances",
    "finish": "finish",
    "assembly": "assembly",
    "fastening": "fastening",
    "applicable_rules": "rule profile",
    "evidence": "evidence",
    "print_orientation": "print orientation",
}

#: Authorities ``print_orientation`` may carry: a person states it or MetaForge
#: assumes it. No design decision records an orientation.
ORIENTATION_AUTHORITIES: frozenset[Authority] = frozenset(
    {Authority.USER_STATED, Authority.ASSUMPTION, Authority.UNKNOWN}
)

#: What a gap is called in ``unknowns``.
GAP_WORDS: dict[str, str] = {
    "process": "process",
    "material": "material",
    "machine": "machine",
    "supplier": "supplier",
    "tolerance": "tolerance",
    "quantity": "quantity",
    "target_cost": "target cost",
    "lead_time": "lead time",
}

_IN_HOUSE_ROUTES = frozenset({"home", "workshop", "in_house"})
_VENDOR_ROUTES = frozenset({"external", "vendor"})
_MAX_VALUE_CHARS = 120
_MAX_CONFLICTS = 20
_CONTROL = re.compile(r"[\x00-\x1f\x7f`]+")


class Provenance(BaseModel):
    """Where a field's value came from."""

    #: ``create_brief``, ``create_route``, ``api``, ``decision``,
    #: ``flow_proposal``, ``proposal`` ...
    source: str = Field(..., min_length=1, max_length=200)
    at: str = Field(default_factory=_now)
    actor: str = Field("", max_length=200)
    #: What it points at: a decision node id, a run id, a library entry.
    ref: str | None = Field(None, max_length=300)


class ContextField(BaseModel):
    """One field: a value, how far it can be trusted, and where it came from."""

    value: Any = None
    authority: Authority = Authority.UNKNOWN
    provenance: Provenance | None = None
    note: str = Field("", max_length=500)
    #: An assumption that only holds for the selected process (3D-RC-013).
    process_specific: bool = False
    #: Set when the process it was assumed for changed. Kept, never deleted.
    stale: bool = False
    stale_reason: str = Field("", max_length=300)
    #: A design-flow run whose gate approval turns this into a decision.
    pending_gate: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _value_matches_authority(self) -> ContextField:
        if self.authority is Authority.UNKNOWN:
            self.value = None
        elif _empty(self.value):
            raise ValueError(
                f"a field with authority {self.authority.value} needs a value; "
                "use authority unknown to clear it"
            )
        return self

    @property
    def known(self) -> bool:
        return self.authority is not Authority.UNKNOWN

    @property
    def confirmed(self) -> bool:
        return self.authority in CONFIRMED_AUTHORITIES


class FieldConflict(BaseModel):
    """A write the conflict rule refused, kept so a reviewer can see it."""

    field: str
    kept_authority: Authority
    kept_value: Any = None
    refused_authority: Authority
    refused_value: Any = None
    source: str = ""
    reason: str
    at: str = Field(default_factory=_now)


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | dict):
        return not value
    return False


_UNIT: dict[str, tuple[float, float, float]] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}
#: Axis names a person may state an orientation as.
_AXES: dict[str, tuple[float, float, float]] = {
    **{f"+{k}": v for k, v in _UNIT.items()},
    **{k: v for k, v in _UNIT.items()},
    **{f"-{k}": (-v[0], -v[1], -v[2]) for k, v in _UNIT.items()},
}


def normalize_orientation(value: Any) -> dict[str, Any]:
    """A print orientation as the record keeps it, or ``ValueError``.

    ``build_direction`` is three finite numbers, not all zero, scaled to unit
    length; ``rotation_deg`` (optional) is a turn about it, in ``[0, 360)``.
    A bare ``[x, y, z]`` or an axis name (``+Z``, ``-Y``, ``x``) is read as
    the build direction.
    """
    if isinstance(value, str):
        axis = _AXES.get(value.strip().lower().replace(" ", ""))
        if axis is None:
            raise ValueError("print_orientation as text is an axis: +X, -X, +Y, -Y, +Z or -Z")
        value = {"build_direction": list(axis)}
    if isinstance(value, list | tuple):
        value = {"build_direction": list(value)}
    if not isinstance(value, dict):
        raise ValueError("print_orientation is {build_direction: [x, y, z], rotation_deg}")
    raw = value.get("build_direction")
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise ValueError("print_orientation.build_direction is three numbers")
    nums: list[float] = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise ValueError("print_orientation.build_direction is three finite numbers")
        nums.append(float(v))
    length = math.sqrt(sum(v * v for v in nums))
    if length < 1e-9:
        raise ValueError("print_orientation.build_direction cannot be zero")
    out: dict[str, Any] = {
        # ``+ 0.0`` turns a -0.0 into 0.0, so equal directions compare equal.
        "build_direction": [round(v / length, 6) + 0.0 for v in nums],
        "rotation_deg": None,
    }
    rotation = value.get("rotation_deg")
    if rotation is not None:
        if (
            isinstance(rotation, bool)
            or not isinstance(rotation, int | float)
            or not math.isfinite(rotation)
        ):
            raise ValueError("print_orientation.rotation_deg is a number of degrees")
        out["rotation_deg"] = round(float(rotation) % 360.0, 6)
    return out


def _field() -> ContextField:
    return ContextField()


class ManufacturingContextRecord(BaseModel):
    """The manufacturing context of one product, or one of its components."""

    project_id: str
    #: ``None`` for the product as a whole (``MFC-PROJECT``).
    component_key: str | None = Field(None, max_length=120)

    intended_use: ContextField = Field(default_factory=_field)
    production_goal: ContextField = Field(default_factory=_field)
    #: How it will be made: home, workshop or external (Create), in_house or vendor (flows).
    route: ContextField = Field(default_factory=_field)
    process_candidates: ContextField = Field(default_factory=_field)
    selected_process: ContextField = Field(default_factory=_field)
    material_candidates: ContextField = Field(default_factory=_field)
    selected_material: ContextField = Field(default_factory=_field)
    quantity: ContextField = Field(default_factory=_field)
    #: ``{"amount": float | None, "currency": str, "text": str}``.
    target_cost: ContextField = Field(default_factory=_field)
    #: ``{"days": float | None, "text": str}``.
    lead_time: ContextField = Field(default_factory=_field)
    #: ``{"machine": str, "machine_id": str, "supplier": str}``, stated only.
    machine_or_supplier_profile: ContextField = Field(default_factory=_field)
    dimensional_envelope: ContextField = Field(default_factory=_field)
    tolerances: ContextField = Field(default_factory=_field)
    finish: ContextField = Field(default_factory=_field)
    assembly: ContextField = Field(default_factory=_field)
    fastening: ContextField = Field(default_factory=_field)
    applicable_rules: ContextField = Field(default_factory=_field)
    evidence: ContextField = Field(default_factory=_field)
    #: How the part sits on the build plate (additive processes):
    #: ``{"build_direction": [x, y, z], "rotation_deg": float | None}``, the
    #: direction the layers stack along in the part's own frame and an optional
    #: turn about it. Stated by a person or assumed, never decided. Exact FDM
    #: DFM reads it when no request names a direction.
    print_orientation: ContextField = Field(default_factory=_field)

    #: Derived on every validation: the gaps (DFM-010) and the assumed fields.
    unknowns: list[str] = Field(default_factory=list)
    assumed: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    dfm_status: DfmStatus = DfmStatus.NOT_ASSESSED
    #: Writes the conflict rule refused, newest last.
    conflicts: list[FieldConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _checked_and_derived(self) -> ManufacturingContextRecord:
        for name in NEVER_ASSUMED:
            if self.get(name).authority is Authority.ASSUMPTION:
                raise ValueError(
                    f"{name} is never assumed: state it, decide it, or leave it unknown"
                )
        goal = self.production_goal
        if goal.known and str(goal.value) not in PRODUCTION_GOALS:
            raise ValueError(f"production_goal must be one of {', '.join(PRODUCTION_GOALS)}")
        cost = self.target_cost.value
        if self.target_cost.known:
            if not isinstance(cost, dict):
                raise ValueError("target_cost is {amount, currency, text}")
            if cost.get("amount") is not None and not str(cost.get("currency") or "").strip():
                raise ValueError("target_cost with an amount needs its currency")
        orientation = self.print_orientation
        if orientation.authority not in ORIENTATION_AUTHORITIES:
            raise ValueError("print_orientation is stated by a person or assumed, never decided")
        if orientation.known:
            self.print_orientation = orientation.model_copy(
                update={"value": normalize_orientation(orientation.value)}
            )
        for name in ("process_candidates", "material_candidates"):
            value = self.get(name).value
            if value is not None and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)
            ):
                raise ValueError(f"{name} is a list of names")
        self.unknowns = _gaps(self)
        self.assumed = [n for n in CONTEXT_FIELDS if self.get(n).authority is Authority.ASSUMPTION]
        self.confidence = _confidence(self)
        return self

    def get(self, name: str) -> ContextField:
        if name not in CONTEXT_FIELDS:
            raise KeyError(name)
        value: ContextField = getattr(self, name)
        return value

    def unresolved(self) -> list[str]:
        """The gaps and the assumed fields: what is not settled yet."""
        return [*self.unknowns, *(f"{n} (assumed)" for n in self.assumed)]


def _gaps(record: ManufacturingContextRecord) -> list[str]:
    gaps: list[str] = []
    if not record.selected_process.known:
        gaps.append("process")
    if not record.selected_material.known:
        gaps.append("material")
    profile = record.machine_or_supplier_profile.value
    profile = profile if isinstance(profile, dict) else {}
    route = str(record.route.value or "").strip().lower() if record.route.known else ""
    if route not in _VENDOR_ROUTES and not (profile.get("machine") or profile.get("machine_id")):
        gaps.append("machine")
    if route not in _IN_HOUSE_ROUTES and not profile.get("supplier"):
        gaps.append("supplier")
    if not record.tolerances.known:
        gaps.append("tolerance")
    if not record.quantity.known:
        gaps.append("quantity")
    if not record.target_cost.known:
        gaps.append("target_cost")
    if not record.lead_time.known:
        gaps.append("lead_time")
    return gaps


def _confidence(record: ManufacturingContextRecord) -> Literal["low", "medium", "high"]:
    process, material = record.selected_process.confirmed, record.selected_material.confirmed
    if process and material and record.quantity.known:
        return "high"
    if process or material:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# The conflict rule
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """What :func:`apply_updates` did."""

    record: ManufacturingContextRecord
    changed: list[str] = field(default_factory=list)
    refused: list[FieldConflict] = field(default_factory=list)
    #: The fields marked stale because the process changed.
    staled: list[str] = field(default_factory=list)
    #: ``(old, new)`` when ``selected_process`` changed value.
    process_change: tuple[Any, Any] | None = None


#: A write to one field: the field's new state.
ContextUpdate = dict[str, ContextField]


def may_override(
    current: ContextField, incoming: ContextField, *, allow_clear: bool
) -> tuple[bool, str]:
    """Whether ``incoming`` may replace ``current``, and why (the conflict rule)."""
    if incoming.authority is Authority.UNKNOWN:
        if allow_clear or not current.known:
            return True, "a person cleared it"
        return False, "an unknown never erases a value; a person must clear it"
    if incoming.authority in CONFIRMED_AUTHORITIES:
        return True, "the most recent explicit human statement or approved decision wins"
    if current.authority in CONFIRMED_AUTHORITIES:
        return (
            False,
            f"an assumption never overrides a {current.authority.value.replace('_', ' ')} value",
        )
    return True, "a newer assumption replaces an older assumption or a gap"


def _same(a: ContextField, b: ContextField) -> bool:
    return (
        a.authority is b.authority
        and a.value == b.value
        and a.pending_gate == b.pending_gate
        and a.process_specific == b.process_specific
    )


def apply_updates(
    record: ManufacturingContextRecord,
    updates: ContextUpdate,
    *,
    allow_clear: bool = False,
) -> MergeResult:
    """``record`` with ``updates`` applied under the conflict rule. Never mutates ``record``.

    A field whose value and authority are unchanged is left as it is (its
    provenance is not refreshed), so a resync of the same brief makes no
    revision. When the process changes, the other process-specific
    assumptions are marked stale.
    """
    data = record.model_dump(mode="python")
    changed: list[str] = []
    refused: list[FieldConflict] = []
    conflicts = list(record.conflicts)
    for name, incoming in updates.items():
        if name not in CONTEXT_FIELDS:
            raise ValueError(f"unknown manufacturing context field {name!r}")
        current = record.get(name)
        if name == "print_orientation" and incoming.known:
            incoming = incoming.model_copy(update={"value": normalize_orientation(incoming.value)})
        if _same(current, incoming):
            continue
        ok, why = may_override(current, incoming, allow_clear=allow_clear)
        if not ok:
            conflict = FieldConflict(
                field=name,
                kept_authority=current.authority,
                kept_value=current.value,
                refused_authority=incoming.authority,
                refused_value=incoming.value,
                source=incoming.provenance.source if incoming.provenance else "",
                reason=why,
            )
            refused.append(conflict)
            conflicts.append(conflict)
            continue
        data[name] = incoming.model_dump(mode="python")
        changed.append(name)

    process_change: tuple[Any, Any] | None = None
    staled: list[str] = []
    if "selected_process" in changed:
        old = record.selected_process.value
        new = data["selected_process"].get("value")
        if old is not None and _norm(old) != _norm(new):
            process_change = (old, new)
            for name in CONTEXT_FIELDS:
                entry = data[name]
                if (
                    name not in changed
                    and entry.get("authority") == Authority.ASSUMPTION
                    and entry.get("process_specific")
                    and not entry.get("stale")
                ):
                    entry["stale"] = True
                    entry["stale_reason"] = (
                        f"assumed for {_display(old)}; the process is now {_display(new)}"
                    )[:300]
                    staled.append(name)
    data["conflicts"] = [c.model_dump(mode="python") for c in conflicts[-_MAX_CONFLICTS:]]
    out = ManufacturingContextRecord.model_validate(data)
    if refused:
        logger.info(
            "manufacturing_context_writes_refused",
            project_id=record.project_id,
            fields=[c.field for c in refused],
        )
    return MergeResult(
        record=out,
        changed=changed,
        refused=refused,
        staled=staled,
        process_change=process_change,
    )


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def changed_fields(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[str]:
    """Fields whose value or authority differs between two stored records."""
    old = old or {}
    new = new or {}
    out: list[str] = []
    for name in CONTEXT_FIELDS:
        a = old.get(name) if isinstance(old.get(name), dict) else {}
        b = new.get(name) if isinstance(new.get(name), dict) else {}
        if (a or {}).get("value") != (b or {}).get("value") or (a or {}).get("authority") != (
            b or {}
        ).get("authority"):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Rendering: the prompt block and the note
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    """One prompt-safe line: no control characters, no backticks, bounded."""
    out = _CONTROL.sub(" ", text)
    out = re.sub(r"\s+", " ", out).strip()
    return out if len(out) <= _MAX_VALUE_CHARS else out[: _MAX_VALUE_CHARS - 1] + "…"


def _display(value: Any, name: str = "") -> str:
    """A field value in words."""
    if value is None:
        return "not stated"
    if name == "target_cost" and isinstance(value, dict):
        amount = value.get("amount")
        if isinstance(amount, int | float):
            text = f"{amount:g} {value.get('currency') or ''}".strip()
            return _clean(text)
        return _clean(str(value.get("text") or "stated"))
    if name == "print_orientation" and isinstance(value, dict):
        d = value.get("build_direction") or []
        text = "build direction " + ", ".join(f"{float(v):g}" for v in d)
        if value.get("rotation_deg") is not None:
            text += f", turned {float(value['rotation_deg']):g} deg about it"
        return _clean(text)
    if name == "lead_time" and isinstance(value, dict):
        days = value.get("days")
        if isinstance(days, int | float):
            return _clean(f"{days:g} days")
        return _clean(str(value.get("text") or "stated"))
    if isinstance(value, dict):
        parts = [f"{k} {v}" for k, v in value.items() if v not in (None, "", [], {})]
        return _clean(", ".join(parts))
    if isinstance(value, list | tuple):
        return _clean(", ".join(str(v) for v in value))
    if isinstance(value, float):
        return f"{value:g}"
    return _clean(str(value))


_AUTHORITY_WORDS = {
    Authority.USER_STATED: "stated",
    Authority.DECISION: "decided",
    Authority.ASSUMPTION: "assumed",
}


def _line(name: str, entry: ContextField) -> str:
    return f"{FIELD_WORDS[name]} {_display(entry.value, name)}"


def render_prompt_block(
    record: ManufacturingContextRecord | None,
    ref: str,
    *,
    speculative: bool = False,
) -> str:
    """The compact, prompt-safe block a generator is given (DFM-001, DFM-002).

    Confirmed constraints, assumptions and gaps are separate lines, so a model
    never reads an assumption as a requirement. Values are single lines with
    no control characters or backticks. In speculative mode the block says the
    context is recorded but not applied, and lists none of it.
    """
    head = f"Manufacturing context ({ref})"
    if speculative:
        return (
            f"{head}: {SPECULATIVE_LABEL.lower()}. Explore freely; the manufacturing "
            "context is recorded with this output but does not constrain it."
        )
    if record is None:
        return (
            f"{head}: none recorded yet. Process, material, machine, supplier, tolerance, "
            "quantity, target cost and lead time are all unknown. Keep the concept "
            "open to more than one way of making it."
        )
    confirmed = [
        f"{_line(n, record.get(n))} ({_AUTHORITY_WORDS[record.get(n).authority]})"
        for n in CONTEXT_FIELDS
        if record.get(n).confirmed and n != "evidence"
    ]
    assumed = [
        _line(n, record.get(n))
        for n in CONTEXT_FIELDS
        if record.get(n).authority is Authority.ASSUMPTION and not record.get(n).stale
    ]
    stale = [
        f"{FIELD_WORDS[n]} ({record.get(n).stale_reason})"
        for n in CONTEXT_FIELDS
        if record.get(n).authority is Authority.ASSUMPTION and record.get(n).stale
    ]
    lines = [f"{head}. Concept guidance, not a checked constraint set."]
    lines.append("Confirmed (stated by the person or decided): " + ("; ".join(confirmed) or "none"))
    lines.append("Assumed by MetaForge, may change: " + ("; ".join(assumed) or "none"))
    if stale:
        lines.append("Out of date, ignore: " + "; ".join(stale))
    gaps = [GAP_WORDS.get(g, g) for g in record.unknowns]
    lines.append("Not known yet: " + (", ".join(gaps) or "nothing"))
    lines.append(
        "Draw something that could plausibly be made this way and keep the person's "
        "distinctive features; never present it as manufacturable or proven."
    )
    return "\n".join(lines)


def illustrative_dfm_status(status: DfmStatus | str | None) -> DfmStatus:
    """The status an illustrative output may carry: ``not_assessed`` unless a heuristic ran."""
    try:
        value = DfmStatus(status) if status is not None else DfmStatus.NOT_ASSESSED
    except ValueError:
        return DfmStatus.NOT_ASSESSED
    if value not in ILLUSTRATIVE_DFM_STATUSES:
        logger.warning("illustrative_dfm_status_clamped", requested=value.value)
        return DfmStatus.NOT_ASSESSED
    return value


def _informing(record: ManufacturingContextRecord | None) -> list[str]:
    """``FDM (assumed)``-style phrases for the process and material that informed it."""
    if record is None:
        return []
    out: list[str] = []
    for chosen, candidates in (
        ("selected_process", "process_candidates"),
        ("selected_material", "material_candidates"),
    ):
        entry = record.get(chosen)
        if entry.known and not entry.stale:
            out.append(f"{_display(entry.value)} ({_AUTHORITY_WORDS[entry.authority]})")
            continue
        pool = record.get(candidates)
        if pool.known and not pool.stale and isinstance(pool.value, list) and pool.value:
            names = " or ".join(_clean(str(v)) for v in pool.value[:2])
            out.append(f"{names} ({_AUTHORITY_WORDS[pool.authority]})")
    return out


def manufacturing_note(
    record: ManufacturingContextRecord | None,
    *,
    speculative: bool = False,
    dfm_status: DfmStatus | str | None = None,
) -> str:
    """The short, truthful note a concept card shows (section 10, 3D-RC-012).

    ``Manufacturing not assessed`` when nothing informed it,
    ``Informed by: FDM (assumed), PLA (assumed) · Manufacturing not assessed``
    when a context did. Never says verified or production-ready.
    """
    status = illustrative_dfm_status(dfm_status)
    label = DFM_STATUS_LABELS[status]
    if speculative:
        return f"{SPECULATIVE_LABEL} · manufacturing context not applied"
    informed = _informing(record)
    if not informed:
        return label
    return f"Informed by: {', '.join(informed)} · {label}"
