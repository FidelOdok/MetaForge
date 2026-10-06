"""Tailoring a flow into a graph (FORGE-539): set_dependencies, set_condition,
set_outcome, and drop_phase / add_deliverable keeping graph fields intact."""

from __future__ import annotations

import pytest

from orchestrator.design_flow.generator import (
    Operation,
    OperationKind,
    TailoringError,
    apply_operations,
    build_proposal,
    parse_caller_operations,
)
from orchestrator.design_flow.graph import build_graph
from orchestrator.design_flow.spec import FlowDefinition, Phase, get_flow


def _op(kind: OperationKind, phase: str, value: object = None) -> Operation:
    return Operation(kind=kind, phase_id=phase, rationale="because", value=value)


def test_hardware_v1_tailored_into_parallel_disciplines() -> None:
    # Electronics and firmware no longer wait for mechanical design: both
    # need only the concept. Verification needs all three.
    base = get_flow("hardware_v1")
    ops = [
        _op(OperationKind.SET_DEPENDENCIES, "electronics", ["concept_selection"]),
        _op(OperationKind.SET_DEPENDENCIES, "firmware", ["electronics"]),
        _op(OperationKind.SET_DEPENDENCIES, "simulation", ["design", "electronics", "firmware"]),
        _op(OperationKind.SET_OUTCOME, "electronics", "schematic and BOM reviewed"),
    ]
    proposal = build_proposal(base, base_version="1", operations=ops)
    assert proposal.valid, proposal.validation.violations
    graph = build_graph(proposal.definition.phases)
    wave = next(w for w in graph.waves() if "design" in w)
    assert "electronics" in wave, "design and electronics run in the same wave"
    assert graph.downstream("design") == ["design", "simulation", "manufacturing"]
    electronics = next(p for p in proposal.definition.phases if p.id == "electronics")
    assert electronics.outcome == "schematic and BOM reviewed"
    assert any("depends on" in line for line in proposal.diff())


def test_set_condition_is_parsed_and_applied() -> None:
    base = get_flow("hardware_v1")
    ops = [_op(OperationKind.SET_CONDITION, "firmware", "route != vendor")]
    tailored, applied = apply_operations(base, ops)
    assert applied and next(p for p in tailored.phases if p.id == "firmware").condition
    bad = [_op(OperationKind.SET_CONDITION, "firmware", "eval(x)")]
    assert apply_operations(base, bad)[1] == []


def test_add_deliverable_keeps_graph_fields() -> None:
    # The bug this guards: a phase rebuilt field by field lost depends_on.
    base = FlowDefinition(
        "g",
        "g",
        (
            Phase(id="a", title="a", objective="a"),
            Phase(
                id="b",
                title="b",
                objective="b",
                depends_on=("a",),
                outcome="b done",
                condition="route == in_house",
            ),
        ),
    )
    tailored, _ = apply_operations(base, [_op(OperationKind.ADD_DELIVERABLE, "b", "test_plan")])
    b = tailored.phases[1]
    assert (b.depends_on, b.outcome, b.condition) == (("a",), "b done", "route == in_house")
    assert "test_plan" in b.required_deliverables


def test_dropping_a_dependency_rewires_its_dependents() -> None:
    base = FlowDefinition(
        "g",
        "g",
        (
            Phase(id="req", title="r", objective="r"),
            Phase(id="sim", title="s", objective="s", depends_on=("req",)),
            Phase(id="verify", title="v", objective="v", depends_on=("sim",)),
        ),
    )
    tailored, _ = apply_operations(base, [_op(OperationKind.DROP_PHASE, "sim")])
    verify = tailored.phases[-1]
    assert verify.depends_on == ("req",)
    build_graph(tailored.phases)  # no dangling reference


def test_route_selection_is_a_dependency_of_the_geometry_phase_in_a_graph() -> None:
    from orchestrator.design_flow.context import (
        FlowContext,
        ManufacturingContext,
        ManufacturingRoute,
    )

    base = FlowDefinition(
        "g",
        "g",
        (
            Phase(id="req", title="r", objective="r"),
            Phase(
                id="design",
                title="d",
                objective="d",
                depends_on=("req",),
                expected_artifacts=("cad_model",),
            ),
        ),
    )
    ctx = FlowContext(manufacturing=ManufacturingContext(route=ManufacturingRoute.UNDECIDED))
    proposal = build_proposal(base, base_version="1", operations=[], context=ctx)
    design = next(p for p in proposal.definition.phases if p.id == "design")
    assert "route_selection" in (design.depends_on or ())


class TestCallerValidation:
    base = get_flow("mech_v1")

    def test_unknown_dependency_is_refused(self) -> None:
        with pytest.raises(TailoringError, match="unknown or self"):
            parse_caller_operations(
                [
                    {
                        "op": "set_dependencies",
                        "phase": "design",
                        "value": ["ghost"],
                        "rationale": "r",
                    }
                ],
                self.base,
            )

    def test_self_dependency_is_refused(self) -> None:
        with pytest.raises(TailoringError, match="unknown or self"):
            parse_caller_operations(
                [
                    {
                        "op": "set_dependencies",
                        "phase": "design",
                        "value": ["design"],
                        "rationale": "r",
                    }
                ],
                self.base,
            )

    def test_bad_condition_is_refused_with_the_grammar(self) -> None:
        with pytest.raises(TailoringError, match="fact == value"):
            parse_caller_operations(
                [{"op": "set_condition", "phase": "design", "value": "x = 1", "rationale": "r"}],
                self.base,
            )

    def test_empty_outcome_is_refused(self) -> None:
        with pytest.raises(TailoringError, match="set_outcome"):
            parse_caller_operations(
                [{"op": "set_outcome", "phase": "design", "value": " ", "rationale": "r"}],
                self.base,
            )

    def test_a_cycle_is_refused_by_the_invariants(self) -> None:
        ops = parse_caller_operations(
            [
                {"op": "set_dependencies", "phase": "needs", "value": ["design"], "rationale": "r"},
            ],
            self.base,
        )
        proposal = build_proposal(self.base, base_version="1", operations=ops)
        assert not proposal.valid
        assert "graph-is-valid" in {v.rule for v in proposal.validation.violations}

    def test_a_conditional_release_gate_is_refused(self) -> None:
        # Making every gated phase conditional leaves no guaranteed sign-off.
        ops = [
            _op(OperationKind.SET_CONDITION, p.id, "route == vendor")
            for p in self.base.phases
            if p.gate
        ]
        proposal = build_proposal(self.base, base_version="1", operations=ops)
        assert "release-gate-exists" in {v.rule for v in proposal.validation.violations}
