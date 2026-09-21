"""Unit tests for the HITLEngine (FORGE-53)."""

from uuid import uuid4

import pytest

from twin_core.hitl import ApprovalRequest, HITLEngine, HITLLevel, IndependenceViolation
from twin_core.models.patch import Patch, PatchOp, PatchOperation


@pytest.fixture
def engine():
    return HITLEngine()


def _patch(*ops: PatchOperation, created_by: str = "") -> Patch:
    return Patch(operations=list(ops), reason="test", created_by=created_by)


def _link_op() -> PatchOperation:
    return PatchOperation(
        op=PatchOp.LINK, entity_id=uuid4(), relation="depends_on", target_id=uuid4()
    )


class TestBaseClassification:
    async def test_link_is_autonomous(self, engine):
        op = _link_op()
        result = await engine.required_approval(_patch(op), impact=None, state={})
        assert result.level == HITLLevel.AUTONOMOUS
        assert result.required is False
        assert result.notify is False

    async def test_add_is_review_by_default(self, engine):
        op = PatchOperation(op=PatchOp.ADD, entity_kind="constraint", entity={"name": "x"})
        result = await engine.required_approval(_patch(op), impact=None, state={})
        assert result.level == HITLLevel.REVIEW
        assert result.required is True
        assert result.notify is True

    async def test_deprecate_is_explicit_approval(self, engine):
        op = PatchOperation(op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=uuid4())
        result = await engine.required_approval(_patch(op), impact=None, state={})
        assert result.level == HITLLevel.EXPLICIT_APPROVAL
        assert result.required is True

    async def test_revise_on_proposed_entity_is_review(self, engine):
        op = PatchOperation(
            op=PatchOp.REVISE, entity_kind="constraint", entity_id=uuid4(), fields={"message": "x"}
        )
        result = await engine.required_approval(_patch(op), impact=None, state={})
        assert result.level == HITLLevel.REVIEW

    async def test_revise_on_baselined_entity_is_explicit_approval(self, engine):
        target = uuid4()
        op = PatchOperation(
            op=PatchOp.REVISE, entity_kind="constraint", entity_id=target, fields={"message": "x"}
        )
        result = await engine.required_approval(
            _patch(op), impact=None, state={"baselined_entity_ids": [str(target)]}
        )
        assert result.level == HITLLevel.EXPLICIT_APPROVAL

    async def test_revise_that_creates_a_baseline_is_explicit_approval(self, engine):
        op = PatchOperation(
            op=PatchOp.REVISE,
            entity_kind="constraint",
            entity_id=uuid4(),
            fields={"authority": "baselined"},
        )
        result = await engine.required_approval(_patch(op), impact=None, state={})
        assert result.level == HITLLevel.EXPLICIT_APPROVAL

    async def test_supersede_on_baselined_entity_is_explicit_approval(self, engine):
        old = uuid4()
        op = PatchOperation(
            op=PatchOp.SUPERSEDE, entity_kind="constraint", entity_id=old, target_id=uuid4()
        )
        result = await engine.required_approval(
            _patch(op), impact=None, state={"baselined_entity_ids": [str(old)]}
        )
        assert result.level == HITLLevel.EXPLICIT_APPROVAL

    async def test_patch_level_is_max_of_its_operations(self, engine):
        link_op = _link_op()
        deprecate_op = PatchOperation(
            op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=uuid4()
        )
        result = await engine.required_approval(
            _patch(link_op, deprecate_op), impact=None, state={}
        )
        assert result.level == HITLLevel.EXPLICIT_APPROVAL


class TestRiskHint:
    async def test_risk_hint_lowers_an_add_below_review(self, engine):
        op = PatchOperation(op=PatchOp.ADD, entity_kind="constraint", entity={"name": "x"})
        result = await engine.required_approval(
            _patch(op), impact=None, state={"risk_hint": "autonomous"}
        )
        assert result.level == HITLLevel.AUTONOMOUS

    async def test_risk_hint_does_not_lower_deletion_class_ops(self, engine):
        """A hint only applies to ADD/proposed-REVISE branches -- deprecate/
        invalidate and baselined-entity edits stay at their own fixed level
        regardless of any caller-supplied hint."""
        op = PatchOperation(op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=uuid4())
        result = await engine.required_approval(
            _patch(op), impact=None, state={"risk_hint": "autonomous"}
        )
        assert result.level == HITLLevel.EXPLICIT_APPROVAL


class TestSafetyCriticalFloors:
    async def test_waiver_floors_at_mandatory_authority(self, engine):
        op = _link_op()
        result = await engine.required_approval(
            _patch(op),
            impact=None,
            state={"safety_critical": True, "category": "waiver"},
        )
        assert result.level == HITLLevel.MANDATORY_AUTHORITY
        assert result.require_independent_approver is True

    async def test_floor_never_lowers_an_already_higher_level(self, engine):
        op = PatchOperation(op=PatchOp.DEPRECATE, entity_kind="constraint", entity_id=uuid4())
        result = await engine.required_approval(
            _patch(op),
            impact=None,
            state={"safety_critical": True, "category": "creation"},  # floor is only REVIEW
        )
        assert result.level == HITLLevel.EXPLICIT_APPROVAL  # unchanged, already higher

    async def test_verification_adds_a_note_without_forcing_a_level(self, engine):
        op = _link_op()
        result = await engine.required_approval(
            _patch(op),
            impact=None,
            state={"safety_critical": True, "category": "verification"},
        )
        assert result.level == HITLLevel.AUTONOMOUS  # no floor for verification
        assert result.require_independent_approver is True
        assert any("independent evidence" in n.lower() for n in result.notes)

    async def test_not_safety_critical_ignores_category(self, engine):
        op = _link_op()
        result = await engine.required_approval(
            _patch(op),
            impact=None,
            state={"category": "waiver"},  # safety_critical not set
        )
        assert result.level == HITLLevel.AUTONOMOUS
        assert result.require_independent_approver is False


class TestValidateApprover:
    def test_raises_when_approver_is_the_author(self, engine):
        op = _link_op()
        patch = _patch(op, created_by="agent:mech-agent-1")
        approval = ApprovalRequest(
            patch_id=patch.id,
            level=HITLLevel.MANDATORY_AUTHORITY,
            required=True,
            notify=True,
            reason="x",
            require_independent_approver=True,
        )
        with pytest.raises(IndependenceViolation):
            engine.validate_approver(approval, patch, "agent:mech-agent-1")

    def test_allows_a_different_approver(self, engine):
        op = _link_op()
        patch = _patch(op, created_by="agent:mech-agent-1")
        approval = ApprovalRequest(
            patch_id=patch.id,
            level=HITLLevel.MANDATORY_AUTHORITY,
            required=True,
            notify=True,
            reason="x",
            require_independent_approver=True,
        )
        engine.validate_approver(approval, patch, "human:reviewer-2")  # must not raise

    def test_no_check_when_independence_not_required(self, engine):
        op = _link_op()
        patch = _patch(op, created_by="agent:mech-agent-1")
        approval = ApprovalRequest(
            patch_id=patch.id, level=HITLLevel.REVIEW, required=True, notify=True, reason="x"
        )
        engine.validate_approver(approval, patch, "agent:mech-agent-1")  # must not raise
