"""Unit tests for ObjectiveEngine (FORGE-58)."""

import pytest

from twin_core.consistency.objectives import (
    Candidate,
    Objective,
    ObjectiveDirection,
    ObjectiveEngine,
    objective_from_entity,
)
from twin_core.models.engineering_entity import EngineeringEntity


def _doc_objectives() -> list[Objective]:
    """spec section 19's own worked example."""
    return [
        Objective(id="mass", metric="mass", direction=ObjectiveDirection.MINIMIZE, weight=0.4),
        Objective(
            id="runtime", metric="runtime", direction=ObjectiveDirection.MAXIMIZE, weight=0.35
        ),
        Objective(id="cost", metric="cost", direction=ObjectiveDirection.MINIMIZE, weight=0.25),
    ]


def _candidates() -> list[Candidate]:
    return [
        Candidate(id="A", metrics={"mass": 3.0, "runtime": 40.0, "cost": 900.0}),
        Candidate(id="B", metrics={"mass": 2.0, "runtime": 30.0, "cost": 700.0}),
        Candidate(id="C", metrics={"mass": 4.0, "runtime": 60.0, "cost": 1000.0}),
    ]


class TestObjectiveFromEntity:
    def test_reads_metric_and_direction(self):
        e = EngineeringEntity(
            entity_type="objective",
            statement="minimize mass",
            metadata={"metric": "mass", "direction": "minimize", "weight": 0.4, "priority": 1},
        )
        obj = objective_from_entity(e)
        assert obj.metric == "mass"
        assert obj.direction == ObjectiveDirection.MINIMIZE
        assert obj.weight == 0.4
        assert obj.priority == 1

    def test_rejects_non_objective_entity(self):
        e = EngineeringEntity(entity_type="intent", statement="build a robot")
        with pytest.raises(ValueError, match="not an objective"):
            objective_from_entity(e)

    def test_requires_metric_and_direction_in_metadata(self):
        e = EngineeringEntity(entity_type="objective", statement="x", metadata={})
        with pytest.raises(ValueError, match="missing metric/direction"):
            objective_from_entity(e)


class TestObjectiveEngineConstruction:
    def test_requires_at_least_one_objective(self):
        with pytest.raises(ValueError, match="at least one objective"):
            ObjectiveEngine([])


class TestWeightedScore:
    def test_requires_every_objective_to_have_a_weight(self):
        objs = [Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE)]
        with pytest.raises(ValueError, match="requires every Objective to have a weight"):
            ObjectiveEngine(objs).weighted_score(_candidates())

    def test_ranks_best_candidate_first(self):
        result = ObjectiveEngine(_doc_objectives()).weighted_score(_candidates())
        assert result.method == "weighted_score"
        # B has the lowest mass, lowest cost, but also lowest runtime --
        # not obviously best; just assert the engine produced a full,
        # consistently-ordered ranking rather than asserting a winner by eye.
        assert {s.candidate_id for s in result.ranked} == {"A", "B", "C"}
        scores = [s.score for s in result.ranked]
        assert scores == sorted(scores, reverse=True)

    def test_all_tied_metric_is_neutral_not_a_fake_winner(self):
        objs = [Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE, weight=1.0)]
        candidates = [
            Candidate(id="A", metrics={"mass": 5.0}),
            Candidate(id="B", metrics={"mass": 5.0}),
        ]
        result = ObjectiveEngine(objs).weighted_score(candidates)
        assert result.ranked[0].score == pytest.approx(result.ranked[1].score)
        assert result.ranked[0].score == pytest.approx(0.5)

    def test_excluded_candidates_are_not_ranked(self):
        result = ObjectiveEngine(_doc_objectives()).weighted_score(_candidates(), exclude_ids=["C"])
        assert {s.candidate_id for s in result.ranked} == {"A", "B"}
        assert result.excluded == ["C"]

    def test_no_candidate_has_the_metric_raises(self):
        objs = [Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE, weight=1.0)]
        candidates = [Candidate(id="A", metrics={})]
        with pytest.raises(ValueError, match="no candidate has a value for"):
            ObjectiveEngine(objs).weighted_score(candidates)

    def test_one_candidate_missing_the_metric_raises(self):
        """Other candidates DO have the metric (so bounds compute fine) --
        this exercises the per-candidate check, not the all-missing one."""
        objs = [Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE, weight=1.0)]
        candidates = [
            Candidate(id="A", metrics={"mass": 1.0}),
            Candidate(id="B", metrics={}),
        ]
        with pytest.raises(ValueError, match="no value for"):
            ObjectiveEngine(objs).weighted_score(candidates)

    def test_target_direction_prefers_closest_value(self):
        objs = [
            Objective(
                id="width",
                metric="width",
                direction=ObjectiveDirection.TARGET,
                target=450.0,
                weight=1.0,
            )
        ]
        candidates = [
            Candidate(id="exact", metrics={"width": 450.0}),
            Candidate(id="close", metrics={"width": 440.0}),
            Candidate(id="far", metrics={"width": 300.0}),
        ]
        result = ObjectiveEngine(objs).weighted_score(candidates)
        assert [s.candidate_id for s in result.ranked] == ["exact", "close", "far"]

    def test_target_direction_without_target_value_raises(self):
        objs = [Objective(id="w", metric="width", direction=ObjectiveDirection.TARGET, weight=1.0)]
        candidates = [Candidate(id="A", metrics={"width": 100.0})]
        with pytest.raises(ValueError, match="requires Objective.target"):
            ObjectiveEngine(objs).weighted_score(candidates)


class TestParetoFrontier:
    def test_dominated_candidate_is_excluded(self):
        objs = [
            Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE),
            Objective(id="c", metric="cost", direction=ObjectiveDirection.MINIMIZE),
        ]
        candidates = [
            Candidate(id="best", metrics={"mass": 1.0, "cost": 100.0}),
            Candidate(id="dominated", metrics={"mass": 2.0, "cost": 200.0}),  # worse on both
        ]
        result = ObjectiveEngine(objs).pareto_frontier(candidates)
        assert result.pareto_optimal == ["best"]

    def test_tradeoff_candidates_are_all_optimal(self):
        objs = [
            Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE),
            Objective(id="r", metric="runtime", direction=ObjectiveDirection.MAXIMIZE),
        ]
        candidates = [
            Candidate(id="light", metrics={"mass": 1.0, "runtime": 20.0}),
            Candidate(id="long_life", metrics={"mass": 3.0, "runtime": 60.0}),
        ]
        result = ObjectiveEngine(objs).pareto_frontier(candidates)
        assert set(result.pareto_optimal) == {"light", "long_life"}

    def test_weight_is_not_required_for_pareto(self):
        objs = [Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE)]
        candidates = [Candidate(id="A", metrics={"mass": 1.0})]
        result = ObjectiveEngine(objs).pareto_frontier(candidates)  # must not raise
        assert result.pareto_optimal == ["A"]


class TestLexicographic:
    def test_higher_priority_objective_decides_first(self):
        objs = [
            Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE, priority=0),
            Objective(id="c", metric="cost", direction=ObjectiveDirection.MINIMIZE, priority=1),
        ]
        candidates = [
            Candidate(id="lighter_pricier", metrics={"mass": 1.0, "cost": 1000.0}),
            Candidate(id="heavier_cheaper", metrics={"mass": 2.0, "cost": 10.0}),
        ]
        result = ObjectiveEngine(objs).lexicographic(candidates)
        assert result.ranked[0].candidate_id == "lighter_pricier"

    def test_tie_on_top_priority_falls_through_to_next(self):
        objs = [
            Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE, priority=0),
            Objective(id="c", metric="cost", direction=ObjectiveDirection.MINIMIZE, priority=1),
        ]
        candidates = [
            Candidate(id="pricier", metrics={"mass": 1.0, "cost": 1000.0}),
            Candidate(id="cheaper", metrics={"mass": 1.0, "cost": 10.0}),
        ]
        result = ObjectiveEngine(objs).lexicographic(candidates)
        assert [s.candidate_id for s in result.ranked] == ["cheaper", "pricier"]

    def test_priority_field_is_independent_of_declaration_order(self):
        objs = [
            Objective(id="c", metric="cost", direction=ObjectiveDirection.MINIMIZE, priority=1),
            Objective(id="m", metric="mass", direction=ObjectiveDirection.MINIMIZE, priority=0),
        ]
        candidates = [
            Candidate(id="lighter_pricier", metrics={"mass": 1.0, "cost": 1000.0}),
            Candidate(id="heavier_cheaper", metrics={"mass": 2.0, "cost": 10.0}),
        ]
        result = ObjectiveEngine(objs).lexicographic(candidates)
        assert result.ranked[0].candidate_id == "lighter_pricier"


class TestMethodIsRecorded:
    def test_every_result_records_method_and_rationale(self):
        engine = ObjectiveEngine(_doc_objectives())
        for result in (
            engine.weighted_score(_candidates()),
            engine.pareto_frontier(_candidates()),
            engine.lexicographic(_candidates()),
        ):
            assert result.method
            assert result.rationale
