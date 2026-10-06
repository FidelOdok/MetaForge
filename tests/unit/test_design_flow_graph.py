"""The flow dependency graph (FORGE-539)."""

from __future__ import annotations

import pytest

from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.graph import (
    ConditionError,
    GraphError,
    build_graph,
    evaluate_condition,
    is_linear,
)
from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Phase


def _p(pid: str, depends_on: tuple[str, ...] | None = None, condition: str | None = None) -> Phase:
    return Phase(id=pid, title=pid, objective=pid, depends_on=depends_on, condition=condition)


class TestDefaultIsTheOldStraightLine:
    @pytest.mark.parametrize("flow_id", sorted(FLOWS))
    def test_every_template_is_linear(self, flow_id: str) -> None:
        # The property that keeps every approved flow running the sequential
        # loop it was approved under.
        graph = build_graph(FLOWS[flow_id].phases)
        assert is_linear(graph)
        assert graph.waves() == [[p.id] for p in FLOWS[flow_id].phases]

    def test_a_frozen_flow_builds_the_same_graph(self) -> None:
        flow = FLOWS["hardware_v1"]
        assert build_graph(freeze_flow(flow).phases).as_dict() == build_graph(flow.phases).as_dict()


class TestDependencies:
    def _diamond(self) -> list[Phase]:
        return [
            _p("req"),
            _p("mech", ("req",)),
            _p("elec", ("req",)),
            _p("verify", ("mech", "elec")),
        ]

    def test_independent_phases_share_a_wave(self) -> None:
        graph = build_graph(self._diamond())
        assert graph.waves() == [["req"], ["mech", "elec"], ["verify"]]
        assert not is_linear(graph)

    def test_ready_waits_for_every_parent(self) -> None:
        graph = build_graph(self._diamond())
        assert graph.ready(done=[]) == ["req"]
        assert graph.ready(done=["req"]) == ["mech", "elec"]
        assert graph.ready(done=["req", "mech"]) == ["elec"]
        assert graph.ready(done=["req", "mech"], running=["elec"]) == []
        assert graph.ready(done=["req", "mech", "elec"]) == ["verify"]

    def test_a_skipped_parent_releases_its_child(self) -> None:
        graph = build_graph(self._diamond())
        assert graph.ready(done=["req", "mech"], skipped=["elec"]) == ["verify"]

    def test_downstream_is_only_what_depends(self) -> None:
        # Selective repair: reworking mech re-runs mech and verify, not elec.
        graph = build_graph(self._diamond())
        assert graph.downstream("mech") == ["mech", "verify"]
        assert graph.downstream("req") == ["req", "mech", "elec", "verify"]
        assert graph.upstream("verify") == ["req", "mech", "elec"]

    def test_downstream_of_a_line_is_every_later_phase(self) -> None:
        # Which is exactly the old rework behaviour, so linear flows keep it.
        graph = build_graph(FLOWS["mech_v1"].phases)
        ids = [p.id for p in FLOWS["mech_v1"].phases]
        assert graph.downstream("design") == ids[ids.index("design") :]

    def test_an_explicit_empty_tuple_is_a_root(self) -> None:
        graph = build_graph([_p("a"), _p("b", ())])
        assert graph.roots() == ["a", "b"]
        assert graph.waves() == [["a", "b"]]


class TestRefusals:
    def test_unknown_dependency(self) -> None:
        with pytest.raises(GraphError, match="unknown phase"):
            build_graph([_p("a"), _p("b", ("ghost",))])

    def test_self_dependency(self) -> None:
        with pytest.raises(GraphError, match="itself"):
            build_graph([_p("a", ("a",))])

    def test_cycle(self) -> None:
        with pytest.raises(GraphError, match="cycle"):
            build_graph([_p("a", ("b",)), _p("b", ("a",))])

    def test_duplicate_ids(self) -> None:
        with pytest.raises(GraphError, match="duplicate"):
            build_graph([_p("a"), _p("a")])

    def test_unknown_phase_in_query(self) -> None:
        with pytest.raises(GraphError, match="unknown phase"):
            build_graph([_p("a")]).downstream("zzz")


class TestConditions:
    facts = {"route": "undecided", "target_maturity": "concept", "loads_known": "true"}

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (None, True),
            ("", True),
            ("route == undecided", True),
            ("route != undecided", False),
            ("route in [in_house, vendor]", False),
            ("route not in [in_house, vendor]", True),
            ("route == undecided and loads_known == true", True),
            ("route == undecided and loads_known == false", False),
            # An unstated fact is never a match, in either direction.
            ("budget == low", False),
            ("budget != low", False),
        ],
    )
    def test_evaluate(self, text: str | None, expected: bool) -> None:
        assert evaluate_condition(text, self.facts) is expected

    @pytest.mark.parametrize(
        "text",
        ["route", "route = x", "__import__('os')", "route == [a, b]", "route in []", "1 == 1"],
    )
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(ConditionError):
            evaluate_condition(text, self.facts)

    def test_a_bad_condition_fails_the_graph(self) -> None:
        with pytest.raises(ConditionError):
            build_graph([_p("a", condition="route = x")])

    def test_a_conditional_flow_is_not_linear(self) -> None:
        graph = build_graph([_p("a"), _p("b", condition="route == undecided")])
        assert not is_linear(graph)
        assert graph.conditions == {"b": "route == undecided"}


class TestFrozenFields:
    def test_graph_fields_change_the_hash(self) -> None:
        base = FlowDefinition(id="f", name="f", phases=(_p("a"), _p("b")))
        graphed = FlowDefinition(id="f", name="f", phases=(_p("a"), _p("b", ())))
        assert freeze_flow(base).content_hash != freeze_flow(graphed).content_hash

    def test_facts_are_hashed_when_present(self) -> None:
        flow = FlowDefinition(id="f", name="f", phases=(_p("a"),))
        plain = freeze_flow(flow)
        with_facts = freeze_flow(flow, facts={"route": "vendor"})
        assert plain.content_hash != with_facts.content_hash
        with_facts.verify()
        with_facts.facts["route"] = "in_house"
        with pytest.raises(ValueError, match="does not match"):
            with_facts.verify()


class TestInvariants:
    """Graph rules in the flow validator (FORGE-539)."""

    def _gated(self, pid: str, **kw: object) -> Phase:
        from orchestrator.design_flow.spec import Gate

        return Phase(
            id=pid,
            title=pid,
            objective=pid,
            gate=Gate(name=kw.pop("gate_name", f"{pid} review")),  # type: ignore[arg-type]
            **kw,  # type: ignore[arg-type]
        )

    def _rules(self, phases: list[Phase]) -> set[str]:
        from orchestrator.design_flow.invariants import validate_flow

        return {v.rule for v in validate_flow(FlowDefinition("f", "f", tuple(phases))).violations}

    def test_a_cycle_is_a_violation(self) -> None:
        assert "graph-is-valid" in self._rules([_p("a", ("b",)), _p("b", ("a",))])

    def test_duplicates_are_reported_once(self) -> None:
        rules = self._rules([_p("a"), _p("a")])
        assert "unique-phase-ids" in rules and "graph-is-valid" not in rules

    def test_producible_follows_dependencies_not_list_order(self) -> None:
        # 'verify' needs a cad_model. 'design' makes one but is not upstream
        # of 'verify', so in a graph the gate could run before it exists.
        phases = [
            self._gated("req", expected_artifacts=("prd",), required_deliverables=("prd",)),
            self._gated(
                "design",
                depends_on=("req",),
                expected_artifacts=("cad_model",),
                required_deliverables=("cad_model",),
            ),
            self._gated(
                "verify",
                depends_on=("req",),
                expected_artifacts=("test_result",),
                required_deliverables=("cad_model",),
                gate_name="Release sign-off",
            ),
        ]
        assert "deliverable-is-producible" in self._rules(phases)
        fixed = [
            *phases[:2],
            self._gated(
                "verify",
                depends_on=("design",),
                expected_artifacts=("test_result",),
                required_deliverables=("cad_model",),
                gate_name="Release sign-off",
            ),
        ]
        assert "deliverable-is-producible" not in self._rules(fixed)

    def test_a_conditional_producer_does_not_count(self) -> None:
        phases = [
            self._gated(
                "design",
                expected_artifacts=("cad_model",),
                required_deliverables=("cad_model",),
                condition="route == in_house",
            ),
            self._gated(
                "verify",
                expected_artifacts=("test_result",),
                required_deliverables=("cad_model",),
                gate_name="Release sign-off",
            ),
        ]
        assert "deliverable-is-producible" in self._rules(phases)

    def test_a_conditional_release_gate_is_not_a_release_gate(self) -> None:
        phases = [
            self._gated(
                "a",
                expected_artifacts=("prd",),
                required_deliverables=("prd",),
                gate_name="Release sign-off",
                condition="route == vendor",
            ),
        ]
        assert "release-gate-exists" in self._rules(phases)
