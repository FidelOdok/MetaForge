"""The load-bearing bracket: the first workflow scenario (FORGE-562/563).

Simple but complete: intent, requirements, a workflow, execution, evidence
and verification, in one engineering domain. Everything that decides the
expected behaviour is fixed here, so a test failure is about the code, not
about an ambiguous scenario:

* load: 10 kg, i.e. 98.1 N static, downward, on the tip face;
* mounting: two M5 bolts on the back face, which is held fixed;
* material: Al 6061-T6, yield 276 MPa (a cited handbook value);
* acceptance: factor of safety against yield of at least 2;
* envelope: 80 mm x 60 mm x 40 mm.

Two things are built from it. :func:`generate` runs the real generation
path (intent compiler, then ``build_proposal`` tailoring ``mech_v1``), which
is what the generation suite tests. :func:`accept` takes that proposal
through the real acceptance path (``FlowVersionStore.save`` binds slots,
validates and freezes it; ``decide`` approves it), and the result is the
contract the lifecycle suite starts from. A lifecycle test never depends on
generation having been right: it gets this fixed, accepted workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_core.profiles import DELIVERABLE_TOOLS
from orchestrator.design_flow.context import (
    FlowContext,
    ManufacturingContext,
    ManufacturingRoute,
    TargetMaturity,
)
from orchestrator.design_flow.frozen import FrozenFlow
from orchestrator.design_flow.generator import (
    CallerProvenance,
    FlowProposal,
    build_proposal,
    parse_caller_operations,
)
from orchestrator.design_flow.intent import IntentModel, compile_intent
from orchestrator.design_flow.spec import FlowDefinition, definition_from_frozen, get_flow
from orchestrator.design_flow.spec import flow_version as template_version
from orchestrator.design_flow.versions import FlowVersion, FlowVersionStore

TEMPLATE = "mech_v1"

LOAD_N = 98.1
LOAD_KG = 10.0
YIELD_MPA = 276.0
MIN_SAFETY_FACTOR = 2.0
ENVELOPE_MM = (80.0, 60.0, 40.0)

INTENT = (
    "Design a wall-mounted bracket that supports a 10 kg load with a factor of safety "
    "of at least 2, within 80 x 60 x 40 mm. Prefer aluminium if possible. "
    "Deliver CAD and validation evidence."
)

#: The mandatory requirements, each with what verifies it.
REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "REQ-LOAD",
        "text": "The bracket shall support a static load of 98.1 N (10 kg) at its tip face.",
        "verified_by": "simulation_result",
    },
    {
        "id": "REQ-FOS",
        "text": "The minimum factor of safety against yield shall be at least 2.",
        "verified_by": "simulation_result",
    },
    {
        "id": "REQ-ENVELOPE",
        "text": "The bracket shall fit within an envelope of 80 mm x 60 mm x 40 mm.",
        "verified_by": "cad_model",
    },
)

LOADS_AND_USE = (
    "98.1 N static, downward, on the tip face; fixed by two M5 bolts on the back face; "
    "Al 6061-T6, yield 276 MPa"
)


def context(*, loads: str | None = LOADS_AND_USE, budget: str | None = "GBP 200") -> FlowContext:
    """The project context the generator is told, besides the intent."""
    return FlowContext(
        manufacturing=ManufacturingContext(
            route=ManufacturingRoute.VENDOR,
            processes=("cnc_milling",),
            stock_materials=("Al 6061-T6",),
        ),
        target_maturity=TargetMaturity.SIM_VALIDATED,
        loads_and_use=loads,
        budget=budget,
        requirements=tuple(r["text"] for r in REQUIREMENTS),
    )


#: How a client tailors ``mech_v1`` for the bracket: what flow.propose would be
#: sent. Each operation carries its rationale, as the generator requires.
TAILORING: list[dict[str, Any]] = [
    {
        "op": "set_outcome",
        "phase": "intent",
        "value": "why the bracket exists and what it must hold, agreed",
        "rationale": "outcomes come before tasks",
    },
    {
        "op": "set_outcome",
        "phase": "needs",
        "value": "stakeholder needs for the mounting, load and envelope recorded",
        "rationale": "outcomes come before tasks",
    },
    {
        "op": "set_outcome",
        "phase": "requirements",
        "value": "load, safety factor and envelope requirements established",
        "rationale": "outcomes come before tasks",
    },
    {
        "op": "set_outcome",
        "phase": "feasibility",
        "value": "load path and section sizing shown feasible in Al 6061-T6",
        "rationale": "outcomes come before tasks",
    },
    {
        "op": "set_outcome",
        "phase": "design",
        "value": "bracket geometry within the envelope committed",
        "rationale": "outcomes come before tasks",
    },
    {
        "op": "set_outcome",
        "phase": "simulation",
        "value": "factor of safety against yield shown to be at least 2 under 98.1 N",
        "rationale": "outcomes come before tasks",
    },
    {
        "op": "declare_items",
        "phase": "design",
        "value": [{"type": "cad_model", "name": "Bracket"}],
        "rationale": "one part; every revision lands on the same item",
    },
    {
        "op": "add_deliverable",
        "phase": "simulation",
        "value": "simulation_result",
        "rationale": "REQ-LOAD and REQ-FOS are verified by analysis; the gate must require "
        "the result, not merely expect it",
    },
]


@dataclass(frozen=True)
class Accepted:
    """An accepted bracket workflow: the contract between the two suites."""

    version: FlowVersion
    store: FlowVersionStore

    @property
    def frozen(self) -> FrozenFlow:
        return self.version.frozen

    @property
    def definition(self) -> FlowDefinition:
        """Rebuilt from the frozen, hashed content, as a run would see it."""
        return definition_from_frozen(self.version.frozen)

    @property
    def content_hash(self) -> str:
        return self.version.frozen.content_hash

    @property
    def phase_ids(self) -> list[str]:
        return [p.id for p in self.definition.phases]

    def item_key(self, phase_id: str, item_type: str) -> str:
        phase = next(p for p in self.definition.phases if p.id == phase_id)
        from orchestrator.design_flow.slots import effective_slots

        for slot in effective_slots(phase):
            if slot.item_type == item_type:
                return slot.item_key
        raise KeyError(f"{phase_id} has no {item_type} slot")


def compile(**overrides: Any) -> IntentModel:
    ctx = overrides.pop("context", None) or context()
    return compile_intent(
        overrides.pop("intent", INTENT),
        context=ctx,
        requirements=overrides.pop("requirements", tuple(r["text"] for r in REQUIREMENTS)),
    )


def generate(
    *, operations: list[dict[str, Any]] | None = None, ctx: FlowContext | None = None
) -> FlowProposal:
    """The real generation path for the bracket.

    ``operations=None`` uses :data:`TAILORING`; ``[]`` is the template as is,
    which is what a generator that tailors nothing would hand over.
    """
    base = get_flow(TEMPLATE)
    ops = parse_caller_operations(TAILORING if operations is None else operations, base)
    return build_proposal(
        base,
        base_version=template_version(TEMPLATE),
        operations=ops,
        intent=INTENT,
        context=ctx or context(),
        proposed_by=CallerProvenance(client="tests.workflow", model="scripted"),
    )


def accept(proposal: FlowProposal | None = None) -> Accepted:
    """Save and approve the proposal through the real version store."""
    proposal = proposal or generate()
    store = FlowVersionStore()
    ctx = proposal.context
    version = store.save(
        proposal.definition,
        base_template_id=proposal.base_template_id,
        base_version=proposal.base_version,
        changes=proposal.diff(),
        origin="caller",
        intent=INTENT,
        context=ctx.render_for_phases() if ctx else "",
        facts=ctx.facts() if ctx else None,
    )
    version = store.decide(version.id, approved=True, decided_by="reviewer@example.test")
    return Accepted(version=version, store=store)


def all_tools() -> set[str]:
    """Every tool that produces some deliverable: a fully provisioned server."""
    return {tool for tools in DELIVERABLE_TOOLS.values() for tool in tools}
