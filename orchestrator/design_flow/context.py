"""What the generator must know before it tailors a flow (FORGE-463).

The generator used to take an intent and nothing else, and it filled the gaps
by guessing. ``"A wall shelf I can make with what I have."`` came back as a
``mech_v1`` proposal that dropped simulation because "practical load testing
will suffice" -- with no idea what the shelf holds, what "what I have" is, or
how far the person wants to take it. Every one of those is a decision the
flow depends on, and a guessed one reads, in the proposal, exactly like an
answered one.

So the inputs that decide the shape of a flow are named here, and when one is
missing the proposal asks instead of guessing. The questions are MetaForge's
own and deterministic: whether a flow can be tailored at all must not depend
on a model remembering to ask. The model may add a few product-specific
questions on top (see ``api_gateway.design_flows.generate``), capped at
:data:`MAX_EXTRA_QUESTIONS` -- the same "few high-value questions, not a
questionnaire" policy as ``requirement_intelligence.clarification``.

Pure data and pure functions, no I/O: the route, the MCP binding and the
invariants all read the same answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "MAX_EXTRA_QUESTIONS",
    "ClarifyingQuestion",
    "FlowContext",
    "ManufacturingContext",
    "ManufacturingRoute",
    "DFM_REVIEW_MISSING",
    "DFM_REVIEW_PHASE_ID",
    "PROCESS_RANKING_MISSING",
    "ROUTE_DECISION_MISSING",
    "DfmReview",
    "ProcessRanking",
    "RankedCandidate",
    "dfm_review_gap",
    "parse_dfm_review",
    "parse_process_ranking",
    "RouteDecision",
    "TargetMaturity",
    "missing_inputs",
    "parse_route_decision",
    "route_decision_gap",
]

#: The most product-specific questions a model may add to the required ones.
MAX_EXTRA_QUESTIONS = 3

#: Answers that mean "I do not know", so the loads are still unknown even
#: though the field is filled in. Answering is not the same as knowing, and
#: the invariants care about the second.
_UNKNOWN_ANSWERS = frozenset(
    {"unknown", "not sure", "unsure", "idk", "don't know", "dont know", "n/a", "na", "tbd", "?"}
)


class ManufacturingRoute(StrEnum):
    #: Made with the person's own processes, machines and stock.
    IN_HOUSE = "in_house"
    #: Made by a supplier (machine shop, PCB fab, printing service, ...).
    VENDOR = "vendor"
    #: Not decided. A real answer: it adds a route-selection decision step to
    #: the flow instead of letting the generator pick one silently.
    UNDECIDED = "undecided"


class TargetMaturity(StrEnum):
    CONCEPT = "concept"
    SIM_VALIDATED = "sim_validated"
    PHYSICALLY_VALIDATED = "physically_validated"
    RELEASED = "released"


@dataclass(frozen=True)
class ManufacturingContext:
    route: ManufacturingRoute | None = None
    processes: tuple[str, ...] = ()
    #: Free-form capability descriptions ("Prusa MK4, 250x210x220 mm, PLA/PETG").
    machines: tuple[str, ...] = ()
    stock_materials: tuple[str, ...] = ()
    production_quantity: int | None = None
    #: FORGE-451: machine-library instances the person chose for this
    #: project. Empty means none chosen.
    machine_ids: tuple[str, ...] = ()
    #: One line per library machine with its key limits, resolved when the
    #: flow is proposed and frozen with it: the chosen machines, or, when
    #: none were chosen, every available one so the generator and the phases
    #: know what exists.
    library_machines: tuple[str, ...] = ()

    @property
    def has_capabilities(self) -> bool:
        return bool(
            self.processes or self.machines or self.stock_materials or self.library_machines
        )


#: Definition items whose current revision a new flow can reuse instead of
#: producing again (FORGE-571): the front of the lifecycle, settled once.
REUSABLE_ITEM_TYPES: tuple[str, ...] = ("intent", "stakeholder_need", "constraint_set")


@dataclass(frozen=True)
class FlowContext:
    """Everything the generator is told about a project besides its intent."""

    manufacturing: ManufacturingContext | None = None
    target_maturity: TargetMaturity | None = None
    loads_and_use: str | None = None
    budget: str | None = None
    requirements: tuple[str, ...] = ()
    #: FORGE-571: which :data:`REUSABLE_ITEM_TYPES` already have a current
    #: revision in the project's twin. ``None`` means nobody checked (no
    #: project, or the twin could not be read): then nothing is reused,
    #: because a missing fact would make a reuse condition skip the phase.
    current_items: tuple[str, ...] | None = None

    @property
    def loads_known(self) -> bool:
        text = (self.loads_and_use or "").strip().lower().rstrip(".!")
        return bool(text) and text not in _UNKNOWN_ANSWERS

    @property
    def requirements_pending(self) -> bool:
        """No requirements recorded yet.

        Said explicitly on every proposal made in this state, because "every
        requirement is verified" is trivially true of none -- which is how a
        flow with its verification dropped passed as valid (FORGE-463).
        """
        return not self.requirements

    @property
    def route(self) -> ManufacturingRoute | None:
        return self.manufacturing.route if self.manufacturing else None

    def facts(self) -> dict[str, str]:
        """The facts a phase ``condition`` may test (FORGE-539).

        Only what was stated: an unstated route is absent, not ``"undecided"``,
        so a condition naming it is false rather than true on a guess. Values
        are strings because conditions compare literals.
        """
        out: dict[str, str] = {}
        if self.route is not None:
            out["route"] = self.route.value
        if self.target_maturity is not None:
            out["target_maturity"] = self.target_maturity.value
        if (self.loads_and_use or "").strip():
            out["loads_known"] = "true" if self.loads_known else "false"
        m = self.manufacturing
        if m is not None and m.production_quantity is not None:
            out["production_quantity"] = str(m.production_quantity)
        out["budget_stated"] = "true" if (self.budget or "").strip() else "false"
        out["requirements_recorded"] = "false" if self.requirements_pending else "true"
        if self.current_items is not None:
            for item_type in REUSABLE_ITEM_TYPES:
                out[f"{item_type}_current"] = "true" if item_type in self.current_items else "false"
        return out

    def capability_basis(self) -> str:
        """The stated capabilities, in one line, for each change's rationale.

        Recorded on the change rather than left to the model to cite, so a
        reviewer can see what the generator was told when it made the choice
        -- whether or not the model remembered to mention it.
        """
        m = self.manufacturing
        if m is None or m.route is None:
            return "manufacturing route not stated"
        parts = [f"route {m.route.value}"]
        if m.processes:
            parts.append(f"processes: {', '.join(m.processes)}")
        if m.machines:
            parts.append(f"machines: {'; '.join(m.machines)}")
        if m.machine_ids:
            parts.append(f"library machines: {', '.join(m.machine_ids)}")
        if m.stock_materials:
            parts.append(f"stock: {', '.join(m.stock_materials)}")
        if m.production_quantity is not None:
            parts.append(f"quantity: {m.production_quantity}")
        return "; ".join(parts)

    def render_for_phases(self) -> str:
        """The block every phase brain receives (FORGE-491).

        Deterministic for a given context, so it is byte-identical across
        phases and runs and sits in the cacheable prompt prefix. Empty when
        nothing was stated, so a flow with no context adds no block.
        """
        if (
            self.manufacturing is None
            and self.target_maturity is None
            and not (self.loads_and_use or "").strip()
            and not (self.budget or "").strip()
            and not self.requirements
        ):
            return ""
        reqs = "\n".join(f"  - {r}" for r in self.requirements) or "  (none recorded yet)"
        return f"{self.describe_for_prompt()}\nRequirements stated:\n{reqs}"

    def describe_for_prompt(self) -> str:
        m = self.manufacturing or ManufacturingContext()

        def _or(values: tuple[str, ...], sep: str = ", ") -> str:
            return sep.join(values) if values else "(none stated)"

        loads = (self.loads_and_use or "").strip()
        lines = [
            f"Manufacturing route: {m.route.value if m.route else '(not stated)'}",
            f"Processes available: {_or(m.processes)}",
            f"Machines available: {_or(m.machines, '; ')}",
            f"Stock materials: {_or(m.stock_materials)}",
            "Production quantity: "
            + (str(m.production_quantity) if m.production_quantity is not None else "(not stated)"),
            "Target maturity: "
            + (self.target_maturity.value if self.target_maturity else "(not stated)"),
            "Loads and use: "
            + (loads if self.loads_known else f"UNKNOWN ({loads or 'not stated'})"),
            f"Budget: {(self.budget or '').strip() or '(not stated)'}",
        ]
        if m.library_machines:
            # Only when there are some, so a flow with no library machines
            # renders exactly as before and keeps its hash.
            heading = (
                "Machines chosen from the machine library"
                if m.machine_ids
                else "Machines in the machine library (available; none chosen for this project)"
            )
            lines.append(
                f"{heading}. Rank and check against these, never from memory; "
                "library.machine_limits gives each one's limits with their sources:"
            )
            lines.extend(f"  - {line}" for line in m.library_machines)
        return "\n".join(lines)


@dataclass(frozen=True)
class ClarifyingQuestion:
    """One thing the generator needs answered before it can tailor a flow."""

    id: str
    question: str
    #: Why the answer changes the flow, in the words of whoever has to answer.
    why: str
    #: ``choice`` | ``text`` | ``list`` | ``number``.
    answer_type: str
    options: tuple[str, ...] = ()
    #: The request field the answer goes into, so a client can resubmit.
    field: str = ""
    required: bool = True
    #: ``metaforge`` for the deterministic ones, ``model`` for extras.
    source: str = "metaforge"


_ROUTE_QUESTION = ClarifyingQuestion(
    id="manufacturing_route",
    question="Will you make this yourself, have a vendor make it, or is that undecided?",
    why=(
        "The route decides which processes and materials the design may assume. "
        "'undecided' is a fine answer: the flow then gets an explicit route-selection "
        "step instead of the generator picking one for you."
    ),
    answer_type="choice",
    options=tuple(r.value for r in ManufacturingRoute),
    field="manufacturingContext.route",
)

_CAPABILITY_QUESTION = ClarifyingQuestion(
    id="in_house_capabilities",
    question=(
        "What can you make it with? List the processes, machines (with sizes or "
        "limits) and stock materials you have."
    ),
    why=(
        "An in-house design is only buildable if it fits the tools and stock you "
        "actually have; without them the generator would be guessing what 'what I "
        "have' means."
    ),
    answer_type="list",
    field="manufacturingContext.processes / machines / stockMaterials",
)

_MATURITY_QUESTION = ClarifyingQuestion(
    id="target_maturity",
    question=(
        "How far should this go: a concept, simulation-validated, physically tested, or released?"
    ),
    why=(
        "Maturity decides which verification the flow must carry. A concept can stop "
        "early; a physically validated part needs a test plan; a release needs both "
        "evidence and sign-off."
    ),
    answer_type="choice",
    options=tuple(m.value for m in TargetMaturity),
    field="targetMaturity",
)

_LOADS_QUESTION = ClarifyingQuestion(
    id="loads_and_use",
    question=(
        "What will it carry or endure, and how is it used? (e.g. '20 kg of books, "
        "indoors, wall-mounted on studs'). 'unknown' is an allowed answer."
    ),
    why=(
        "Loads decide whether simulation or a physical test is needed. While they "
        "are unknown, MetaForge will not drop the verification phase."
    ),
    answer_type="text",
    field="loadsAndUse",
)


def missing_inputs(context: FlowContext) -> list[ClarifyingQuestion]:
    """The required questions still unanswered, in a fixed order.

    Deterministic on purpose: the same request always gets the same questions,
    so a client can render them as a form and a test can assert on them.
    """
    questions: list[ClarifyingQuestion] = []
    if context.route is None:
        questions.append(_ROUTE_QUESTION)
    elif (
        context.route is ManufacturingRoute.IN_HOUSE
        and context.manufacturing is not None
        and not context.manufacturing.has_capabilities
    ):
        questions.append(_CAPABILITY_QUESTION)
    if context.target_maturity is None:
        questions.append(_MATURITY_QUESTION)
    if not (context.loads_and_use or "").strip():
        questions.append(_LOADS_QUESTION)
    if questions:
        logger.info("flow_context_incomplete", missing=[q.id for q in questions])
    return questions


# ── The route decision (FORGE-608) ────────────────────────────────────────
#
# An undecided route adds a route-selection phase (``generator``). Its gate
# used to pass on any decision at all, so "route NOT SELECTED (deferred)" got
# through and the design went ahead on assumed materials with nothing saying
# so. The decision now carries a typed ``manufacturing_route``, and the gate
# reads it: a chosen route, process and material class, or a deferral the
# person accepts by approving the gate with its reason in front of them.

#: What the gate lists as missing when no usable route decision is recorded.
ROUTE_DECISION_MISSING = (
    "manufacturing_route decision (record-decision with manufacturing_route: "
    "route in_house or vendor, process and material_class; or deferred with a reason)"
)


@dataclass(frozen=True)
class RouteDecision:
    """A manufacturing route decision, as recorded on a design decision."""

    route: str | None = None
    process: str = ""
    material_class: str = ""
    deferred: bool = False
    reason: str = ""
    #: FORGE-611: the processes compared, read from the same decision.
    ranking: ProcessRanking | None = None

    def as_metadata(self) -> dict[str, object]:
        if self.deferred:
            return {"deferred": True, "reason": self.reason}
        return {"route": self.route, "process": self.process, "material_class": self.material_class}

    def summary(self) -> str:
        if self.deferred:
            return f"route DEFERRED: {self.reason}"
        chosen = f"route {self.route}, process {self.process}, material {self.material_class}"
        return f"{chosen}; {self.ranking.summary()}" if self.ranking else chosen


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_route_decision(value: object) -> RouteDecision:
    """A ``manufacturing_route`` value checked; raises ``ValueError`` saying why.

    Either a route (``in_house`` or ``vendor``, not ``undecided``: deciding is
    the point) with a process and a material class, or ``deferred: true`` with
    a reason. A deferral is a decision too, but only with its reason stated.
    """
    if not isinstance(value, dict):
        raise ValueError("manufacturing_route must be an object")
    if value.get("deferred") is True:
        reason = _text(value.get("reason"))
        if not reason:
            raise ValueError("a deferred manufacturing_route needs a reason")
        return RouteDecision(deferred=True, reason=reason)
    route = _text(value.get("route"))
    if route not in (ManufacturingRoute.IN_HOUSE.value, ManufacturingRoute.VENDOR.value):
        raise ValueError(
            "manufacturing_route.route must be 'in_house' or 'vendor' "
            "(or set deferred: true with a reason)"
        )
    process = _text(value.get("process"))
    material = _text(value.get("material_class"))
    if not process or not material:
        raise ValueError("manufacturing_route needs process and material_class")
    return RouteDecision(route=route, process=process, material_class=material)


def route_decision_gap(decision: RouteDecision | None) -> str | None:
    """What the route-selection gate is missing, or None when it may pass.

    FORGE-611: a chosen route also needs the ranking it was chosen from, so
    the person picks between compared processes rather than approving one
    that appeared from nowhere. A deferral needs no ranking.
    """
    if decision is None:
        return ROUTE_DECISION_MISSING
    if not decision.deferred and decision.ranking is None:
        return PROCESS_RANKING_MISSING
    return None


# ── Process ranking and DFM review (FORGE-611) ─────────────────────────────
#
# The process is chosen before the design, from what is available
# (``rank_manufacturing_processes``), and the person picks at the route gate.
# After the design, ``check_manufacturability`` checks it against that
# process, and the DFM gate reads the verdict.

#: The phase that reviews the design against its process. Distinct from
#: hardware_v1's ``manufacturing`` phase, which this rule must not touch.
DFM_REVIEW_PHASE_ID = "dfm_review"

PROCESS_RANKING_MISSING = (
    "process_ranking on the route decision (record-decision with process_ranking: "
    "the candidate processes compared, each with its reasons; a process the person "
    "stated up front is a one-candidate ranking)"
)

DFM_REVIEW_MISSING = (
    "dfm_review decision (record-decision with dfm_review: process, verdict, findings "
    "and the manufacturing plan)"
)

_DFM_VERDICTS = ("manufacturable", "not_manufacturable", "incomplete")


@dataclass(frozen=True)
class RankedCandidate:
    process: str
    reasons: tuple[str, ...]
    route: str | None = None
    verdict: str = ""
    cost_at_quantity: float | None = None


@dataclass(frozen=True)
class ProcessRanking:
    candidates: tuple[RankedCandidate, ...]
    recommended: str | None = None

    def summary(self) -> str:
        rows = []
        for i, c in enumerate(self.candidates, 1):
            cost = f", cost {c.cost_at_quantity:g}" if c.cost_at_quantity is not None else ""
            rows.append(f"{i}. {c.process}" + (f" ({c.verdict}{cost})" if c.verdict else cost))
        head = f"recommended {self.recommended}; " if self.recommended else ""
        return head + "ranking: " + "; ".join(rows)

    def as_metadata(self) -> dict[str, object]:
        return {
            "recommended": self.recommended,
            "candidates": [
                {
                    "process": c.process,
                    "route": c.route,
                    "verdict": c.verdict,
                    "cost_at_quantity": c.cost_at_quantity,
                    "reasons": list(c.reasons),
                }
                for c in self.candidates
            ],
        }


def parse_process_ranking(value: object) -> ProcessRanking:
    """A ``process_ranking`` value checked; raises ``ValueError`` saying why."""
    if isinstance(value, list):
        value = {"candidates": value}
    if not isinstance(value, dict):
        raise ValueError("process_ranking must be an object with candidates")
    raw = value.get("candidates")
    if not isinstance(raw, list) or not raw:
        raise ValueError("process_ranking needs at least one candidate")
    candidates: list[RankedCandidate] = []
    for item in raw:
        if not isinstance(item, dict) or not _text(item.get("process")):
            raise ValueError("every process_ranking candidate needs a process")
        reasons = item.get("reasons")
        if not isinstance(reasons, list) or not [r for r in reasons if _text(r)]:
            raise ValueError(f"candidate '{item.get('process')}' needs its reasons")
        cost = item.get("cost_at_quantity")
        candidates.append(
            RankedCandidate(
                process=_text(item.get("process")),
                reasons=tuple(_text(r) for r in reasons if _text(r)),
                route=_text(item.get("route")) or None,
                verdict=_text(item.get("verdict")),
                cost_at_quantity=float(cost) if isinstance(cost, int | float) else None,
            )
        )
    return ProcessRanking(
        candidates=tuple(candidates), recommended=_text(value.get("recommended")) or None
    )


@dataclass(frozen=True)
class DfmReview:
    process: str
    verdict: str
    passed: int = 0
    failed: int = 0
    unknown: int = 0
    unknowns: tuple[str, ...] = ()
    stock: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    quantity: int | None = None
    unit_cost_estimate: float | None = None
    cost_source: str = ""

    def summary(self) -> str:
        counts = f"{self.passed} pass, {self.failed} fail, {self.unknown} unknown"
        if self.verdict == "incomplete":
            missing = "; ".join(self.unknowns[:5]) or "see the findings"
            return f"DFM incomplete for {self.process} ({counts}): {missing}"
        return f"DFM {self.verdict.replace('_', ' ')} for {self.process} ({counts})"

    def as_metadata(self) -> dict[str, object]:
        return {
            "process": self.process,
            "verdict": self.verdict,
            "findings": {"pass": self.passed, "fail": self.failed, "unknown": self.unknown},
            "unknowns": list(self.unknowns),
            "plan": {
                "stock": list(self.stock),
                "operations": list(self.operations),
                "quantity": self.quantity,
                "unit_cost_estimate": self.unit_cost_estimate,
                "cost_source": self.cost_source,
            },
        }


def _count(value: object, name: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"dfm_review.findings.{name} must be a whole number")
    return value


def _texts(value: object) -> tuple[str, ...]:
    return tuple(_text(v) for v in value if _text(v)) if isinstance(value, list) else ()


def parse_dfm_review(value: object) -> DfmReview:
    """A ``dfm_review`` value checked; raises ``ValueError`` saying why."""
    if not isinstance(value, dict):
        raise ValueError("dfm_review must be an object")
    process = _text(value.get("process"))
    if not process:
        raise ValueError("dfm_review needs the process it was checked against")
    verdict = _text(value.get("verdict"))
    if verdict not in _DFM_VERDICTS:
        raise ValueError(f"dfm_review.verdict must be one of {', '.join(_DFM_VERDICTS)}")
    findings = value.get("findings") or {}
    if not isinstance(findings, dict):
        raise ValueError("dfm_review.findings must be an object of counts")
    plan = value.get("plan") or {}
    if not isinstance(plan, dict):
        raise ValueError("dfm_review.plan must be an object")
    operations = _texts(plan.get("operations"))
    if verdict != "not_manufacturable" and not operations:
        raise ValueError("dfm_review.plan needs the operations, in order")
    cost = plan.get("unit_cost_estimate")
    cost_value = float(cost) if isinstance(cost, int | float) else None
    cost_source = _text(plan.get("cost_source"))
    if cost_value is not None and not cost_source:
        raise ValueError("a unit_cost_estimate needs its cost_source")
    quantity = plan.get("quantity")
    return DfmReview(
        process=process,
        verdict=verdict,
        passed=_count(findings.get("pass"), "pass"),
        failed=_count(findings.get("fail"), "fail"),
        unknown=_count(findings.get("unknown"), "unknown"),
        unknowns=_texts(value.get("unknowns")),
        stock=_texts(plan.get("stock")),
        operations=operations,
        quantity=quantity if isinstance(quantity, int) and quantity > 0 else None,
        unit_cost_estimate=cost_value,
        cost_source=cost_source,
    )


def dfm_review_gap(review: DfmReview | None) -> str | None:
    """What the DFM gate is missing, or None when it may pass.

    ``incomplete`` passes: the summary lists what could not be checked and
    approving accepts it, as with a route deferral.
    """
    if review is None:
        return DFM_REVIEW_MISSING
    if review.verdict == "not_manufacturable":
        return (
            f"a manufacturable design: the DFM review against {review.process} found "
            f"{review.failed} failing feature(s); rework the design or reopen the process"
        )
    return None
