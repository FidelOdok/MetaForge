"""Unit tests for api_gateway.requirement_intelligence.promotion (FORGE-319)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.requirement_intelligence.promotion import attempt_promotion
from api_gateway.twin.claim_recorder import make_claim_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import AuthorityState, ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _seed_requirement(twin, project_id, name, message):
    return await twin.create_constraint(
        Constraint(
            name=name,
            expression="True",
            message=message,
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
            project_id=project_id,
        )
    )


async def _seed_artefact(twin, project_id, name="upper_arm"):
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            project_id=project_id,
        )
    )


async def _record_supported_claim(twin, project_id, req, artefact, *, margin_mm=0.45):
    record_evidence = make_evidence_recorder(twin)
    ev = await record_evidence(
        evidence_type="calculation",
        producer={"tool": "twin.evaluate_metric"},
        inputs={},
        result={
            "metric": "tip_deflection",
            "tier": 0,
            "value_mm": 0.5 - margin_mm,
            "band_mm": 0.1,
            "limit_mm": 0.5,
            "margin_mm": margin_mm,
            "escalated": False,
        },
        project_id=str(project_id),
    )
    record_claim = make_claim_recorder(twin)
    await record_claim(
        requirement_ref=req.name,
        artefact_ref=artefact.name,
        evidence_refs=[ev["node_id"]],
        project_id=str(project_id),
    )
    return ev["node_id"]


async def _record_failing_claim(twin, project_id, req, artefact):
    return await _record_supported_claim(twin, project_id, req, artefact, margin_mm=-0.2)


async def _seed_waiver(twin, project_id, *, requirement_id, approved: bool) -> EngineeringEntity:
    entity = await twin.create_engineering_entity(
        EngineeringEntity(
            entity_type="waiver",
            statement="accept mass overage for this iteration",
            project_id=project_id,
            parent_refs=[str(requirement_id)],
            authority=AuthorityState.APPROVED if approved else AuthorityState.PROPOSED,
        )
    )
    return entity


class TestAttemptPromotion:
    async def test_no_data_requirement_blocks(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "tip_deflection", "<= 0.5mm")
        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        assert result["promoted"] is False
        assert "no claim" in result["blocked_reason"]
        assert result["results"][0]["decision"] == "fail"

    async def test_all_pass_but_no_decided_by_is_a_dry_run_not_promoted(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "tip_deflection", "<= 0.5mm")
        artefact = await _seed_artefact(twin, project_id)
        await _record_supported_claim(twin, project_id, req, artefact)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
        )
        assert result["promoted"] is False
        assert "human authority" in result["blocked_reason"]
        assert result["results"][0]["decision"] == "pass"

    async def test_all_pass_with_decided_by_promotes(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "tip_deflection", "<= 0.5mm")
        artefact = await _seed_artefact(twin, project_id)
        await _record_supported_claim(twin, project_id, req, artefact)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        assert result["promoted"] is True
        assert result["blocked_reason"] is None
        assert result["results"][0]["decision"] == "pass"

    async def test_fail_without_waiver_blocks(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "moving_mass_budget", "<= 4.5 kg")
        artefact = await _seed_artefact(twin, project_id)
        await _record_failing_claim(twin, project_id, req, artefact)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        assert result["promoted"] is False
        assert result["results"][0]["decision"] == "fail"
        assert result["results"][0]["waiverId"] is None

    async def test_fail_with_approved_waiver_promotes(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "moving_mass_budget", "<= 4.5 kg")
        artefact = await _seed_artefact(twin, project_id)
        await _record_failing_claim(twin, project_id, req, artefact)
        waiver = await _seed_waiver(twin, project_id, requirement_id=req.id, approved=True)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        assert result["promoted"] is True
        assert result["results"][0]["decision"] == "waived"
        assert result["results"][0]["waiverId"] == str(waiver.id)

    async def test_fail_with_unapproved_waiver_still_blocks(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "moving_mass_budget", "<= 4.5 kg")
        artefact = await _seed_artefact(twin, project_id)
        await _record_failing_claim(twin, project_id, req, artefact)
        await _seed_waiver(twin, project_id, requirement_id=req.id, approved=False)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        assert result["promoted"] is False
        assert result["results"][0]["decision"] == "fail"

    async def test_waiver_for_a_different_requirement_does_not_apply(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "moving_mass_budget", "<= 4.5 kg")
        other_req = await _seed_requirement(twin, project_id, "other_req", "<= 1 kg")
        artefact = await _seed_artefact(twin, project_id)
        await _record_failing_claim(twin, project_id, req, artefact)
        # Waiver names a DIFFERENT requirement -- must not unblock this one.
        await _seed_waiver(twin, project_id, requirement_id=other_req.id, approved=True)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        assert result["promoted"] is False
        assert result["results"][0]["decision"] == "fail"

    async def test_arm_acceptance_scenario_deflection_and_sf_pass_mass_waived(
        self, twin, project_id
    ):
        """The ticket's own acceptance criterion: promoted only after
        deflection + SF are validated; mass FAIL is waived."""
        deflection = await _seed_requirement(twin, project_id, "tip_deflection", "<= 0.5mm")
        safety_factor = await _seed_requirement(twin, project_id, "safety_factor", ">= 2.0")
        mass = await _seed_requirement(twin, project_id, "moving_mass_budget", "<= 4.5 kg")
        artefact = await _seed_artefact(twin, project_id)

        await _record_supported_claim(twin, project_id, deflection, artefact)
        await _record_supported_claim(twin, project_id, safety_factor, artefact)
        await _record_failing_claim(twin, project_id, mass, artefact)
        await _seed_waiver(twin, project_id, requirement_id=mass.id, approved=True)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(deflection.id), str(safety_factor.id), str(mass.id)],
            decided_by="chief_engineer",
        )
        assert result["promoted"] is True
        decisions = {r["requirementName"]: r["decision"] for r in result["results"]}
        assert decisions["tip_deflection"] == "pass"
        assert decisions["safety_factor"] == "pass"
        assert decisions["moving_mass_budget"] == "waived"

    async def test_persists_the_gate_and_all_results(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "tip_deflection", "<= 0.5mm")
        artefact = await _seed_artefact(twin, project_id)
        await _record_supported_claim(twin, project_id, req, artefact)

        result = await attempt_promotion(
            twin,
            project_id=str(project_id),
            level="sim_validated",
            required_claim_ids=[str(req.id)],
            decided_by="reviewer",
        )
        gate = await twin.get_maturity_gate(UUID(result["gate_id"]))
        assert gate is not None
        assert gate.promoted is True
        assert gate.level.value == "sim_validated"
        assert gate.decided_by == "reviewer"
        assert len(gate.results) == 1


class TestAttemptPromotionAdapter:
    async def test_tool_registered_and_returns_shape(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, "tip_deflection", "<= 0.5mm")
        artefact = await _seed_artefact(twin, project_id)
        await _record_supported_claim(twin, project_id, req, artefact)

        async def promotion_attempter(**kwargs):
            return await attempt_promotion(twin, **kwargs)

        server = TwinServer(twin=twin, promotion_attempter=promotion_attempter)
        assert "twin.attempt_promotion" in server.tool_ids

        out = await server.attempt_promotion(
            {
                "project_id": str(project_id),
                "level": "sim_validated",
                "required_claim_ids": [str(req.id)],
                "decided_by": "reviewer",
            }
        )
        assert out["promoted"] is True

    async def test_not_registered_when_no_attempter_supplied(self, twin):
        server = TwinServer(twin=twin)
        assert "twin.attempt_promotion" not in server.tool_ids

    async def test_missing_required_claim_ids_rejected(self, twin):
        async def promotion_attempter(**kwargs):
            return {}

        server = TwinServer(twin=twin, promotion_attempter=promotion_attempter)
        with pytest.raises(ValueError, match="required_claim_ids"):
            await server.attempt_promotion({"project_id": "x", "level": "sim_validated"})
