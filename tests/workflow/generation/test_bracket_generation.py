"""Workflow generation, bracket: did we create the right workflow? (FORGE-564)

Inputs are the intent, the project context and the available tools; the
output is a validated workflow, a readiness status and explicit gaps. No
domain tool runs. Every test asserts a property a correct workflow must
have, never one exact task list: several workflows satisfy the same intent.

Properties the code does not meet yet are ``xfail(strict=True)`` with the
ticket that tracks them, so the suite stays green while the gap stays
visible, and the marker has to come off the day the gap closes.
"""

from __future__ import annotations

import pytest

from mcp_core.profiles import DELIVERABLE_TOOLS
from orchestrator.design_flow.capabilities import Coverage, assess_capabilities
from orchestrator.design_flow.graph import build_graph
from orchestrator.design_flow.intent import ConstraintCategory
from tests.workflow.scenarios import bracket

# --- Constraints, preferences and unknowns -----------------------------------


def test_the_load_is_a_hard_constraint_with_its_value() -> None:
    model = bracket.compile()
    loads = [
        c
        for c in model.constraints
        if c.category is ConstraintCategory.MECHANICAL and c.value is not None
    ]
    assert any(c.value == bracket.LOAD_N and c.unit == "N" for c in loads)
    assert any(c.value == bracket.LOAD_KG and c.unit == "kg" for c in loads)


@pytest.mark.xfail(
    strict=True, reason="FORGE-569: a dimensionless 'at least 2' is not compiled as a criterion"
)
def test_the_safety_factor_is_a_measurable_criterion() -> None:
    model = bracket.compile()
    assert any(
        s.operator == ">=" and s.limit == bracket.MIN_SAFETY_FACTOR for s in model.success_criteria
    )


@pytest.mark.xfail(
    strict=True,
    reason="FORGE-569: '80 x 60 x 40 mm' compiles to one '<= 40 mm' criterion, not three",
)
def test_the_envelope_keeps_every_dimension() -> None:
    model = bracket.compile(requirements=())
    limits = {s.limit for s in model.success_criteria if s.unit == "mm"}
    assert set(bracket.ENVELOPE_MM) <= limits


def test_the_budget_is_kept_as_a_hard_constraint() -> None:
    model = bracket.compile()
    assert any(c.category is ConstraintCategory.COST and "200" in c.text for c in model.constraints)


def test_preferences_stay_separate_from_constraints() -> None:
    model = bracket.compile()
    assert any("aluminium" in p.lower() for p in model.preferences)
    assert not any("aluminium" in c.text.lower() for c in model.constraints if c.source == "stated")


@pytest.mark.xfail(
    strict=True,
    reason="FORGE-569: 'Deliver CAD and validation evidence' is classed as a regulatory constraint",
)
def test_a_deliverable_request_is_not_a_constraint() -> None:
    model = bracket.compile()
    assert not any(c.text.startswith("Deliver CAD") for c in model.constraints)


def test_unknown_loads_are_recorded_not_invented() -> None:
    model = bracket.compile(context=bracket.context(loads="unknown"), requirements=())
    assert any(u.id == "loads" for u in model.unknowns)
    # Nothing quantified about the load was made up: only the intent's own 10 kg.
    assert not any(c.source == "context" and "loads" in c.text for c in model.constraints)
    assert all(c.source in {"stated", "context"} for c in model.constraints)


def test_a_fully_stated_case_has_no_blocking_unknown() -> None:
    assert bracket.compile().ready_to_plan


# --- Structure: outcomes, dependencies, evidence ------------------------------


def test_outcomes_are_defined() -> None:
    model = bracket.compile()
    assert model.desired_outcomes
    proposal = bracket.generate()
    assert all(p.outcome for p in proposal.definition.phases)


def test_dependencies_form_a_valid_graph_in_engineering_order() -> None:
    proposal = bracket.generate()
    graph = build_graph(proposal.definition.phases)
    order = list(graph.order)
    # Requirements before analysis, analysis before geometry, geometry before validation.
    for before, after in (
        ("requirements", "feasibility"),
        ("feasibility", "design"),
        ("design", "simulation"),
    ):
        assert order.index(before) < order.index(after)
        assert before in graph.upstream(after)


def test_every_requirement_gets_validation_with_required_evidence() -> None:
    proposal = bracket.generate()
    assert proposal.valid, proposal.validation.violations
    required_at_gates = {
        d for p in proposal.definition.phases if p.gate for d in p.required_deliverables
    }
    for req in bracket.REQUIREMENTS:
        assert req["verified_by"] in required_at_gates, req["id"]


def test_generation_alone_requires_analysis_evidence_for_analysed_requirements() -> None:
    proposal = bracket.generate(operations=[])
    sim = next(p for p in proposal.definition.phases if p.id == "simulation")
    assert "simulation_result" in sim.required_deliverables


@pytest.mark.xfail(
    strict=True,
    reason="FORGE-571: generation does not inspect existing work, so recorded requirements "
    "are planned again instead of reused",
)
def test_valid_existing_work_is_reused() -> None:
    proposal = bracket.generate(operations=[])
    req = next(p for p in proposal.definition.phases if p.id == "requirements")
    assert req.condition, "a phase whose output already exists should be conditional"


# --- Capabilities and readiness -------------------------------------------------


def test_a_fully_provisioned_server_is_ready() -> None:
    proposal = bracket.generate()
    report = assess_capabilities(
        proposal.definition.phases, producers=DELIVERABLE_TOOLS, registered=bracket.all_tools()
    )
    assert report.status == "READY"
    assert all(n.coverage is Coverage.FULL for n in report.nodes)
    assert report.limits  # what was not checked is said, not assumed


def test_a_missing_solver_is_an_explicit_blocking_gap_with_alternatives() -> None:
    proposal = bracket.generate()
    no_fea = (
        bracket.all_tools()
        - {t for t in DELIVERABLE_TOOLS["simulation_result"] if t.startswith("calculix.")}
        - {"freecad.generate_mesh"}
    )
    report = assess_capabilities(
        proposal.definition.phases, producers=DELIVERABLE_TOOLS, registered=no_fea
    )
    sim_gaps = [g for g in report.gaps if g.phase_id == "simulation"]
    assert sim_gaps and all(g.capability == "simulation_result" for g in sim_gaps)
    # FORGE-572: the twin can still store a result, but nothing can produce one,
    # so the required analysis is blocked, with the missing tools and alternatives.
    gap = sim_gaps[0]
    assert gap.blocking
    assert "calculix.run_fea" in gap.missing_tools
    assert gap.workarounds
    assert report.status == "BLOCKED"


def test_an_unreachable_adapter_blocks_the_phase_that_needs_it() -> None:
    proposal = bracket.generate()
    tools = bracket.all_tools()
    reachable = tools - {t for t in tools if t.startswith(("calculix.", "freecad."))}
    report = assess_capabilities(
        proposal.definition.phases,
        producers=DELIVERABLE_TOOLS,
        registered=tools,
        reachable=reachable,
    )
    blocked = {g.phase_id for g in report.blocking_gaps}
    assert {"design", "simulation"} & blocked
    assert "intent" not in blocked and "requirements" not in blocked
