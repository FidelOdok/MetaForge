"""Unit tests for RequirementAuthorAgent (FORGE-55)."""

from uuid import uuid4

import pytest

from api_gateway.requirement_intelligence import AgentResult, RequirementAuthorAgent
from twin_core.models.patch import PatchOp


async def _fake_extract(spec: dict):
    async def extract(source: str, context: str) -> dict:
        return spec

    return extract


class TestRequirementAuthorAgent:
    async def test_generates_a_patch_with_one_add_per_requirement(self):
        spec = {
            "requirements": [
                {
                    "statement": "The system shall weigh less than 3 kg.",
                    "rationale": "Desk-portability from the source intent.",
                    "verification_method": "test",
                    "confidence": 0.8,
                },
                {
                    "statement": "The system shall operate below 45 dB.",
                    "rationale": "Quiet operation from the source intent.",
                    "verification_method": "test",
                    "confidence": 0.7,
                },
            ]
        }
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("desktop quadruped, light and quiet")

        assert isinstance(result, AgentResult)
        assert result.proposed_patch is not None
        adds = [op for op in result.proposed_patch.operations if op.op == PatchOp.ADD]
        assert len(adds) == 2
        assert all(op.entity_kind == "constraint" for op in adds)
        assert {op.entity["message"] for op in adds} == {
            "The system shall weigh less than 3 kg.",
            "The system shall operate below 45 dB.",
        }
        assert result.conclusions == [
            "The system shall weigh less than 3 kg.",
            "The system shall operate below 45 dB.",
        ]
        assert 0.0 < result.confidence <= 1.0

    async def test_each_requirement_carries_rationale_and_verification_method(self):
        spec = {
            "requirements": [
                {
                    "statement": "The system shall weigh less than 3 kg.",
                    "rationale": "Desk-portability.",
                    "verification_method": "test",
                    "confidence": 0.9,
                }
            ]
        }
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal")
        op = result.proposed_patch.operations[0]
        assert op.entity["metadata"]["rationale"] == "Desk-portability."
        assert op.entity["metadata"]["verification_method"] == "test"

    async def test_each_requirement_is_linted_before_being_proposed(self):
        spec = {
            "requirements": [
                {"statement": "The system should preferably be light.", "confidence": 0.5}
            ]
        }
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal")
        op = result.proposed_patch.operations[0]
        findings = op.entity["metadata"]["lint_findings"]
        categories = {f["category"] for f in findings}
        assert "weak_modal" in categories
        assert "weak_word" in categories
        assert op.entity["metadata"]["quality"]["clarity"] == "fail"

    async def test_no_requirements_yields_no_patch(self):
        agent = RequirementAuthorAgent(extract=await _fake_extract({}))
        result = await agent.author("a vague goal")
        assert result.proposed_patch is None
        assert result.conclusions == []

    async def test_malformed_items_are_skipped(self):
        spec = {"requirements": ["not a dict", {"statement": ""}, {"statement": "real one"}]}
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal")
        assert len(result.proposed_patch.operations) == 1

    async def test_parent_ref_adds_a_link_operation_per_requirement(self):
        parent_id = uuid4()
        spec = {"requirements": [{"statement": "The system shall weigh less than 3 kg."}]}
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal", parent_ref=str(parent_id))

        ops = result.proposed_patch.operations
        add_ops = [op for op in ops if op.op == PatchOp.ADD]
        link_ops = [op for op in ops if op.op == PatchOp.LINK]
        assert len(add_ops) == 1
        assert len(link_ops) == 1
        assert link_ops[0].entity_id == add_ops[0].entity["id"]
        assert link_ops[0].target_id == parent_id
        assert link_ops[0].relation == "derives_from"

    async def test_parent_ref_sets_traceability_pass_in_quality_record(self):
        parent_id = uuid4()
        spec = {"requirements": [{"statement": "The system shall weigh less than 3 kg."}]}
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal", parent_ref=str(parent_id))
        op = result.proposed_patch.operations[0]
        assert op.entity["metadata"]["quality"]["traceability"] == "pass"

    async def test_no_parent_ref_leaves_traceability_unevaluated(self):
        spec = {"requirements": [{"statement": "The system shall weigh less than 3 kg."}]}
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal")
        op = result.proposed_patch.operations[0]
        assert op.entity["metadata"]["quality"]["traceability"] is None

    async def test_extract_failure_propagates(self):
        async def boom(source: str, context: str) -> dict:
            raise RuntimeError("provider unavailable")

        agent = RequirementAuthorAgent(extract=boom)
        with pytest.raises(RuntimeError):
            await agent.author("goal")

    async def test_project_id_is_attached_to_the_patch(self):
        spec = {"requirements": [{"statement": "The system shall weigh less than 3 kg."}]}
        pid = str(uuid4())
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal", project_id=pid)
        assert str(result.proposed_patch.project_id) == pid

    async def test_confidence_is_clamped(self):
        spec = {
            "requirements": [
                {"statement": "The system shall weigh less than 3 kg.", "confidence": 5.0}
            ]
        }
        agent = RequirementAuthorAgent(extract=await _fake_extract(spec))
        result = await agent.author("goal")
        assert result.confidence == 1.0
