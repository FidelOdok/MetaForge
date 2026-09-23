"""Unit tests for the G7 Verification Readiness Gate and G8 Release Gate
evaluators (FORGE-63)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency import (
    GateCheckStatus,
    GateStatus,
    evaluate_g7_verification_readiness,
    evaluate_g8_release,
)
from twin_core.models.baseline import Baseline
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState, ConstraintSeverity


@pytest.fixture
def twin():
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


def _critical_req(project_id, name="req1", metadata=None, source="") -> Constraint:
    return Constraint(
        name=name,
        expression="True",
        severity=ConstraintSeverity.ERROR,
        domain="mech",
        source=source,
        project_id=project_id,
        metadata=metadata or {},
    )


def _evidence(project_id, statement="sim result", metadata=None) -> EngineeringEntity:
    return EngineeringEntity(
        entity_type="evidence", statement=statement, project_id=project_id, metadata=metadata or {}
    )


def _waiver(
    project_id, statement="waived", authority: AuthorityState = AuthorityState.PROPOSED
) -> EngineeringEntity:
    return EngineeringEntity(
        entity_type="waiver", statement=statement, project_id=project_id, authority=authority
    )


def _release_approval(
    project_id, statement="release", authority: AuthorityState = AuthorityState.PROPOSED
) -> EngineeringEntity:
    return EngineeringEntity(
        entity_type="release_approval",
        statement=statement,
        project_id=project_id,
        authority=authority,
    )


class TestG7NoCriticalRequirements:
    async def test_no_critical_requirements_is_not_evaluated(self, twin, project_id):
        result = await evaluate_g7_verification_readiness(twin, project_id)
        check = next(c for c in result.checks if c.id == "requirements:none-critical")
        assert check.status == GateCheckStatus.NOT_EVALUATED
        assert result.status == GateStatus.READY_FOR_REVIEW

    async def test_warning_severity_requirement_does_not_count_as_critical(self, twin, project_id):
        req = _critical_req(project_id)
        req = req.model_copy(update={"severity": ConstraintSeverity.WARNING})
        await twin.create_constraint(req)
        result = await evaluate_g7_verification_readiness(twin, project_id)
        check = next(c for c in result.checks if c.id == "requirements:none-critical")
        assert check.status == GateCheckStatus.NOT_EVALUATED


class TestG7VerificationMethodCheck:
    async def test_verification_method_present_passes(self, twin, project_id):
        req = await twin.create_constraint(
            _critical_req(project_id, metadata={"verification_method": "FEA"}, source="agent")
        )
        result = await evaluate_g7_verification_readiness(twin, project_id)
        check_id = f"requirement:{req.id}:verification_method"
        check = next(c for c in result.checks if c.id == check_id)
        assert check.status == GateCheckStatus.PASS

    async def test_missing_verification_method_fails(self, twin, project_id):
        req = await twin.create_constraint(_critical_req(project_id, source="agent"))
        result = await evaluate_g7_verification_readiness(twin, project_id)
        check_id = f"requirement:{req.id}:verification_method"
        check = next(c for c in result.checks if c.id == check_id)
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED


class TestG7OwnershipCheck:
    async def test_source_present_passes(self, twin, project_id):
        req = await twin.create_constraint(_critical_req(project_id, source="req_handlers"))
        result = await evaluate_g7_verification_readiness(twin, project_id)
        check = next(c for c in result.checks if c.id == f"requirement:{req.id}:ownership")
        assert check.status == GateCheckStatus.PASS

    async def test_missing_source_fails(self, twin, project_id):
        req = await twin.create_constraint(_critical_req(project_id, source=""))
        result = await evaluate_g7_verification_readiness(twin, project_id)
        check = next(c for c in result.checks if c.id == f"requirement:{req.id}:ownership")
        assert check.status == GateCheckStatus.FAIL


class TestG7NotEvaluatedChecks:
    async def test_acceptance_measurement_evidence_are_not_evaluated(self, twin, project_id):
        result = await evaluate_g7_verification_readiness(twin, project_id)
        ids = {c.id for c in result.checks}
        for expected in (
            "acceptance_criteria_defined",
            "measurement_method_defined",
            "expected_evidence_defined",
        ):
            assert expected in ids
            check = next(c for c in result.checks if c.id == expected)
            assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_gate_id_is_g7(self, twin, project_id):
        result = await evaluate_g7_verification_readiness(twin, project_id)
        assert result.gate_id == "G7"


class TestG8BaselineCheck:
    async def test_no_baseline_fails(self, twin, project_id):
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "configuration_baseline_fixed")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_one_baseline_passes(self, twin, project_id):
        await twin.create_baseline(
            Baseline(name="v1", includes=[], project_id=project_id, reason="release candidate")
        )
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "configuration_baseline_fixed")
        assert check.status == GateCheckStatus.PASS

    async def test_other_projects_baseline_does_not_count(self, twin, project_id):
        other = uuid4()
        await twin.create_baseline(Baseline(name="v1", includes=[], project_id=other))
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "configuration_baseline_fixed")
        assert check.status == GateCheckStatus.FAIL


class TestG8StaleEvidenceCheck:
    async def test_no_evidence_is_not_evaluated_not_a_vacuous_pass(self, twin, project_id):
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "stale_evidence_resolved")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_current_evidence_passes(self, twin, project_id):
        entity = _evidence(project_id, metadata={"staleness": "current"})
        await twin.create_engineering_entity(entity)
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "stale_evidence_resolved")
        assert check.status == GateCheckStatus.PASS

    async def test_default_unset_staleness_counts_as_current(self, twin, project_id):
        await twin.create_engineering_entity(_evidence(project_id))
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "stale_evidence_resolved")
        assert check.status == GateCheckStatus.PASS

    async def test_stale_evidence_fails(self, twin, project_id):
        await twin.create_engineering_entity(_evidence(project_id, metadata={"staleness": "stale"}))
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "stale_evidence_resolved")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_invalid_evidence_fails(self, twin, project_id):
        entity = _evidence(project_id, metadata={"staleness": "invalid"})
        await twin.create_engineering_entity(entity)
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "stale_evidence_resolved")
        assert check.status == GateCheckStatus.FAIL


class TestG8NotEvaluatedChecks:
    async def test_verification_complete_is_not_evaluated_without_an_accessor(
        self, twin, project_id
    ):
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "required_verification_complete")
        assert check.status == GateCheckStatus.NOT_EVALUATED

    async def test_gate_id_is_g8(self, twin, project_id):
        result = await evaluate_g8_release(twin, project_id)
        assert result.gate_id == "G8"


class TestG8WaiversApprovedCheck:
    async def test_no_waivers_recorded_is_a_real_pass_not_vacuous(self, twin, project_id):
        """FORGE-73: unlike stale-evidence/baseline, zero waivers is a real
        PASS -- nothing outstanding needs one -- same vacuous-pass exception
        as G4's zero-constraints case."""
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "waivers_approved")
        assert check.status == GateCheckStatus.PASS

    async def test_unapproved_waiver_fails(self, twin, project_id):
        await twin.create_engineering_entity(_waiver(project_id, authority=AuthorityState.PROPOSED))
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "waivers_approved")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_approved_waiver_passes(self, twin, project_id):
        await twin.create_engineering_entity(_waiver(project_id, authority=AuthorityState.APPROVED))
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "waivers_approved")
        assert check.status == GateCheckStatus.PASS

    async def test_one_unapproved_among_several_still_fails(self, twin, project_id):
        await twin.create_engineering_entity(
            _waiver(project_id, "w1", authority=AuthorityState.APPROVED)
        )
        await twin.create_engineering_entity(
            _waiver(project_id, "w2", authority=AuthorityState.PROPOSED)
        )
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "waivers_approved")
        assert check.status == GateCheckStatus.FAIL


class TestG8ReleaseApprovedCheck:
    async def test_none_recorded_fails_not_a_vacuous_pass(self, twin, project_id):
        """Unlike waivers, release-to-manufacture is an unconditionally
        required sign-off -- absence FAILS, same posture as the baseline
        check's own missing-baseline FAIL."""
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "release_approved")
        assert check.status == GateCheckStatus.FAIL
        assert result.status == GateStatus.FAILED

    async def test_unapproved_release_approval_fails(self, twin, project_id):
        await twin.create_engineering_entity(
            _release_approval(project_id, authority=AuthorityState.PROPOSED)
        )
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "release_approved")
        assert check.status == GateCheckStatus.FAIL

    async def test_approved_release_approval_passes(self, twin, project_id):
        await twin.create_engineering_entity(
            _release_approval(project_id, authority=AuthorityState.APPROVED)
        )
        result = await evaluate_g8_release(twin, project_id)
        check = next(c for c in result.checks if c.id == "release_approved")
        assert check.status == GateCheckStatus.PASS


class TestVerificationCompleteWithInjectedAccessor:
    """FORGE-73: required_verification_complete becomes real once a caller
    injects a traceability_coverage accessor -- same seam as G6's
    requirement_coverage, reading a different field off the same object."""

    async def test_full_coverage_passes(self, twin, project_id):
        from types import SimpleNamespace

        async def accessor(pid):
            assert pid == project_id
            return SimpleNamespace(verification_to_evidence=100.0)

        result = await evaluate_g8_release(twin, project_id, traceability_coverage=accessor)
        check = next(c for c in result.checks if c.id == "required_verification_complete")
        assert check.status == GateCheckStatus.PASS

    async def test_partial_coverage_fails(self, twin, project_id):
        from types import SimpleNamespace

        async def accessor(pid):
            return SimpleNamespace(verification_to_evidence=40.0)

        result = await evaluate_g8_release(twin, project_id, traceability_coverage=accessor)
        check = next(c for c in result.checks if c.id == "required_verification_complete")
        assert check.status == GateCheckStatus.FAIL

    async def test_no_verification_cases_is_not_evaluated(self, twin, project_id):
        from types import SimpleNamespace

        async def accessor(pid):
            return SimpleNamespace(verification_to_evidence=None)

        result = await evaluate_g8_release(twin, project_id, traceability_coverage=accessor)
        check = next(c for c in result.checks if c.id == "required_verification_complete")
        assert check.status == GateCheckStatus.NOT_EVALUATED
