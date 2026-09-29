"""Unit tests for api_gateway.requirement_intelligence.matrix (FORGE-318)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.requirement_intelligence.matrix import build_requirement_matrix
from api_gateway.twin.claim_recorder import make_claim_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.staleness import StalenessEngine, StalenessStatus
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _seed_requirement(twin, project_id, name="moving_mass_budget", message="<= 4.5 kg"):
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


class TestBuildRequirementMatrix:
    async def test_no_data_when_no_claim_recorded(self, twin, project_id):
        await _seed_requirement(twin, project_id)
        rows = await build_requirement_matrix(twin, project_id)
        assert len(rows) == 1
        assert rows[0].status == "no_data"

    async def test_pass_when_margin_positive_and_not_escalated(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, name="deflection", message="<= 0.5mm")
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="calculation",
            producer={"tool": "twin.evaluate_metric"},
            inputs={},
            result={
                "metric": "tip_deflection",
                "tier": 0,
                "value_mm": 0.05,
                "band_mm": 0.1,
                "limit_mm": 0.5,
                "margin_mm": 0.45,
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
        rows = await build_requirement_matrix(twin, project_id)
        row = next(r for r in rows if r.requirementId == str(req.id))
        assert row.status == "pass"
        assert row.evidence[0].tier == 0
        assert row.evidence[0].margin == 0.45

    async def test_fail_when_margin_negative(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="calculation",
            producer={"tool": "twin.rank_sensitivity"},
            inputs={},
            result={
                "metric": "mass",
                "baseline_value": 6.78,
                "limit": 4.5,
                "baseline_margin": -2.28,
                "rankings": [],
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
        rows = await build_requirement_matrix(twin, project_id)
        row = next(r for r in rows if r.requirementId == str(req.id))
        assert row.status == "fail"
        assert "exceeds limit" in row.detail

    async def test_uncertain_when_escalated(self, twin, project_id):
        req = await _seed_requirement(twin, project_id, name="deflection", message="<= 0.5mm")
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="calculation",
            producer={"tool": "twin.evaluate_metric"},
            inputs={},
            result={
                "metric": "tip_deflection",
                "tier": 0,
                "value_mm": 0.42,
                "band_mm": 0.1,
                "limit_mm": 0.5,
                "margin_mm": 0.08,
                "escalated": True,
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
        rows = await build_requirement_matrix(twin, project_id)
        row = next(r for r in rows if r.requirementId == str(req.id))
        assert row.status == "uncertain"

    async def test_stale_flagged_even_though_claim_still_supported_elsewhere(
        self, twin, project_id
    ):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        stale_ev = await record_evidence(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={},
            result={"value_mm": 1, "limit_mm": 2, "margin_mm": 1, "escalated": False},
            project_id=str(project_id),
        )
        current_ev = await record_evidence(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={},
            result={"value_mm": 1, "limit_mm": 2, "margin_mm": 1, "escalated": False},
            project_id=str(project_id),
        )
        record_claim = make_claim_recorder(twin)
        await record_claim(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            evidence_refs=[stale_ev["node_id"], current_ev["node_id"]],
            project_id=str(project_id),
        )
        await StalenessEngine(twin).set_status(
            "engineering_entity", UUID(stale_ev["node_id"]), StalenessStatus.STALE
        )
        rows = await build_requirement_matrix(twin, project_id)
        row = next(r for r in rows if r.requirementId == str(req.id))
        assert row.status == "stale"
        staleness_by_id = {e.id: e.staleness for e in row.evidence}
        assert staleness_by_id[stale_ev["node_id"]] == "stale"
        assert staleness_by_id[current_ev["node_id"]] == "current"

    async def test_stale_when_claim_unsupported_due_to_all_stale_evidence(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        artefact = await _seed_artefact(twin, project_id)
        record_evidence = make_evidence_recorder(twin)
        ev = await record_evidence(
            evidence_type="calculation",
            producer={"tool": "x"},
            inputs={},
            result={"value_mm": 1},
            project_id=str(project_id),
        )
        record_claim = make_claim_recorder(twin)
        await record_claim(
            requirement_ref=req.name,
            artefact_ref=artefact.name,
            evidence_refs=[ev["node_id"]],
            project_id=str(project_id),
        )
        await StalenessEngine(twin).set_status(
            "engineering_entity", UUID(ev["node_id"]), StalenessStatus.STALE
        )
        rows = await build_requirement_matrix(twin, project_id)
        row = next(r for r in rows if r.requirementId == str(req.id))
        assert row.status == "stale"

    async def test_candidate_requirements_excluded(self, twin, project_id):
        await twin.create_constraint(
            Constraint(
                name="candidate_req",
                expression="True",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=project_id,
                metadata={"candidate": True},
            )
        )
        rows = await build_requirement_matrix(twin, project_id)
        assert rows == []

    async def test_limit_text_reflects_recorded_message(self, twin, project_id):
        await _seed_requirement(twin, project_id, message="mass <= 4.5 kg")
        rows = await build_requirement_matrix(twin, project_id)
        assert rows[0].limitText == "mass <= 4.5 kg"

    async def test_limit_text_prefers_structured_binding_when_present(self, twin, project_id):
        # FORGE-259: a real metric/operator/limit/unit binding beats the
        # free-text message fallback.
        await twin.create_constraint(
            Constraint(
                name="tip_deflection",
                expression="True",
                message="a stale free-text description",
                metric="tip_deflection",
                operator="<=",
                limit=0.5,
                unit="mm",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=project_id,
            )
        )
        rows = await build_requirement_matrix(twin, project_id)
        assert rows[0].limitText == "tip_deflection <= 0.5mm"

    async def test_verification_method_and_expected_evidence_surfaced(self, twin, project_id):
        # FORGE-258 (gap G-A2): a matrix row carries the declaration itself,
        # not just the live evidence-derived status.
        await twin.create_constraint(
            Constraint(
                name="safety_factor",
                expression="True",
                verification_method="FEA",
                expected_evidence="simulation",
                severity=ConstraintSeverity.ERROR,
                domain="mech",
                source="test",
                project_id=project_id,
            )
        )
        rows = await build_requirement_matrix(twin, project_id)
        assert rows[0].verificationMethod == "FEA"
        assert rows[0].expectedEvidence == "simulation"

    async def test_verification_method_and_expected_evidence_default_to_empty(
        self, twin, project_id
    ):
        await _seed_requirement(twin, project_id)
        rows = await build_requirement_matrix(twin, project_id)
        assert rows[0].verificationMethod == ""
        assert rows[0].expectedEvidence == ""
