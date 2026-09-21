"""Unit tests for the Engineering Change Transaction lifecycle (FORGE-66):
twin_core.transactions.ect.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.hitl.engine import HITLEngine, IndependenceViolation
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_change_transaction import ChangeTrigger, ECTStatus
from twin_core.models.enums import ConstraintSeverity
from twin_core.models.patch import Patch, PatchOp, PatchOperation
from twin_core.transactions.ect import (
    ECTStateError,
    analyze,
    approve,
    commit,
    mark_rolled_back,
    propose_change,
    reject,
)


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _seed_requirement(twin, project_id, name="mass_budget") -> Constraint:
    return await twin.create_constraint(
        Constraint(
            name=name,
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
            project_id=project_id,
        )
    )


def _revise_patch(req: Constraint, *, project_id, reason="fix mass budget", created_by="agent"):
    return Patch(
        operations=[
            PatchOperation(
                op=PatchOp.REVISE,
                entity_kind="constraint",
                entity_id=req.id,
                fields={"message": "revised"},
                expected_revision=req.revision,
            )
        ],
        reason=reason,
        created_by=created_by,
        project_id=project_id,
    )


class TestProposeChange:
    async def test_creates_ect_in_proposed_status(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="user asked to change mass budget",
            patch=_revise_patch(req, project_id=project_id),
            created_by="agent",
            project_id=project_id,
        )
        assert ect.status == ECTStatus.PROPOSED
        assert ect.affected_objects == []
        assert ect.impact is None
        assert ect.approval_required is None

        stored = await twin.get_ect(ect.id)
        assert stored is not None
        assert stored.trigger.type == "user_request"


class TestAnalyze:
    async def test_transitions_to_ready_for_review_with_direct_impact(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="simulation_result", ref="SIM-091"),
            observation="actuator exceeds thermal rating",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        analyzed = await analyze(twin, ect.id)
        assert analyzed.status == ECTStatus.READY_FOR_REVIEW
        assert analyzed.affected_objects == [str(req.id)]
        assert analyzed.impact in ("low", "medium", "high")
        assert isinstance(analyzed.approval_required, bool)

    async def test_cannot_analyze_twice(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        with pytest.raises(ECTStateError):
            await analyze(twin, ect.id)

    async def test_impact_reflects_baselined_entity_via_hitl_state(self, twin, project_id):
        """A REVISE against an already-baselined entity floors at
        EXPLICIT_APPROVAL in HITLEngine -- this should surface as impact
        'high' and approval_required True when the caller passes that
        context through `state`."""
        req = await _seed_requirement(twin, project_id)
        patch = _revise_patch(req, project_id=project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=patch,
            project_id=project_id,
        )
        analyzed = await analyze(twin, ect.id, state={"baselined_entity_ids": [str(req.id)]})
        assert analyzed.impact == "high"
        assert analyzed.approval_required is True


class TestApproveReject:
    async def test_approve_transitions_to_approved(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id, created_by="agent"),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        approved = await approve(twin, ect.id, approver="reviewer")
        assert approved.status == ECTStatus.APPROVED
        assert approved.decided_by == "reviewer"

    async def test_cannot_approve_before_analyze(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        with pytest.raises(ECTStateError):
            await approve(twin, ect.id, approver="reviewer")

    async def test_independence_violation_when_approver_is_author(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id, created_by="agent-1"),
            project_id=project_id,
        )
        await analyze(twin, ect.id, state={"baselined_entity_ids": [str(req.id)]})
        with pytest.raises(IndependenceViolation):
            await approve(
                twin,
                ect.id,
                approver="agent-1",
                state={
                    "baselined_entity_ids": [str(req.id)],
                    "safety_critical": True,
                    "category": "verification",
                },
            )

    async def test_reject_transitions_to_rejected_terminal(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        rejected = await reject(twin, ect.id, reason="not justified", decided_by="reviewer")
        assert rejected.status == ECTStatus.REJECTED
        assert rejected.decision_reason == "not justified"

    async def test_reject_requires_reason(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        with pytest.raises(ValueError, match="reason"):
            await reject(twin, ect.id, reason="")


class TestCommit:
    async def test_commit_applies_the_patch_and_transitions_to_committed(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        await approve(twin, ect.id, approver="reviewer")
        committed = await commit(twin, ect.id)
        assert committed.status == ECTStatus.COMMITTED
        assert committed.committed_patch_result["status"] == "committed"

        updated_req = await twin.get_constraint(req.id)
        assert updated_req is not None
        assert updated_req.message == "revised"
        assert updated_req.revision == 2

    async def test_cannot_commit_before_approved(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        with pytest.raises(ECTStateError):
            await commit(twin, ect.id)

    async def test_conflict_on_commit_stays_approved_not_committed(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            # expected_revision pinned to 1, but we'll bump the real
            # revision to 2 before commit -- forces a conflict.
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        await approve(twin, ect.id, approver="reviewer")
        await twin.update_constraint(req.id, {"message": "someone else's change"})

        result = await commit(twin, ect.id)
        assert result.status == ECTStatus.APPROVED
        assert result.committed_patch_result["status"] == "conflict"


class TestMarkRolledBack:
    async def test_rolled_back_requires_committed_status(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        with pytest.raises(ECTStateError):
            await mark_rolled_back(twin, ect.id, reason="regretted it")

    async def test_marks_rolled_back_after_commit(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        await approve(twin, ect.id, approver="reviewer")
        await commit(twin, ect.id)
        rolled_back = await mark_rolled_back(twin, ect.id, reason="regretted it")
        assert rolled_back.status == ECTStatus.ROLLED_BACK
        assert rolled_back.decision_reason == "regretted it"


class TestListAndGet:
    async def test_list_ects_scoped_to_project(self, twin, project_id):
        other_project = uuid4()
        req = await _seed_requirement(twin, project_id)
        await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=_revise_patch(req, project_id=project_id),
            project_id=project_id,
        )
        req_other = await _seed_requirement(twin, other_project, name="other_req")
        await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="y",
            patch=_revise_patch(req_other, project_id=other_project),
            project_id=other_project,
        )
        scoped = await twin.list_ects(project_id=project_id)
        assert len(scoped) == 1

    async def test_get_ect_returns_none_for_unknown_id(self, twin):
        assert await twin.get_ect(uuid4()) is None


def test_hitl_engine_still_importable_standalone() -> None:
    """Sanity check the composition point -- HITLEngine remains usable on
    its own outside the ECT wrapper."""
    assert HITLEngine is not None
