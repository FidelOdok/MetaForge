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

import re
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
    "DESIGN_PHASE_ID",
    "KICKOFF_AUTO",
    "KICKOFF_MISSING",
    "KICKOFF_PHASE_ID",
    "Kickoff",
    "KickoffDirection",
    "KickoffLesson",
    "KickoffQuestion",
    "INDUSTRIAL_DESIGN_PHASE_ID",
    "INDUSTRIAL_DESIGN_MISSING",
    "IndustrialDesignRecord",
    "industrial_design_gap",
    "parse_industrial_design",
    "assembly_conformance_gap",
    "kickoff_gap",
    "parse_kickoff",
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
    #: FORGE-451 / FORGE-446: process and material library entries the person
    #: chose, and one line each (rules or properties with their sources),
    #: resolved at proposal and frozen. With none chosen, the lines are the
    #: entries that match what was stated, or the library's entries.
    process_ids: tuple[str, ...] = ()
    material_ids: tuple[str, ...] = ()
    library_processes: tuple[str, ...] = ()
    library_materials: tuple[str, ...] = ()
    #: How the lines were picked: "chosen", "matched" or "listed".
    process_basis: str = ""
    material_basis: str = ""

    @property
    def has_capabilities(self) -> bool:
        return bool(
            self.processes
            or self.machines
            or self.stock_materials
            or self.library_machines
            or self.library_processes
            or self.library_materials
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
        if m.process_ids:
            parts.append(f"library processes: {', '.join(m.process_ids)}")
        if m.material_ids:
            parts.append(f"library materials: {', '.join(m.material_ids)}")
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
        headings = {
            "chosen": "chosen for this project",
            "matched": "matching what was stated (none chosen)",
            "listed": "in the library (none chosen or matched)",
        }
        if m.library_processes:
            lines.append(
                f"Manufacturing processes {headings.get(m.process_basis, '')}. Their rules are "
                "the design and DFM limits, with sources; library.process_limits gives them "
                "as limits, for a material if you name one:"
            )
            lines.extend(f"  - {line}" for line in m.library_processes)
        if m.library_materials:
            lines.append(
                f"Materials {headings.get(m.material_basis, '')}. Use these properties, with "
                "their sources, rather than values from memory; library.get_material has "
                "every property:"
            )
            lines.extend(f"  - {line}" for line in m.library_materials)
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


# ── the concept's selected assembly (FORGE-758) ───────────────────────────
#
# The Chair run reached CAD with its joints undecided: the concept was prose,
# so nothing said which parts met where or how they were held. A concept
# decision now carries the assembly it selects as data: the parts (made,
# bought or standard), the connections between them and each one's joint,
# every entry tagged with how sure it is. Design builds to it.

PART_SOURCES: tuple[str, ...] = ("made", "bought", "standard")
PROVENANCE_TAGS: tuple[str, ...] = ("verified", "intended", "assumption", "open")


@dataclass(frozen=True)
class AssemblyPart:
    name: str
    source: str = "made"
    material: str = ""
    process: str = ""
    quantity: int = 1
    provenance: str = "intended"

    def as_metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "material": self.material,
            "process": self.process,
            "quantity": self.quantity,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class AssemblyConnection:
    parts: tuple[str, str]
    joint: str
    fasteners: str = ""
    provenance: str = "intended"

    def as_metadata(self) -> dict[str, object]:
        return {
            "parts": list(self.parts),
            "joint": self.joint,
            "fasteners": self.fasteners,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ConceptAssembly:
    parts: tuple[AssemblyPart, ...]
    connections: tuple[AssemblyConnection, ...] = ()

    def summary(self) -> str:
        joints = "; ".join(f"{c.parts[0]} to {c.parts[1]}: {c.joint}" for c in self.connections)
        head = f"{len(self.parts)} part(s), {len(self.connections)} connection(s)"
        return f"{head} ({joints})" if joints else head

    def as_metadata(self) -> dict[str, object]:
        return {
            "parts": [p.as_metadata() for p in self.parts],
            "connections": [c.as_metadata() for c in self.connections],
        }

    def markdown(self) -> str:
        lines = [
            "## Selected assembly",
            "",
            "| Part | Source | Material | Process | Qty | Status |",
            "|---|---|---|---|---|---|",
        ]
        for p in self.parts:
            lines.append(
                f"| {p.name} | {p.source} | {p.material} | {p.process} | {p.quantity} "
                f"| {p.provenance} |"
            )
        if self.connections:
            lines += ["", "| Connection | Joint | Fasteners | Status |", "|---|---|---|---|"]
            for c in self.connections:
                lines.append(
                    f"| {c.parts[0]} to {c.parts[1]} | {c.joint} | {c.fasteners} | {c.provenance} |"
                )
        return "\n".join(lines) + "\n"


def _provenance(value: object, where: str) -> str:
    tag = _text(value) or "intended"
    if tag not in PROVENANCE_TAGS:
        raise ValueError(f"{where}.provenance must be one of {', '.join(PROVENANCE_TAGS)}")
    return tag


def _assembly_part(raw: object, where: str) -> AssemblyPart:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    name = _text(raw.get("name"))
    if not name:
        raise ValueError(f"{where} needs a name")
    source = _text(raw.get("source")) or "made"
    if source not in PART_SOURCES:
        raise ValueError(f"{where}.source must be one of {', '.join(PART_SOURCES)}")
    quantity = raw.get("quantity", 1)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise ValueError(f"{where}.quantity must be a whole number of at least 1")
    return AssemblyPart(
        name=name,
        source=source,
        material=_text(raw.get("material")),
        process=_text(raw.get("process")),
        quantity=quantity,
        provenance=_provenance(raw.get("provenance"), where),
    )


def _assembly_connection(raw: object, where: str, names: list[str]) -> AssemblyConnection:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    ends = raw.get("parts")
    if not isinstance(ends, list) or len(ends) != 2:
        raise ValueError(f"{where}.parts must name the two parts it joins")
    a, b = _text(ends[0]), _text(ends[1])
    unknown = [n for n in (a, b) if n not in names]
    if unknown:
        raise ValueError(f"{where} joins a part not in assembly.parts: {', '.join(unknown)}")
    joint = _text(raw.get("joint"))
    if not joint:
        raise ValueError(f"{where} needs its joint (e.g. dowel, mortise and tenon, bolted)")
    return AssemblyConnection(
        parts=(a, b),
        joint=joint,
        fasteners=_text(raw.get("fasteners")),
        provenance=_provenance(raw.get("provenance"), where),
    )


def parse_concept_assembly(value: object) -> ConceptAssembly:
    """An ``assembly`` value checked; raises ``ValueError`` saying why.

    At least one part, unique names, and every connection between two named
    parts with its joint stated: a connection with no joint is exactly the
    gap the concept phase exists to close.
    """
    if not isinstance(value, dict):
        raise ValueError("assembly must be an object with parts and connections")
    raw_parts = value.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("assembly.parts must list at least one part")
    parts = [_assembly_part(raw, f"assembly.parts[{i}]") for i, raw in enumerate(raw_parts)]
    names = [p.name for p in parts]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise ValueError(f"assembly.parts names must be unique; repeated: {', '.join(repeated)}")
    raw_connections = value.get("connections") or []
    if not isinstance(raw_connections, list):
        raise ValueError("assembly.connections must be a list")
    connections = [
        _assembly_connection(raw, f"assembly.connections[{i}]", names)
        for i, raw in enumerate(raw_connections)
    ]
    return ConceptAssembly(parts=tuple(parts), connections=tuple(connections))


@dataclass(frozen=True)
class ConceptOption:
    name: str
    summary: str
    selected: bool = False
    sketch_ref: str = ""

    def as_metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "summary": self.summary,
            "selected": self.selected,
            "sketch_ref": self.sketch_ref,
        }


def parse_concept_options(value: object) -> tuple[ConceptOption, ...]:
    """A ``concept_options`` value checked: each concept, exactly one selected."""
    if not isinstance(value, list) or not value:
        raise ValueError("concept_options must list the concepts considered")
    options: list[ConceptOption] = []
    for i, raw in enumerate(value):
        where = f"concept_options[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object")
        name = _text(raw.get("name"))
        summary = _text(raw.get("summary"))
        if not name or not summary:
            raise ValueError(f"{where} needs a name and a summary")
        options.append(
            ConceptOption(
                name=name,
                summary=summary,
                selected=raw.get("selected") is True,
                sketch_ref=_text(raw.get("sketch_ref")),
            )
        )
    chosen = [o for o in options if o.selected]
    if len(chosen) != 1:
        raise ValueError(f"concept_options must mark exactly one selected, got {len(chosen)}")
    return tuple(options)


# ── design follows the concept (FORGE-763) ────────────────────────────────

#: The phase that authors the CAD. Its gate compares the committed parts with
#: the assembly the concept selected.
DESIGN_PHASE_ID = "design"


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _covered(part: str, cad_names: list[str]) -> bool:
    key = _name_key(part)
    if not key:
        return True
    return any(key in _name_key(cad) or _name_key(cad) in key for cad in cad_names if cad)


def assembly_conformance_gap(assembly: ConceptAssembly | None, cad_names: list[str]) -> str | None:
    """What the design gate is missing against the selected concept, or None.

    Every part the concept makes needs a committed CAD part that carries its
    name ("Leg" is covered by "Leg 1" or "Chair Leg"). Bought and standard
    parts are not modelled necessarily, so they are not required. No recorded
    assembly means there is nothing to conform to, as in a flow whose concept
    phase records none.
    """
    if assembly is None:
        return None
    made = [p.name for p in assembly.parts if p.source == "made"]
    uncovered = [name for name in made if not _covered(name, cad_names)]
    if not uncovered:
        return None
    return (
        "CAD for every part the selected concept makes; none committed for: "
        + ", ".join(uncovered)
        + " (build them, named as in the concept, or record a revised concept decision "
        "with its assembly if the design has moved on)"
    )


# ── kickoff: deciding questions, directions and the pick ─────────────────
#
# The first phase of mech_v1 settles the direction before anything is
# designed: the few questions whose answers change the design most, two or
# three named directions for the product type, and the one the person picks
# (or "auto": the agent chooses and says why). Recorded as ``kickoff`` on a
# design decision; every later phase brief carries it, so nothing is asked
# twice, and the concept phase builds on the picked direction.

#: The phase that records the kickoff, and whose gate shows the directions.
KICKOFF_PHASE_ID = "kickoff"
#: The pick that leaves the choice to the agent ("you choose").
KICKOFF_AUTO = "auto"
#: Where an answer came from: the goal or flow context, the person, an
#: assumption the agent states, or still open.
KICKOFF_ANSWER_SOURCES: tuple[str, ...] = ("stated", "person", "assumed", "open")
#: The most deciding questions a kickoff asks: a few, not a questionnaire.
MAX_KICKOFF_QUESTIONS = 8
#: The most lessons a kickoff record carries.
MAX_KICKOFF_LESSONS = 10

KICKOFF_MISSING = (
    "kickoff decision (record-decision with kickoff: questions [{id, question, answer, "
    "source}], directions [{name, rationale, risk}] (two or three), picked (a direction "
    "name, or 'auto' with chosen and why), lessons [{text, source_run}])"
)


@dataclass(frozen=True)
class KickoffQuestion:
    id: str
    question: str
    answer: str = ""
    source: str = "stated"

    def as_metadata(self) -> dict[str, object]:
        return {
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "source": self.source,
        }


@dataclass(frozen=True)
class KickoffDirection:
    name: str
    rationale: str
    risk: str

    def as_metadata(self) -> dict[str, object]:
        return {"name": self.name, "rationale": self.rationale, "risk": self.risk}


@dataclass(frozen=True)
class KickoffLesson:
    text: str
    source_run: str = ""

    def as_metadata(self) -> dict[str, object]:
        return {"text": self.text, "source_run": self.source_run}


@dataclass(frozen=True)
class Kickoff:
    """A kickoff record, as recorded on a design decision."""

    questions: tuple[KickoffQuestion, ...]
    directions: tuple[KickoffDirection, ...]
    #: A direction's name, or :data:`KICKOFF_AUTO`.
    picked: str
    lessons: tuple[KickoffLesson, ...] = ()
    #: For ``auto``: the direction the agent chose.
    chosen: str = ""
    #: Why that direction (required for ``auto``, optional for a person's pick).
    why: str = ""

    @property
    def is_auto(self) -> bool:
        return self.picked == KICKOFF_AUTO

    @property
    def direction(self) -> KickoffDirection:
        """The direction the concept phase builds on."""
        name = self.chosen if self.is_auto else self.picked
        return next(d for d in self.directions if d.name == name)

    def pick_line(self) -> str:
        d = self.direction
        if self.is_auto:
            return f"{d.name} (left to the agent, which chose it because {self.why})"
        return f"{d.name} (picked by the person)" + (f": {self.why}" if self.why else "")

    def summary(self) -> str:
        """One line for the gate reason: the directions, their risks and the pick."""
        assumed = sum(1 for q in self.questions if q.source == "assumed")
        open_ = sum(1 for q in self.questions if q.source == "open")
        counts = f"{len(self.questions)} deciding question(s)"
        if assumed or open_:
            counts += f" ({assumed} assumed, {open_} open)"
        directions = "; ".join(f"{d.name}: {d.rationale} (risk: {d.risk})" for d in self.directions)
        lessons = f"; {len(self.lessons)} lesson(s) from earlier runs" if self.lessons else ""
        return f"kickoff: {counts}{lessons}; directions: {directions}; picked: {self.pick_line()}"

    def gate_note(self) -> str:
        """The gate's "pick a direction" card, as text."""
        others = ", ".join(d.name for d in self.directions if d.name != self.direction.name)
        return (
            f"{self.summary()}. Approving accepts {self.direction.name}; to pick "
            f"{others or 'another direction'} instead, retry naming it (or say 'you choose')"
        )

    def brief(self, *, include_lessons: bool = True) -> str:
        """The block every phase after the kickoff carries in its brief."""
        lines = [
            "Kickoff (settled at the start of this run; treat these answers as given and "
            "do not ask them again):"
        ]
        for q in self.questions:
            answer = q.answer if q.source != "open" else "(open: not yet answered)"
            lines.append(f"  - {q.question} {answer} [{q.source}]")
        d = self.direction
        lines.append(f"Direction: {self.pick_line()}. {d.rationale}. Main risk: {d.risk}.")
        others = [x.name for x in self.directions if x.name != d.name]
        if others:
            lines.append(f"Directions not taken: {', '.join(others)}.")
        if include_lessons and self.lessons:
            lines.append("Lessons from earlier runs:")
            lines.extend(f"  - {lesson.text}" for lesson in self.lessons)
        return "\n".join(lines)

    def markdown(self) -> str:
        lines = ["## Kickoff", "", "| Question | Answer | Source |", "|---|---|---|"]
        for q in self.questions:
            lines.append(f"| {q.question} | {q.answer} | {q.source} |")
        lines += ["", "| Direction | Rationale | Main risk |", "|---|---|---|"]
        for d in self.directions:
            lines.append(f"| {d.name} | {d.rationale} | {d.risk} |")
        lines += ["", f"Picked: {self.pick_line()}"]
        if self.lessons:
            lines += ["", "Lessons from earlier runs:"]
            lines += [f"- {lesson.text}" for lesson in self.lessons]
        return "\n".join(lines) + "\n"

    def as_metadata(self) -> dict[str, object]:
        return {
            "questions": [q.as_metadata() for q in self.questions],
            "directions": [d.as_metadata() for d in self.directions],
            "picked": self.picked,
            "chosen": self.chosen,
            "why": self.why,
            "lessons": [lesson.as_metadata() for lesson in self.lessons],
        }


def _kickoff_question(raw: object, where: str) -> KickoffQuestion:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    qid, question = _text(raw.get("id")), _text(raw.get("question"))
    if not qid or not question:
        raise ValueError(f"{where} needs an id and a question")
    source = _text(raw.get("source")) or "stated"
    if source not in KICKOFF_ANSWER_SOURCES:
        raise ValueError(f"{where}.source must be one of {', '.join(KICKOFF_ANSWER_SOURCES)}")
    answer = _text(raw.get("answer"))
    if not answer and source != "open":
        raise ValueError(f"{where} needs an answer, or source 'open' when nobody can answer yet")
    return KickoffQuestion(id=qid, question=question, answer=answer, source=source)


def _kickoff_direction(raw: object, where: str) -> KickoffDirection:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    name, rationale, risk = (_text(raw.get(k)) for k in ("name", "rationale", "risk"))
    if not name or not rationale or not risk:
        raise ValueError(f"{where} needs a name, a one-line rationale and its main risk")
    return KickoffDirection(name=name, rationale=rationale, risk=risk)


def _kickoff_lesson(raw: object, where: str) -> KickoffLesson:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object with text and source_run")
    text = _text(raw.get("text"))
    if not text:
        raise ValueError(f"{where} needs its text")
    return KickoffLesson(text=text, source_run=_text(raw.get("source_run")))


def _direction_named(name: str, directions: list[KickoffDirection]) -> KickoffDirection | None:
    key = _name_key(name)
    if not key:
        return None
    return next((d for d in directions if _name_key(d.name) == key), None)


def parse_kickoff(value: object) -> Kickoff:
    """A ``kickoff`` value checked; raises ``ValueError`` saying why.

    One to :data:`MAX_KICKOFF_QUESTIONS` deciding questions with unique ids,
    each answered with its source (or ``open``); two or three directions with
    distinct names, each with a rationale and its main risk; and a pick that
    names one of them, or ``auto`` with the direction the agent chose and why.
    """
    if not isinstance(value, dict):
        raise ValueError("kickoff must be an object with questions, directions and picked")
    raw_questions = value.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("kickoff.questions must list the deciding questions")
    if len(raw_questions) > MAX_KICKOFF_QUESTIONS:
        raise ValueError(
            f"kickoff.questions lists {len(raw_questions)}; ask at most "
            f"{MAX_KICKOFF_QUESTIONS}, the ones that change the design most"
        )
    questions = [
        _kickoff_question(raw, f"kickoff.questions[{i}]") for i, raw in enumerate(raw_questions)
    ]
    ids = [q.id for q in questions]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"kickoff.questions ids must be unique; repeated: {', '.join(repeated)}")
    raw_directions = value.get("directions")
    if not isinstance(raw_directions, list) or not 2 <= len(raw_directions) <= 3:
        raise ValueError("kickoff.directions must name two or three directions")
    directions = [
        _kickoff_direction(raw, f"kickoff.directions[{i}]") for i, raw in enumerate(raw_directions)
    ]
    keys = [_name_key(d.name) for d in directions]
    if len(set(keys)) != len(keys):
        raise ValueError("kickoff.directions names must be distinct")
    picked = _text(value.get("picked"))
    if not picked:
        raise ValueError("kickoff.picked must name a direction, or be 'auto'")
    chosen = ""
    why = _text(value.get("why"))
    if picked.lower() == KICKOFF_AUTO:
        picked = KICKOFF_AUTO
        match = _direction_named(_text(value.get("chosen")), directions)
        if match is None:
            raise ValueError("a kickoff picked 'auto' needs chosen: the direction the agent chose")
        if not why:
            raise ValueError("a kickoff picked 'auto' needs why: the reason for that direction")
        chosen = match.name
    else:
        match = _direction_named(picked, directions)
        if match is None:
            raise ValueError(f"kickoff.picked '{picked}' is not one of kickoff.directions")
        picked = match.name
    raw_lessons = value.get("lessons") or []
    if not isinstance(raw_lessons, list):
        raise ValueError("kickoff.lessons must be a list")
    lessons = [_kickoff_lesson(raw, f"kickoff.lessons[{i}]") for i, raw in enumerate(raw_lessons)]
    return Kickoff(
        questions=tuple(questions),
        directions=tuple(directions),
        picked=picked,
        lessons=tuple(lessons[:MAX_KICKOFF_LESSONS]),
        chosen=chosen,
        why=why,
    )


def kickoff_gap(kickoff: Kickoff | None) -> str | None:
    """What the kickoff gate is missing, or None when it may pass.

    A malformed record never reaches here: the recorder refuses it, and the
    gate reader skips one that does not parse, so it reads as missing.
    """
    return KICKOFF_MISSING if kickoff is None else None


# ── industrial design as constraints and evidence (FORGE-764) ──────────────
#
# The Chair's industrial design decision was prose: the joint and edge
# language never reached the design, ergonomics and finishes were asserted
# without the checks behind them, and open questions passed the gate. The
# industrial design decision now carries them as data, and its gate is not
# ready while a decision is still open.

INDUSTRIAL_DESIGN_PHASE_ID = "industrial_design"

INDUSTRIAL_DESIGN_MISSING = (
    "industrial_design on the decision (record-decision with industrial_design: "
    "joint_language, edge_treatment, ergonomics {verdict, source or reason}, "
    "finishes [{part, appearance, compatible}], open_decisions)"
)

_ERGONOMICS_VERDICTS = ("pass", "fail", "not_applicable")


@dataclass(frozen=True)
class FinishChoice:
    part: str
    appearance: str
    compatible: bool

    def as_metadata(self) -> dict[str, object]:
        return {"part": self.part, "appearance": self.appearance, "compatible": self.compatible}


@dataclass(frozen=True)
class IndustrialDesignRecord:
    joint_language: str
    edge_treatment: str
    ergonomics_verdict: str
    ergonomics_basis: str
    finishes: tuple[FinishChoice, ...] = ()
    open_decisions: tuple[str, ...] = ()

    def summary(self) -> str:
        return (
            f"joints: {self.joint_language}; edges: {self.edge_treatment}; ergonomics "
            f"{self.ergonomics_verdict.replace('_', ' ')}; {len(self.finishes)} finish(es)"
        )

    def as_metadata(self) -> dict[str, object]:
        verdict = self.ergonomics_verdict
        basis_key = "reason" if verdict == "not_applicable" else "source"
        return {
            "joint_language": self.joint_language,
            "edge_treatment": self.edge_treatment,
            "ergonomics": {"verdict": verdict, basis_key: self.ergonomics_basis},
            "finishes": [f.as_metadata() for f in self.finishes],
            "open_decisions": list(self.open_decisions),
        }


def parse_industrial_design(value: object) -> IndustrialDesignRecord:
    """An ``industrial_design`` value checked; raises ``ValueError`` saying why.

    The joint and edge language are what mechanical design must follow, so
    both are required. Ergonomics needs its verdict and the sourced limit it
    rests on, or, when nobody handles the product, the reason. Each finish
    says whether the material compatibility check passed.
    """
    if not isinstance(value, dict):
        raise ValueError("industrial_design must be an object")
    joint = _text(value.get("joint_language"))
    edge = _text(value.get("edge_treatment"))
    if not joint or not edge:
        raise ValueError(
            "industrial_design needs joint_language and edge_treatment "
            "(e.g. 'exposed dowels, flush', 'R3 on every hand-contact edge')"
        )
    ergonomics = value.get("ergonomics")
    if not isinstance(ergonomics, dict):
        raise ValueError("industrial_design.ergonomics must be an object with its verdict")
    verdict = _text(ergonomics.get("verdict"))
    if verdict not in _ERGONOMICS_VERDICTS:
        raise ValueError(
            f"industrial_design.ergonomics.verdict must be one of {', '.join(_ERGONOMICS_VERDICTS)}"
        )
    if verdict == "not_applicable":
        basis = _text(ergonomics.get("reason"))
        if not basis:
            raise ValueError("ergonomics not_applicable needs the reason nobody handles it")
    else:
        basis = _text(ergonomics.get("source"))
        if not basis:
            raise ValueError(
                "industrial_design.ergonomics needs the source of its limits "
                "(dataset, table and percentile)"
            )
    raw_finishes = value.get("finishes") or []
    if not isinstance(raw_finishes, list):
        raise ValueError("industrial_design.finishes must be a list")
    finishes: list[FinishChoice] = []
    for i, raw in enumerate(raw_finishes):
        where = f"industrial_design.finishes[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object")
        part, appearance = _text(raw.get("part")), _text(raw.get("appearance"))
        if not part or not appearance:
            raise ValueError(f"{where} needs part and appearance")
        if not isinstance(raw.get("compatible"), bool):
            raise ValueError(f"{where}.compatible must say whether check_cmf_compatibility passed")
        finishes.append(FinishChoice(part, appearance, raw["compatible"]))
    open_decisions = value.get("open_decisions") or []
    if not isinstance(open_decisions, list):
        raise ValueError("industrial_design.open_decisions must be a list")
    return IndustrialDesignRecord(
        joint_language=joint,
        edge_treatment=edge,
        ergonomics_verdict=verdict,
        ergonomics_basis=basis,
        finishes=tuple(finishes),
        open_decisions=_texts(open_decisions),
    )


def industrial_design_gap(record: IndustrialDesignRecord | None) -> str | None:
    """What the industrial design gate is missing, or None when it may pass."""
    if record is None:
        return INDUSTRIAL_DESIGN_MISSING
    if record.open_decisions:
        return "industrial design decisions still open: " + "; ".join(record.open_decisions)
    if record.ergonomics_verdict == "fail":
        return f"an ergonomics pass: the check against {record.ergonomics_basis} failed"
    clashing = [f.part for f in record.finishes if not f.compatible]
    if clashing:
        return "finishes compatible with their material for: " + ", ".join(clashing)
    if not record.finishes:
        return "a finish per visible part (or list the finishes still to choose in open_decisions)"
    return None
