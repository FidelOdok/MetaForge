"""Unit tests for the declarative PolicyEngine (FORGE-52)."""

import pytest

from twin_core.policy import EngineeringPolicyViolation, Policy, PolicyEngine


@pytest.fixture
def engine():
    return PolicyEngine()


class TestDefaultAllow:
    async def test_action_with_no_policy_is_allowed(self, engine):
        result = await engine.evaluate("cad.create_detailed_model", actor={}, state={})
        assert result.allowed is True
        assert result.applied_policy_ids == []

    async def test_policy_whose_when_does_not_match_does_not_apply(self, engine):
        engine.register(
            Policy(
                id="POL-BASELINED-REQ-CHANGE",
                action="requirement.revise",
                when={"requirement.status": "baselined"},
                require={"human_approval": True},
            )
        )
        result = await engine.evaluate(
            "requirement.revise",
            actor={},
            state={"requirement": {"status": "proposed"}},
        )
        assert result.allowed is True
        assert result.applied_policy_ids == []


class TestBlocking:
    async def test_missing_requirement_blocks(self, engine):
        engine.register(
            Policy(
                id="POL-DETAILED-CAD",
                action="cad.create_detailed_model",
                require={"gate.G2": "passed", "blocking_tbd_count": 0},
            )
        )
        result = await engine.evaluate(
            "cad.create_detailed_model",
            actor={},
            state={"gate": {"G2": "passed"}, "blocking_tbd_count": 3},
        )
        assert result.allowed is False
        assert result.applied_policy_ids == ["POL-DETAILED-CAD"]
        assert len(result.failures) == 1
        assert result.failures[0].key == "blocking_tbd_count"
        assert result.failures[0].expected == 0
        assert result.failures[0].actual == 3
        assert result.failures[0].found is True

    async def test_entirely_missing_key_is_reported_as_not_found(self, engine):
        engine.register(
            Policy(id="POL-1", action="cad.create_detailed_model", require={"gate.G6": "passed"})
        )
        result = await engine.evaluate("cad.create_detailed_model", actor={}, state={})
        assert result.allowed is False
        assert result.failures[0].found is False
        assert result.failures[0].actual is None

    async def test_all_requirements_satisfied_allows(self, engine):
        engine.register(
            Policy(
                id="POL-DETAILED-CAD",
                action="cad.create_detailed_model",
                require={"gate.G2": "passed", "gate.G3": "passed", "blocking_tbd_count": 0},
            )
        )
        result = await engine.evaluate(
            "cad.create_detailed_model",
            actor={},
            state={"gate": {"G2": "passed", "G3": "passed"}, "blocking_tbd_count": 0},
        )
        assert result.allowed is True
        assert result.failures == []

    async def test_when_matching_policy_gates_on_require(self, engine):
        engine.register(
            Policy(
                id="POL-BASELINED-REQ-CHANGE",
                action="requirement.revise",
                when={"requirement.status": "baselined"},
                require={"impact_analysis": "complete", "human_approval": True},
            )
        )
        result = await engine.evaluate(
            "requirement.revise",
            actor={},
            state={"requirement": {"status": "baselined"}, "human_approval": False},
        )
        assert result.allowed is False
        assert result.applied_policy_ids == ["POL-BASELINED-REQ-CHANGE"]
        failed_keys = {f.key for f in result.failures}
        assert failed_keys == {"impact_analysis", "human_approval"}


class TestActorContext:
    async def test_require_can_reference_actor(self, engine):
        engine.register(
            Policy(id="POL-HUMAN-ONLY", action="release.approve", require={"actor.type": "human"})
        )
        result = await engine.evaluate("release.approve", actor={"type": "agent"}, state={})
        assert result.allowed is False
        assert result.failures[0].key == "actor.type"

    async def test_actor_wins_over_a_state_provided_actor_key(self, engine):
        engine.register(
            Policy(id="POL-1", action="release.approve", require={"actor.type": "human"})
        )
        result = await engine.evaluate(
            "release.approve",
            actor={"type": "human"},
            state={"actor": {"type": "agent"}},  # must NOT win
        )
        assert result.allowed is True


class TestMultiplePolicies:
    async def test_failures_from_all_applicable_policies_are_collected(self, engine):
        engine.register(Policy(id="POL-A", action="cad.create_detailed_model", require={"a": 1}))
        engine.register(Policy(id="POL-B", action="cad.create_detailed_model", require={"b": 2}))
        result = await engine.evaluate("cad.create_detailed_model", actor={}, state={"a": 1})
        assert result.allowed is False
        assert {f.policy_id for f in result.failures} == {"POL-B"}
        assert set(result.applied_policy_ids) == {"POL-A", "POL-B"}


class TestEvaluatePreconditions:
    async def test_raises_engineering_policy_violation_when_blocked(self, engine):
        engine.register(
            Policy(id="POL-1", action="cad.create_detailed_model", require={"gate.G2": "passed"})
        )
        with pytest.raises(EngineeringPolicyViolation) as exc:
            await engine.evaluate_preconditions("cad.create_detailed_model", actor={}, state={})
        assert exc.value.result.action == "cad.create_detailed_model"
        assert "POL-1" in str(exc.value)
        assert "gate.G2" in str(exc.value)

    async def test_returns_result_when_allowed(self, engine):
        result = await engine.evaluate_preconditions(
            "cad.create_detailed_model", actor={}, state={}
        )
        assert result.allowed is True
