"""Compiling an intent into a structured model (FORGE-539)."""

from __future__ import annotations

from orchestrator.design_flow.context import (
    ClarifyingQuestion,
    FlowContext,
    ManufacturingContext,
    ManufacturingRoute,
)
from orchestrator.design_flow.intent import (
    ConstraintCategory,
    GoalType,
    WorkflowScope,
    compile_intent,
)


def test_a_calculation_that_serves_a_choice() -> None:
    # The spec's own example: the request is a calculation, the point is a
    # selection. Both are kept.
    model = compile_intent("Can you calculate what motor I need for a 12 kg robot?")
    assert model.goal_type is GoalType.ANALYSE
    assert model.immediate_request == "analyse motor"
    assert model.underlying_objective == "select motor"
    assert "selection criteria established" in model.desired_outcomes


def test_quantities_are_categorised_by_their_own_unit() -> None:
    model = compile_intent("Design a 5 kg robot under £1,000 with battery runtime over 30 min")
    by_unit = {c.unit: c.category for c in model.constraints}
    assert by_unit == {
        "kg": ConstraintCategory.MECHANICAL,
        "GBP": ConstraintCategory.COST,
        "min": ConstraintCategory.PERFORMANCE,
    }


def test_only_directed_quantities_become_success_criteria() -> None:
    model = compile_intent("Design a 5 kg robot under £1,000 with battery runtime over 30 min")
    assert {(c.operator, c.limit, c.unit) for c in model.success_criteria} == {
        ("<=", 1000.0, "GBP"),
        (">=", 30.0, "min"),
    }
    assert all(c.measurable for c in model.success_criteria)


def test_nothing_measurable_is_an_unknown_not_a_guess() -> None:
    model = compile_intent("Design a nice desk lamp")
    assert model.success_criteria == ()
    unknown = {u.id: u for u in model.unknowns}["success_criteria"]
    assert not unknown.blocking
    assert model.desired_outcomes[0] == "measurable success criteria agreed"
    assert model.confidence["success_criteria"] == "unknown"


def test_preferences_are_not_constraints() -> None:
    model = compile_intent("Design a shelf. Ideally modular. It must hold at least 20 kg")
    assert model.preferences == ("Ideally modular",)
    assert all("modular" not in c.text for c in model.constraints)


def test_keyword_constraints_without_numbers() -> None:
    model = compile_intent("Design an outdoor enclosure that is waterproof and UKCA compliant")
    categories = {c.category for c in model.constraints}
    assert ConstraintCategory.ENVIRONMENTAL in categories
    assert ConstraintCategory.REGULATORY in categories


def test_context_becomes_constraints_marked_as_context() -> None:
    ctx = FlowContext(
        manufacturing=ManufacturingContext(route=ManufacturingRoute.IN_HOUSE, processes=("cnc",)),
        budget="£200",
        loads_and_use="unknown",
    )
    model = compile_intent("Design a bracket", context=ctx)
    sources = {c.category: c.source for c in model.constraints}
    assert sources[ConstraintCategory.MANUFACTURING] == "context"
    assert sources[ConstraintCategory.COST] == "context"
    loads = {u.id: u for u in model.unknowns}["loads"]
    assert not loads.blocking and "verification stays" in loads.why


def test_required_questions_block_and_model_questions_do_not() -> None:
    questions = [
        ClarifyingQuestion(
            id="route", question="How will it be made?", why="w", answer_type="text"
        ),
        ClarifyingQuestion(
            id="colour", question="What colour?", why="w", answer_type="text", required=False
        ),
    ]
    model = compile_intent("Design a bracket", questions=questions)
    assert [u.id for u in model.blocking_unknowns] == ["route"]
    assert not model.ready_to_plan


def test_no_verb_defaults_to_design_with_low_confidence() -> None:
    model = compile_intent("Wall-mounted plywood bookshelf, 900 mm wide")
    assert model.goal_type is GoalType.DESIGN
    assert model.confidence["goal_type"] == "unknown"


def test_scope() -> None:
    assert compile_intent("Calculate the torque on a shaft").scope is WorkflowScope.ATOMIC
    assert (
        compile_intent("Design a drone with a frame, a PCB and a battery").scope
        is WorkflowScope.PROJECT
    )


def test_template_deliverables_come_first() -> None:
    model = compile_intent("Design a bracket", template_deliverables=["intent", "cad_model"])
    assert model.deliverables[:2] == ("intent", "cad_model")
    assert len(model.deliverables) == len(set(model.deliverables))


def test_as_dict_is_json() -> None:
    import json

    data = json.loads(json.dumps(compile_intent("Design a 2 kg bracket under 50 mm").as_dict()))
    assert data["primary_goal"]["type"] == "design"
