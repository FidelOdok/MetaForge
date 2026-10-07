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
    "TargetMaturity",
    "missing_inputs",
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

    @property
    def has_capabilities(self) -> bool:
        return bool(self.processes or self.machines or self.stock_materials)


@dataclass(frozen=True)
class FlowContext:
    """Everything the generator is told about a project besides its intent."""

    manufacturing: ManufacturingContext | None = None
    target_maturity: TargetMaturity | None = None
    loads_and_use: str | None = None
    budget: str | None = None
    requirements: tuple[str, ...] = ()

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
