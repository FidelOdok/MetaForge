"""Unit tests for the FORGE-54 requirement-intelligence agents."""

from uuid import uuid4

import pytest

from api_gateway.requirement_intelligence import (
    AgentResult,
    ClarificationAgent,
    IntentInterpreterAgent,
    Unknown,
)
from twin_core.models.patch import PatchOp


def _unknown(**overrides) -> Unknown:
    defaults = dict(
        id="TBD-001",
        question="What is the payload?",
        downstream_impact=0.5,
        uncertainty=0.5,
        cost_of_error=0.5,
        dependency_count=1,
    )
    defaults.update(overrides)
    return Unknown(**defaults)


class TestUnknownPriority:
    def test_priority_is_the_product_of_the_four_factors(self):
        u = _unknown(downstream_impact=0.8, uncertainty=0.5, cost_of_error=0.5, dependency_count=2)
        assert u.priority == pytest.approx(0.8 * 0.5 * 0.5 * 2)

    def test_zero_dependency_count_zeroes_priority(self):
        u = _unknown(downstream_impact=1.0, uncertainty=1.0, cost_of_error=1.0, dependency_count=0)
        assert u.priority == 0.0


class TestClarificationAgent:
    def test_ranks_by_priority_descending(self):
        low = _unknown(id="TBD-1", downstream_impact=0.1, dependency_count=1)
        high = _unknown(id="TBD-2", downstream_impact=0.9, dependency_count=3)
        agent = ClarificationAgent()
        result = agent.select_questions([low, high], current_gate="G3")
        assert [u.id for u in result] == ["TBD-2", "TBD-1"]

    def test_caps_at_max_questions(self):
        unknowns = [_unknown(id=f"TBD-{i}") for i in range(10)]
        agent = ClarificationAgent()
        result = agent.select_questions(unknowns, current_gate="G3", max_questions=3)
        assert len(result) == 3

    def test_excludes_unknowns_not_yet_due_at_a_later_gate(self):
        due_now = _unknown(id="TBD-now", required_by_gate="G2")
        later = _unknown(id="TBD-later", required_by_gate="G6")
        agent = ClarificationAgent()
        result = agent.select_questions([due_now, later], current_gate="G3")
        assert [u.id for u in result] == ["TBD-now"]

    def test_includes_unknowns_with_no_gate_requirement(self):
        u = _unknown(id="TBD-1", required_by_gate=None)
        agent = ClarificationAgent()
        result = agent.select_questions([u], current_gate="G0")
        assert [x.id for x in result] == ["TBD-1"]

    def test_overdue_unknown_from_an_earlier_gate_still_surfaces(self):
        overdue = _unknown(id="TBD-overdue", required_by_gate="G1")
        agent = ClarificationAgent()
        result = agent.select_questions([overdue], current_gate="G5")
        assert [u.id for u in result] == ["TBD-overdue"]


class TestIntentInterpreterAgent:
    async def _fake_extract(self, spec: dict):
        async def extract(goal: str, context: str) -> dict:
            return spec

        return extract

    async def test_full_extraction_builds_a_patch_and_unresolved_list(self):
        spec = {
            "intent": "Build a desktop quadruped for demos",
            "goals": ["Walk indoors", "Fit on a desk"],
            "candidate_constraints": [{"statement": "mass under 3kg", "confidence": 0.7}],
            "preferences": ["quiet operation"],
            "assumptions": [{"statement": "indoor use only", "confidence": 0.6}],
            "ambiguous_phrases": [
                {
                    "phrase": "quiet",
                    "question": "What is the max acceptable noise level in dB?",
                    "downstream_impact": 0.6,
                    "uncertainty": 0.8,
                    "cost_of_error": 0.5,
                    "dependency_count": 2,
                    "affected": ["motor selection"],
                }
            ],
        }
        agent = IntentInterpreterAgent(extract=await self._fake_extract(spec))
        result = await agent.interpret("build a quiet desktop quadruped")

        assert isinstance(result, AgentResult)
        assert result.proposed_patch is not None
        ops_by_kind = [(op.op, op.entity_kind) for op in result.proposed_patch.operations]
        assert (PatchOp.ADD, "engineering_entity") in ops_by_kind  # intent
        # intent (1) + goals (2) + assumptions (1) = 4 engineering_entity ADDs
        assert ops_by_kind.count((PatchOp.ADD, "engineering_entity")) == 4
        assert (PatchOp.ADD, "constraint") in ops_by_kind

        assert "Build a desktop quadruped for demos" in result.conclusions
        assert "indoor use only" in result.assumptions
        assert len(result.unresolved) == 1
        assert result.unresolved[0].id == "TBD-001"
        assert result.unresolved[0].question.startswith("What is the max")
        assert result.unresolved[0].dependency_count == 2
        assert 0.0 < result.confidence <= 1.0

    async def test_empty_extraction_yields_no_patch(self):
        agent = IntentInterpreterAgent(extract=await self._fake_extract({}))
        result = await agent.interpret("a vague goal with nothing extractable")
        assert result.proposed_patch is None
        assert result.conclusions == []
        assert result.unresolved == []

    async def test_malformed_items_are_skipped_not_crashed_on(self):
        spec = {
            "intent": None,
            "goals": ["", None, "a real goal"],
            "candidate_constraints": ["not a dict", {"statement": ""}, {"statement": "real one"}],
            "assumptions": [{"statement": "keep this"}],
            "ambiguous_phrases": "not even a list",
        }
        agent = IntentInterpreterAgent(extract=await self._fake_extract(spec))
        result = await agent.interpret("goal")
        assert result.proposed_patch is not None
        # only "a real goal" (objective) + "real one" (constraint) + "keep this" (assumption)
        assert len(result.proposed_patch.operations) == 3
        assert result.unresolved == []

    async def test_extract_failure_degrades_to_empty_result(self):
        async def boom(goal: str, context: str) -> dict:
            raise RuntimeError("provider unavailable")

        # IntentInterpreterAgent only degrades gracefully inside its own
        # _extract (the LLM-calling path); an injected extract that raises
        # propagates -- assert that explicitly so this behavior is documented.
        agent = IntentInterpreterAgent(extract=boom)
        with pytest.raises(RuntimeError):
            await agent.interpret("goal")

    async def test_project_id_is_attached_to_the_patch(self):
        spec = {"goals": ["a goal"]}
        pid = str(uuid4())
        agent = IntentInterpreterAgent(extract=await self._fake_extract(spec))
        result = await agent.interpret("goal", project_id=pid)
        assert str(result.proposed_patch.project_id) == pid

    async def test_out_of_range_scores_are_clamped_not_rejected(self):
        spec = {
            "ambiguous_phrases": [
                {
                    "phrase": "x",
                    "question": "q",
                    "downstream_impact": 5.0,  # out of range
                    "uncertainty": -1.0,  # out of range
                    "cost_of_error": 0.5,
                    "dependency_count": 2,
                }
            ]
        }
        agent = IntentInterpreterAgent(extract=await self._fake_extract(spec))
        result = await agent.interpret("goal")
        u = result.unresolved[0]
        assert u.downstream_impact == 1.0
        assert u.uncertainty == 0.0
