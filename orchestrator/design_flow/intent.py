"""Compiling what someone asked for into a structured intent (FORGE-539).

A design flow used to start from the raw sentence plus a few context fields.
Everything downstream (which template, which phases, what "done" means) was
then inferred again by whoever read it, and inferred differently each time.

This module turns the sentence and the stated context into one explicit
model: the goal and what it is about, the immediate request versus the
underlying objective ("calculate what motor I need" is a calculation whose
point is a selection), the constraints sorted by kind, the quantities that
can become success criteria, the preferences, the assumptions, and the
unknowns, each marked blocking or not.

It is deterministic on purpose. It never calls a model and never invents a
value: a number appears in the model only if the person wrote it, and
anything it cannot establish lands in ``unknowns`` rather than being filled
with a plausible default. A model may refine it later; the floor it starts
from is what was actually said.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from orchestrator.design_flow.context import ClarifyingQuestion, FlowContext

__all__ = [
    "ConstraintCategory",
    "GoalType",
    "IntentConstraint",
    "IntentModel",
    "SuccessCriterion",
    "Unknown",
    "WorkflowScope",
    "compile_intent",
]


class GoalType(StrEnum):
    DESIGN = "design"
    MODIFY = "modify"
    ANALYSE = "analyse"
    SELECT = "select"
    COMPARE = "compare"
    VALIDATE = "validate"
    SIMULATE = "simulate"
    DIAGNOSE = "diagnose"
    OPTIMISE = "optimise"
    MANUFACTURE = "manufacture"
    PROCURE = "procure"
    DOCUMENT = "document"
    UNDERSTAND = "understand"


class ConstraintCategory(StrEnum):
    FUNCTIONAL = "functional"
    PERFORMANCE = "performance"
    GEOMETRIC = "geometric"
    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    THERMAL = "thermal"
    POWER = "power"
    COST = "cost"
    SCHEDULE = "schedule"
    MANUFACTURING = "manufacturing"
    REGULATORY = "regulatory"
    SAFETY = "safety"
    ENVIRONMENTAL = "environmental"
    INTEGRATION = "integration"


class WorkflowScope(StrEnum):
    ATOMIC = "atomic"
    SMALL = "small"
    PROJECT = "project"


#: Verbs, in the order they are tried. The first verb found in the sentence
#: sets the goal type; "design and validate a bracket" is a design.
_GOAL_VERBS: tuple[tuple[GoalType, tuple[str, ...]], ...] = (
    (GoalType.DESIGN, ("design", "build", "make", "create", "develop", "engineer")),
    (GoalType.MODIFY, ("modify", "change", "revise", "update", "redesign", "improve")),
    (GoalType.SELECT, ("select", "choose", "pick", "size", "specify")),
    (GoalType.COMPARE, ("compare", "trade off", "evaluate options")),
    (GoalType.VALIDATE, ("validate", "verify", "test", "check that", "prove")),
    (GoalType.SIMULATE, ("simulate", "model the")),
    (GoalType.ANALYSE, ("analyse", "analyze", "calculate", "compute", "estimate", "check")),
    (GoalType.DIAGNOSE, ("diagnose", "debug", "why does", "why is", "troubleshoot")),
    (GoalType.OPTIMISE, ("optimise", "optimize", "minimise", "minimize", "maximise", "maximize")),
    (GoalType.MANUFACTURE, ("manufacture", "fabricate", "produce", "machine", "print")),
    (GoalType.PROCURE, ("procure", "buy", "source", "order", "purchase")),
    (GoalType.DOCUMENT, ("document", "write up", "write a spec")),
    (GoalType.UNDERSTAND, ("explain", "understand", "what is", "how does")),
)

#: What a goal of each type is normally finished with. A starting point the
#: flow template overrides, not a promise.
_DEFAULT_DELIVERABLES: dict[GoalType, tuple[str, ...]] = {
    GoalType.DESIGN: ("constraint_set", "design_decision", "cad_model", "simulation_result"),
    GoalType.MODIFY: ("design_decision", "cad_model", "simulation_result"),
    GoalType.SELECT: ("design_decision",),
    GoalType.COMPARE: ("design_decision",),
    GoalType.VALIDATE: ("simulation_result", "verification_report"),
    GoalType.SIMULATE: ("simulation_result",),
    GoalType.ANALYSE: ("simulation_result",),
    GoalType.DIAGNOSE: ("design_decision",),
    GoalType.OPTIMISE: ("design_decision", "simulation_result"),
    GoalType.MANUFACTURE: ("manufacturing_file", "bom"),
    GoalType.PROCURE: ("bom",),
    GoalType.DOCUMENT: ("documentation",),
    GoalType.UNDERSTAND: (),
}

#: Units, mapped to the kind of constraint a quantity in that unit states.
_UNIT_CATEGORY: dict[str, ConstraintCategory] = {
    "kg": ConstraintCategory.MECHANICAL,
    "g": ConstraintCategory.MECHANICAL,
    "n": ConstraintCategory.MECHANICAL,
    "kn": ConstraintCategory.MECHANICAL,
    "nm": ConstraintCategory.MECHANICAL,
    "n.m": ConstraintCategory.MECHANICAL,
    "mpa": ConstraintCategory.MECHANICAL,
    "mm": ConstraintCategory.GEOMETRIC,
    "cm": ConstraintCategory.GEOMETRIC,
    "m": ConstraintCategory.GEOMETRIC,
    "in": ConstraintCategory.GEOMETRIC,
    "v": ConstraintCategory.ELECTRICAL,
    "a": ConstraintCategory.ELECTRICAL,
    "ma": ConstraintCategory.ELECTRICAL,
    "mah": ConstraintCategory.POWER,
    "wh": ConstraintCategory.POWER,
    "w": ConstraintCategory.POWER,
    "kw": ConstraintCategory.POWER,
    "°c": ConstraintCategory.THERMAL,
    "c": ConstraintCategory.THERMAL,
    "k": ConstraintCategory.THERMAL,
    "rpm": ConstraintCategory.PERFORMANCE,
    "m/s": ConstraintCategory.PERFORMANCE,
    "km/h": ConstraintCategory.PERFORMANCE,
    "hz": ConstraintCategory.PERFORMANCE,
    "s": ConstraintCategory.PERFORMANCE,
    "min": ConstraintCategory.PERFORMANCE,
    "h": ConstraintCategory.PERFORMANCE,
    "gbp": ConstraintCategory.COST,
    "usd": ConstraintCategory.COST,
    "eur": ConstraintCategory.COST,
    "£": ConstraintCategory.COST,
    "$": ConstraintCategory.COST,
    "€": ConstraintCategory.COST,
    "days": ConstraintCategory.SCHEDULE,
    "weeks": ConstraintCategory.SCHEDULE,
    "months": ConstraintCategory.SCHEDULE,
}

#: Words that put a clause in a category when it has no quantity.
_KEYWORD_CATEGORY: tuple[tuple[ConstraintCategory, tuple[str, ...]], ...] = (
    (ConstraintCategory.REGULATORY, ("ce ", "ukca", "fcc", "rohs", "reach", "certif", "complian")),
    (ConstraintCategory.SAFETY, ("safety", "safe ", "hazard", "fail-safe", "guard")),
    (ConstraintCategory.ENVIRONMENTAL, ("waterproof", "ip6", "ip5", "outdoor", "indoor", "dust")),
    (ConstraintCategory.MANUFACTURING, ("3d print", "cnc", "laser", "injection", "machin")),
    (ConstraintCategory.INTEGRATION, ("mount", "interface", "connector", "compatible", "fit")),
    (ConstraintCategory.THERMAL, ("temperature", "heat", "thermal", "cooling")),
    (ConstraintCategory.POWER, ("battery", "runtime", "power")),
    (ConstraintCategory.COST, ("budget", "cost", "cheap", "price")),
)

_PREFERENCE_MARKERS = ("prefer", "ideally", "nice to have", "if possible", "would like")

_QUANTITY = re.compile(
    r"(?P<prefix>[£$€])?\s*(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>°c|n\.m|nm|mpa|kn|km/h|m/s|mah|kwh|wh|kw|rpm|hz|kg|mm|cm|ma|gbp|usd|eur|"
    r"days|weeks|months|min|g|n|m|v|a|w|c|k|s|h|in)?\b",
    re.IGNORECASE,
)

#: Comparison words before a quantity, read as the criterion's operator.
_OPERATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "<=",
        (
            "under",
            "below",
            "at most",
            "less than",
            "no more than",
            "max",
            "maximum",
            "within",
            "up to",
            "<=",
            "≤",
        ),
    ),
    (
        ">=",
        (
            "over",
            "above",
            "at least",
            "more than",
            "minimum",
            "min ",
            "support",
            "hold",
            "carry",
            ">=",
            "≥",
        ),
    ),
)

#: FORGE-569: named dimensionless quantities. "A factor of safety of at least
#: 2" has no unit, so the unit scan dropped it and the one requirement that
#: decides the bracket was not measurable. The name is the unit.
_RATIO = re.compile(
    r"\b(?P<name>factor of safety|safety factor|margin of safety|fos)\b"
    r"(?P<between>[^0-9.;]{0,48}?)(?P<value>\d+(?:\.\d+)?)(?!\s*(?:%|[a-z]))",
    re.IGNORECASE,
)
#: The unit a ratio is recorded in, and the operator it takes when none is
#: said: a factor of safety is a minimum by definition, never a ceiling.
_RATIO_UNIT = {
    "factor of safety": "FoS",
    "safety factor": "FoS",
    "fos": "FoS",
    "margin of safety": "MoS",
}

#: FORGE-569: "80 x 60 x 40 mm" is three limits, one per axis. Scanning it as
#: quantities kept only the last number, the one next to the unit.
_ENVELOPE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?:mm|cm|m|in)?\s*[x×*]\s*"
    r"(?P<b>\d+(?:\.\d+)?)"
    r"(?:\s*(?:mm|cm|m|in)?\s*[x×*]\s*(?P<c>\d+(?:\.\d+)?))?"
    r"\s*(?P<unit>mm|cm|m|in)\b",
    re.IGNORECASE,
)
_AXES = ("length", "width", "height")

#: FORGE-569: a clause asking for an output ("Deliver CAD and validation
#: evidence") names deliverables; it does not constrain the design.
_REQUEST_VERBS = ("deliver", "provide", "produce", "supply", "hand over", "include", "output")
_REQUESTED: tuple[tuple[str, str], ...] = (
    ("cad", "cad_model"),
    ("step file", "cad_model"),
    ("3d model", "cad_model"),
    ("drawing", "technical_drawing"),
    ("validation evidence", "simulation_result"),
    ("verification evidence", "simulation_result"),
    ("simulation", "simulation_result"),
    ("analysis", "simulation_result"),
    ("fea", "simulation_result"),
    ("test report", "verification_report"),
    ("bill of materials", "bom"),
    ("bom", "bom"),
    ("schematic", "schematic"),
    ("firmware", "firmware_source"),
)

_DISCIPLINE_WORDS: dict[str, tuple[str, ...]] = {
    "mechanical": ("frame", "bracket", "structure", "enclosure", "shelf", "chassis", "link", "arm"),
    "electronics": ("pcb", "circuit", "schematic", "sensor", "board", "electronics"),
    "firmware": ("firmware", "software", "controller code", "rtos"),
    "power": ("battery", "power supply", "charger"),
    "actuation": ("motor", "actuator", "servo", "gearbox", "drive"),
}


@dataclass(frozen=True)
class IntentConstraint:
    category: ConstraintCategory
    text: str
    #: ``stated`` when the person said it; ``context`` when it came from the
    #: structured project context. Never ``inferred``: this compiler does not
    #: invent constraints.
    source: str = "stated"
    value: float | None = None
    unit: str = ""
    #: The axis a geometric limit applies to ("length", "width", "height").
    dimension: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "category": self.category.value,
            "text": self.text,
            "source": self.source,
        }
        if self.value is not None:
            out["value"] = self.value
            out["unit"] = self.unit
        if self.dimension:
            out["dimension"] = self.dimension
        return out


@dataclass(frozen=True)
class SuccessCriterion:
    text: str
    operator: str = ""
    limit: float | None = None
    unit: str = ""
    dimension: str = ""

    @property
    def measurable(self) -> bool:
        return self.limit is not None and bool(self.unit)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "operator": self.operator,
            "limit": self.limit,
            "unit": self.unit,
            "measurable": self.measurable,
        }
        if self.dimension:
            out["dimension"] = self.dimension
        return out


@dataclass(frozen=True)
class Unknown:
    id: str
    question: str
    #: Whether a proposal can go ahead without the answer.
    blocking: bool
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "blocking": self.blocking,
            "why": self.why,
        }


@dataclass(frozen=True)
class IntentModel:
    raw: str
    immediate_request: str
    goal_type: GoalType
    goal_object: str
    underlying_objective: str
    desired_outcomes: tuple[str, ...] = ()
    deliverables: tuple[str, ...] = ()
    #: Deliverables the person asked for by name ("deliver CAD and ...").
    requested_deliverables: tuple[str, ...] = ()
    constraints: tuple[IntentConstraint, ...] = ()
    preferences: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    success_criteria: tuple[SuccessCriterion, ...] = ()
    disciplines: tuple[str, ...] = ()
    scope: WorkflowScope = WorkflowScope.SMALL
    #: How each part was established: ``stated`` (said outright),
    #: ``matched`` (a known verb/unit/word was found) or ``unknown``.
    confidence: dict[str, str] = field(default_factory=dict)

    @property
    def blocking_unknowns(self) -> tuple[Unknown, ...]:
        return tuple(u for u in self.unknowns if u.blocking)

    @property
    def ready_to_plan(self) -> bool:
        """No unknown stops a proposal. Not the same as everything being known."""
        return not self.blocking_unknowns

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "immediate_request": self.immediate_request,
            "primary_goal": {"type": self.goal_type.value, "object": self.goal_object},
            "underlying_objective": self.underlying_objective,
            "desired_outcomes": list(self.desired_outcomes),
            "deliverables": list(self.deliverables),
            "requested_deliverables": list(self.requested_deliverables),
            "constraints": [c.as_dict() for c in self.constraints],
            "preferences": list(self.preferences),
            "assumptions": list(self.assumptions),
            "unknowns": [u.as_dict() for u in self.unknowns],
            "success_criteria": [s.as_dict() for s in self.success_criteria],
            "disciplines": list(self.disciplines),
            "scope": self.scope.value,
            "ready_to_plan": self.ready_to_plan,
            "confidence": dict(self.confidence),
        }


def _goal(text: str) -> tuple[GoalType, str, bool]:
    """The goal type, its object, and whether a verb actually matched."""
    lowered = f" {text.lower()} "
    best: tuple[int, GoalType, str] | None = None
    for goal_type, verbs in _GOAL_VERBS:
        for verb in verbs:
            match = re.search(rf"\b{re.escape(verb)}\b", lowered)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), goal_type, verb)
    if best is None:
        return GoalType.DESIGN, _object_phrase(text), False
    start, goal_type, verb = best
    after = text[max(start - 1, 0) + len(verb) :]
    return goal_type, _object_phrase(after), True


def _object_phrase(text: str) -> str:
    phrase = re.split(r"[,.;:]|\bfor\b|\bwith\b|\bthat\b|\bwhich\b|\bunder\b", text, maxsplit=1)[0]
    filler = {
        "a",
        "an",
        "the",
        "me",
        "us",
        "i",
        "we",
        "what",
        "which",
        "need",
        "needs",
        "my",
        "our",
    }
    words = [w for w in phrase.split() if w.lower() not in filler]
    return " ".join(words[:8]).strip() or "unspecified"


def _clauses(texts: Iterable[str]) -> list[str]:
    out: list[str] = []
    for text in texts:
        for sentence in re.split(r"[;\n]|(?<=[.!?])\s+", text):
            # A request lists its deliverables with commas ("deliver CAD, a
            # drawing and ..."); splitting it would orphan all but the first.
            if sentence.strip().lower().startswith(_REQUEST_VERBS):
                parts = [sentence]
            else:
                parts = re.split(r",\s+(?=[a-z])", sentence)
            for part in parts:
                part = part.strip(" .")
                if part:
                    out.append(part)
    return out


def _unit(raw: str | None, prefix: str | None) -> str:
    if prefix:
        return {"£": "GBP", "$": "USD", "€": "EUR"}[prefix]
    return (raw or "").strip()


def _operator(clause: str, index: int) -> str:
    before = clause[:index].lower()
    window = before[-24:]
    for op, words in _OPERATORS:
        if any(w in window for w in words):
            return op
    return ""


def _scan(clause: str) -> list[tuple[float, str, str]]:
    """(value, unit, operator) for every quantity with a unit in ``clause``."""
    return [(v, u, op) for v, u, op, _pos in _scan_spans(clause)]


def _scan_spans(clause: str) -> list[tuple[float, str, str, int]]:
    """As :func:`_scan`, with where each quantity starts."""
    found: list[tuple[float, str, str, int]] = []
    for match in _QUANTITY.finditer(clause):
        unit = _unit(match.group("unit"), match.group("prefix"))
        if not unit:
            continue
        value = float(match.group("value").replace(",", ""))
        found.append((value, unit, _operator(clause, match.start()), match.start("value")))
    return found


def _keyword_hit(lowered: str, word: str) -> bool:
    """``word`` as a word, not a substring (FORGE-569).

    ``"ce "`` matched inside "eviden*ce* ", which filed "Deliver CAD and
    validation evidence" as a regulatory constraint. A keyword ending in a
    space is a whole word; one without is a stem ("certif", "machin") and
    only has to start a word.
    """
    tail = r"\b" if word.endswith(" ") else ""
    return re.search(r"\b" + re.escape(word.strip()) + tail, lowered) is not None


def _category_for(clause: str, units: Sequence[str]) -> ConstraintCategory | None:
    for unit in units:
        category = _UNIT_CATEGORY.get(unit.lower())
        if category is not None:
            return category
    lowered = f" {clause.lower()} "
    for category, words in _KEYWORD_CATEGORY:
        if any(_keyword_hit(lowered, w) for w in words):
            return category
    return None


def _requested(clause: str) -> tuple[str, ...] | None:
    """The deliverables a request clause names, or ``None`` if it is not one."""
    lowered = clause.lower().strip()
    if not lowered.startswith(_REQUEST_VERBS):
        return None
    found = [kind for words, kind in _REQUESTED if _keyword_hit(f" {lowered} ", words)]
    return tuple(dict.fromkeys(found))


def _ratios(clause: str) -> list[tuple[float, str, str, int]]:
    """(value, unit, operator, start) for every named dimensionless quantity."""
    out: list[tuple[float, str, str, int]] = []
    for m in _RATIO.finditer(clause):
        unit = _RATIO_UNIT[m.group("name").lower()]
        between = m.group("between").lower()
        op = next((o for o, words in _OPERATORS if any(w in between for w in words)), "")
        out.append((float(m.group("value")), unit, op or ">=", m.start("value")))
    return out


def _envelopes(clause: str) -> list[tuple[list[tuple[str, float]], str, str, tuple[int, int]]]:
    """([(axis, value)], unit, operator, span) for every "a x b [x c] unit"."""
    out = []
    for m in _ENVELOPE.finditer(clause):
        values = [float(m.group(k)) for k in ("a", "b", "c") if m.group(k)]
        out.append(
            (
                list(zip(_AXES, values, strict=False)),
                m.group("unit").lower(),
                _operator(clause, m.start()),
                m.span(),
            )
        )
    return out


def compile_intent(
    intent: str,
    *,
    context: FlowContext | None = None,
    requirements: Sequence[str] = (),
    questions: Sequence[ClarifyingQuestion] = (),
    assumptions: Sequence[str] = (),
    template_deliverables: Sequence[str] = (),
) -> IntentModel:
    """Compile ``intent`` (plus what was stated around it) into an :class:`IntentModel`."""
    text = " ".join(intent.split())
    goal_type, goal_object, verb_matched = _goal(text)

    # Immediate request vs the objective behind it: a calculation whose point
    # is a choice ("calculate what motor I need") serves a selection.
    lowered = text.lower()
    underlying_type = goal_type
    if goal_type is GoalType.ANALYSE and re.search(r"\b(need|which|what .* to use)\b", lowered):
        underlying_type = GoalType.SELECT
    underlying = f"{underlying_type.value} {goal_object}".strip()

    constraints: list[IntentConstraint] = []
    criteria: list[SuccessCriterion] = []
    preferences: list[str] = []
    requested: list[str] = []
    for clause in _clauses([text, *requirements]):
        if any(m in clause.lower() for m in _PREFERENCE_MARKERS):
            preferences.append(clause)
            continue
        asked = _requested(clause)
        if asked is not None:
            requested.extend(asked)
            continue
        envelopes = _envelopes(clause)
        for axes, unit, op, _span in envelopes:
            for axis, value in axes:
                constraints.append(
                    IntentConstraint(
                        ConstraintCategory.GEOMETRIC, clause, value=value, unit=unit, dimension=axis
                    )
                )
                if op:
                    criteria.append(
                        SuccessCriterion(clause, op, limit=value, unit=unit, dimension=axis)
                    )
        for value, unit, op, _start in _ratios(clause):
            constraints.append(
                IntentConstraint(ConstraintCategory.SAFETY, clause, value=value, unit=unit)
            )
            criteria.append(SuccessCriterion(clause, op, limit=value, unit=unit))
        spans = [span for *_rest, span in envelopes]
        quantities = [q for q in _scan_spans(clause) if not any(a <= q[3] < b for a, b in spans)]
        if quantities or envelopes or _ratios(clause):
            for value, unit, op, _pos in quantities:
                # Each quantity is categorised by its own unit: a budget and a
                # mass in one sentence are a cost and a mass.
                category = _category_for(clause, [unit]) or ConstraintCategory.PERFORMANCE
                constraints.append(
                    IntentConstraint(category=category, text=clause, value=value, unit=unit)
                )
                if op:
                    # Only a quantity with a direction ("at least", "under")
                    # is a criterion; "a 12 kg robot" states a fact.
                    criteria.append(
                        SuccessCriterion(text=clause, operator=op, limit=value, unit=unit)
                    )
        else:
            # A clause can state several kinds of constraint at once
            # ("outdoor, waterproof and UKCA compliant"); record each.
            lowered_clause = f" {clause.lower()} "
            for keyword, words in _KEYWORD_CATEGORY:
                if any(_keyword_hit(lowered_clause, w) for w in words):
                    constraints.append(IntentConstraint(category=keyword, text=clause))

    unknowns: list[Unknown] = []
    context_assumptions = list(assumptions)
    if context is not None:
        m = context.manufacturing
        if m is not None and m.route is not None:
            constraints.append(
                IntentConstraint(
                    ConstraintCategory.MANUFACTURING, context.capability_basis(), source="context"
                )
            )
        if (context.budget or "").strip():
            constraints.append(
                IntentConstraint(ConstraintCategory.COST, f"budget: {context.budget}", "context")
            )
        if (context.loads_and_use or "").strip() and context.loads_known:
            constraints.append(
                IntentConstraint(
                    ConstraintCategory.MECHANICAL,
                    f"loads and use: {context.loads_and_use}",
                    source="context",
                )
            )
        elif (context.loads_and_use or "").strip():
            unknowns.append(
                Unknown(
                    "loads",
                    "What loads will this carry, with numbers?",
                    blocking=False,
                    why="loads stated as unknown: verification stays in the flow, but no "
                    "load case can be set until they are known",
                )
            )
        if context.requirements_pending and not requirements:
            context_assumptions.append(
                "no requirements recorded yet; the requirements phase records them"
            )
    for q in questions:
        unknowns.append(
            Unknown(q.id, q.question, blocking=bool(getattr(q, "required", False)), why=q.why)
        )
    if not criteria:
        unknowns.append(
            Unknown(
                "success_criteria",
                "What measurable result would count as success (a number with a unit)?",
                blocking=False,
                why="nothing stated is measurable, so completion cannot be verified yet",
            )
        )

    disciplines = tuple(
        name for name, words in _DISCIPLINE_WORDS.items() if any(w in lowered for w in words)
    )
    if goal_type in (GoalType.UNDERSTAND,) or (
        goal_type in (GoalType.ANALYSE, GoalType.SELECT) and len(disciplines) <= 1
    ):
        scope = WorkflowScope.ATOMIC
    elif len(disciplines) >= 2 or goal_type is GoalType.DESIGN and len(constraints) >= 3:
        scope = WorkflowScope.PROJECT
    else:
        scope = WorkflowScope.SMALL

    deliverables = tuple(
        dict.fromkeys([*template_deliverables, *_DEFAULT_DELIVERABLES[underlying_type], *requested])
    )
    outcomes = _outcomes(underlying_type, goal_object, bool(criteria))

    return IntentModel(
        raw=intent,
        immediate_request=f"{goal_type.value} {goal_object}".strip(),
        goal_type=goal_type,
        goal_object=goal_object,
        underlying_objective=underlying,
        desired_outcomes=outcomes,
        deliverables=deliverables,
        requested_deliverables=tuple(dict.fromkeys(requested)),
        constraints=tuple(constraints),
        preferences=tuple(preferences),
        assumptions=tuple(context_assumptions),
        unknowns=tuple(unknowns),
        success_criteria=tuple(criteria),
        disciplines=disciplines,
        scope=scope,
        confidence={
            "goal_type": "matched" if verb_matched else "unknown",
            "goal_object": "matched" if goal_object != "unspecified" else "unknown",
            "constraints": "stated" if constraints else "unknown",
            "success_criteria": "stated" if criteria else "unknown",
        },
    )


def _outcomes(goal: GoalType, obj: str, measurable: bool) -> tuple[str, ...]:
    """The intermediate outcomes a goal of this type passes through."""
    base = {
        GoalType.DESIGN: (
            "requirements established",
            "architecture selected",
            f"{obj} designed",
            "design verified against requirements",
        ),
        GoalType.MODIFY: (
            "affected requirements identified",
            f"{obj} revised",
            "revision verified against requirements",
        ),
        GoalType.SELECT: (
            "selection criteria established",
            "candidates identified",
            "candidates evaluated",
            f"{obj} selected and recorded",
        ),
        GoalType.COMPARE: ("criteria established", "alternatives evaluated", "decision recorded"),
        GoalType.VALIDATE: ("acceptance criteria established", "evidence produced", "verdict"),
        GoalType.SIMULATE: ("model set up", "simulation run", "results recorded"),
        GoalType.ANALYSE: ("inputs established", "analysis run", "result recorded"),
        GoalType.DIAGNOSE: ("symptoms recorded", "cause identified", "fix decided"),
        GoalType.OPTIMISE: ("objective and limits set", "parameters searched", "optimum recorded"),
        GoalType.MANUFACTURE: ("design released", "manufacturing files produced"),
        GoalType.PROCURE: ("parts specified", "sources found", "order prepared"),
        GoalType.DOCUMENT: ("content gathered", "document recorded"),
        GoalType.UNDERSTAND: ("question answered",),
    }[goal]
    if measurable or goal is GoalType.UNDERSTAND:
        return base
    return ("measurable success criteria agreed", *base)
