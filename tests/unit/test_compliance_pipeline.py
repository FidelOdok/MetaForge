"""Comprehensive tests for the compliance checklist generator and evidence tracking pipeline.

Covers: YAML loading, checklist generation (single/multi market), deduplication,
evidence linking, coverage computation, API routes, and agent dispatch.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from domain_agents.compliance.agent import (
    ComplianceAgent,
    ComplianceTaskRequest,
)
from domain_agents.compliance.checklist_generator import ChecklistGenerator
from domain_agents.compliance.evidence_tracker import EvidenceTracker
from domain_agents.compliance.models import (
    ChecklistItem,
    ComplianceChecklist,
    ComplianceEvidence,
    ComplianceRegime,
    EvidenceStatus,
    EvidenceType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REGIMES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "domain_agents" / "compliance" / "regimes"
)


def _make_checklist(items: list[ChecklistItem]) -> ComplianceChecklist:
    """Create a minimal ComplianceChecklist wrapper."""
    return ComplianceChecklist(
        project_id="test-proj",
        target_markets=[ComplianceRegime.UKCA],
        items=items,
        total_items=len(items),
    )


# ===========================================================================
# 1. Model tests
# ===========================================================================


class TestModels:
    """Tests for Pydantic data models."""

    def test_compliance_regime_values(self):
        assert ComplianceRegime.UKCA.value == "UKCA"
        assert ComplianceRegime.CE.value == "CE"
        assert ComplianceRegime.FCC.value == "FCC"
        assert ComplianceRegime.PSTI.value == "PSTI"

    def test_evidence_status_values(self):
        assert EvidenceStatus.MISSING.value == "MISSING"
        assert EvidenceStatus.UPLOADED.value == "UPLOADED"
        assert EvidenceStatus.REVIEWED.value == "REVIEWED"
        assert EvidenceStatus.APPROVED.value == "APPROVED"

    def test_evidence_type_values(self):
        assert EvidenceType.TEST_REPORT.value == "TEST_REPORT"
        assert EvidenceType.DECLARATION.value == "DECLARATION"
        assert EvidenceType.CERTIFICATE.value == "CERTIFICATE"
        assert EvidenceType.TECHNICAL_FILE.value == "TECHNICAL_FILE"
        assert EvidenceType.RISK_ASSESSMENT.value == "RISK_ASSESSMENT"

    def test_checklist_item_defaults(self):
        item = ChecklistItem(
            id="TEST-001",
            regime=ComplianceRegime.UKCA,
            category="safety",
            requirement="Test requirement",
            standard="EN 12345",
            evidence_type=EvidenceType.TEST_REPORT,
        )
        assert item.evidence_status == EvidenceStatus.MISSING
        assert item.evidence_artifact_id is None
        assert item.notes == ""

    def test_compliance_checklist_defaults(self):
        cl = ComplianceChecklist(
            project_id="p1",
            target_markets=[ComplianceRegime.UKCA],
        )
        assert cl.items == []
        assert cl.total_items == 0
        assert cl.evidenced_items == 0
        assert cl.coverage_percent == 0.0
        assert cl.generated_at is not None

    def test_compliance_evidence_defaults(self):
        ev = ComplianceEvidence(
            checklist_item_id="TEST-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Test Report",
        )
        assert ev.status == EvidenceStatus.UPLOADED
        assert ev.id is not None
        assert ev.description == ""
        assert ev.reviewed_by is None
        assert ev.approved_by is None


# ===========================================================================
# 2. YAML loading tests
# ===========================================================================


class TestYamlLoading:
    """Tests for YAML regime file parsing."""

    def test_ukca_yaml_exists(self):
        assert (REGIMES_DIR / "ukca.yaml").is_file()

    def test_ce_yaml_exists(self):
        assert (REGIMES_DIR / "ce.yaml").is_file()

    def test_fcc_yaml_exists(self):
        assert (REGIMES_DIR / "fcc.yaml").is_file()

    def test_psti_yaml_exists(self):
        assert (REGIMES_DIR / "psti.yaml").is_file()

    def test_ukca_yaml_structure(self):
        data = yaml.safe_load((REGIMES_DIR / "ukca.yaml").read_text())
        assert data["regime"] == "UKCA"
        assert "categories" in data
        assert len(data["categories"]) >= 5  # safety, emc, radio, rohs, weee

    def test_ce_yaml_structure(self):
        data = yaml.safe_load((REGIMES_DIR / "ce.yaml").read_text())
        assert data["regime"] == "CE"
        assert len(data["categories"]) >= 6  # lvd, emc, red, rohs, weee, reach

    def test_fcc_yaml_structure(self):
        data = yaml.safe_load((REGIMES_DIR / "fcc.yaml").read_text())
        assert data["regime"] == "FCC"

    def test_psti_yaml_structure(self):
        data = yaml.safe_load((REGIMES_DIR / "psti.yaml").read_text())
        assert data["regime"] == "PSTI"

    def test_all_yaml_items_have_required_fields(self):
        for yaml_file in REGIMES_DIR.glob("*.yaml"):
            data = yaml.safe_load(yaml_file.read_text())
            for category in data.get("categories", []):
                for item in category.get("items", []):
                    assert "id" in item, f"Missing id in {yaml_file.name}"
                    assert "requirement" in item, f"Missing requirement in {yaml_file.name}"
                    assert "standard" in item, f"Missing standard in {yaml_file.name}"
                    assert "evidence_type" in item, f"Missing evidence_type in {yaml_file.name}"

    def test_load_regimes_returns_all_four(self):
        gen = ChecklistGenerator()
        regimes = gen.load_regimes(REGIMES_DIR)
        assert ComplianceRegime.UKCA in regimes
        assert ComplianceRegime.CE in regimes
        assert ComplianceRegime.FCC in regimes
        assert ComplianceRegime.PSTI in regimes

    def test_load_regimes_item_counts(self):
        gen = ChecklistGenerator()
        regimes = gen.load_regimes(REGIMES_DIR)
        assert len(regimes[ComplianceRegime.UKCA]) >= 15
        assert len(regimes[ComplianceRegime.CE]) >= 18
        assert len(regimes[ComplianceRegime.FCC]) >= 10
        assert len(regimes[ComplianceRegime.PSTI]) >= 8

    def test_load_regimes_nonexistent_dir(self):
        gen = ChecklistGenerator()
        regimes = gen.load_regimes(Path("/nonexistent/dir"))
        assert regimes == {}


# ===========================================================================
# 3. Checklist generation tests
# ===========================================================================


class TestChecklistGeneration:
    """Tests for ChecklistGenerator.generate_checklist."""

    def setup_method(self):
        self.gen = ChecklistGenerator()
        self.gen.load_regimes(REGIMES_DIR)

    def test_single_market_ukca(self):
        cl = self.gen.generate_checklist("proj-1", markets=[ComplianceRegime.UKCA])
        assert cl.project_id == "proj-1"
        assert cl.target_markets == [ComplianceRegime.UKCA]
        assert cl.total_items >= 15
        assert all(item.regime == ComplianceRegime.UKCA for item in cl.items)

    def test_single_market_fcc(self):
        cl = self.gen.generate_checklist("proj-2", markets=[ComplianceRegime.FCC])
        assert cl.total_items >= 10

    def test_single_market_psti(self):
        cl = self.gen.generate_checklist("proj-3", markets=[ComplianceRegime.PSTI])
        assert cl.total_items >= 8

    def test_multi_market_ukca_ce(self):
        cl = self.gen.generate_checklist(
            "proj-4", markets=[ComplianceRegime.UKCA, ComplianceRegime.CE]
        )
        assert ComplianceRegime.UKCA in cl.target_markets
        assert ComplianceRegime.CE in cl.target_markets
        # Should have items from both regimes
        regimes_present = {item.regime for item in cl.items}
        assert ComplianceRegime.UKCA in regimes_present
        assert ComplianceRegime.CE in regimes_present

    def test_multi_market_all(self):
        cl = self.gen.generate_checklist(
            "proj-all",
            markets=[
                ComplianceRegime.UKCA,
                ComplianceRegime.CE,
                ComplianceRegime.FCC,
                ComplianceRegime.PSTI,
            ],
        )
        assert cl.total_items > 0
        regimes_present = {item.regime for item in cl.items}
        assert len(regimes_present) >= 3  # At least 3 regimes after dedup

    def test_deduplication_reduces_items(self):
        """UKCA and CE share EN 55032 and EN 55035 -- dedup should remove duplicates."""
        ukca_only = self.gen.generate_checklist("p1", markets=[ComplianceRegime.UKCA])
        ce_only = self.gen.generate_checklist("p2", markets=[ComplianceRegime.CE])
        combined = self.gen.generate_checklist(
            "p3", markets=[ComplianceRegime.UKCA, ComplianceRegime.CE]
        )
        # Combined should be less than sum due to shared standards
        assert combined.total_items < ukca_only.total_items + ce_only.total_items

    def test_deduplication_no_duplicate_standards(self):
        cl = self.gen.generate_checklist(
            "p-dedup", markets=[ComplianceRegime.UKCA, ComplianceRegime.CE]
        )
        standards = [item.standard for item in cl.items]
        assert len(standards) == len(set(standards)), "Standards should be unique after dedup"

    def test_coverage_starts_at_zero(self):
        cl = self.gen.generate_checklist("p-cov", markets=[ComplianceRegime.UKCA])
        assert cl.coverage_percent == 0.0
        assert cl.evidenced_items == 0

    def test_product_category_stored(self):
        cl = self.gen.generate_checklist(
            "p-cat", product_category="iot_sensor", markets=[ComplianceRegime.PSTI]
        )
        assert cl.product_category == "iot_sensor"

    def test_none_markets_uses_all(self):
        cl = self.gen.generate_checklist("p-all", markets=None)
        assert cl.total_items > 0

    def test_empty_generator_produces_empty_checklist(self):
        empty_gen = ChecklistGenerator()
        cl = empty_gen.generate_checklist("p-empty", markets=[ComplianceRegime.UKCA])
        assert cl.total_items == 0
        assert cl.items == []


# ===========================================================================
# 4. Evidence tracker tests
# ===========================================================================


class TestEvidenceTracker:
    """Tests for EvidenceTracker."""

    def setup_method(self):
        self.tracker = EvidenceTracker()

    def test_link_evidence(self):
        ev = self.tracker.link_evidence(
            checklist_item_id="UKCA-SAF-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Safety Test Report",
        )
        assert ev.checklist_item_id == "UKCA-SAF-001"
        assert ev.evidence_type == EvidenceType.TEST_REPORT
        assert ev.status == EvidenceStatus.UPLOADED
        assert ev.title == "Safety Test Report"

    def test_get_evidence_by_id(self):
        ev = self.tracker.link_evidence(
            checklist_item_id="UKCA-SAF-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Report",
        )
        retrieved = self.tracker.get_evidence(ev.id)
        assert retrieved is not None
        assert retrieved.id == ev.id

    def test_get_evidence_not_found(self):
        assert self.tracker.get_evidence(uuid4()) is None

    def test_get_evidence_for_item(self):
        self.tracker.link_evidence(
            checklist_item_id="UKCA-SAF-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Report 1",
        )
        self.tracker.link_evidence(
            checklist_item_id="UKCA-SAF-001",
            evidence_type=EvidenceType.DECLARATION,
            title="Declaration",
        )
        records = self.tracker.get_evidence_for_item("UKCA-SAF-001")
        assert len(records) == 2

    def test_get_evidence_for_item_empty(self):
        records = self.tracker.get_evidence_for_item("NONEXISTENT")
        assert records == []

    def test_update_status_to_reviewed(self):
        ev = self.tracker.link_evidence(
            checklist_item_id="CE-LVD-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="LVD Report",
        )
        updated = self.tracker.update_status(ev.id, EvidenceStatus.REVIEWED, reviewed_by="alice")
        assert updated is not None
        assert updated.status == EvidenceStatus.REVIEWED
        assert updated.reviewed_by == "alice"

    def test_update_status_to_approved(self):
        ev = self.tracker.link_evidence(
            checklist_item_id="CE-LVD-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="LVD Report",
        )
        updated = self.tracker.update_status(ev.id, EvidenceStatus.APPROVED, approved_by="bob")
        assert updated is not None
        assert updated.status == EvidenceStatus.APPROVED
        assert updated.approved_by == "bob"

    def test_update_status_not_found(self):
        result = self.tracker.update_status(uuid4(), EvidenceStatus.REVIEWED)
        assert result is None

    def test_get_coverage_empty(self):
        cl = _make_checklist(
            [
                ChecklistItem(
                    id="X-001",
                    regime=ComplianceRegime.UKCA,
                    category="test",
                    requirement="req",
                    standard="STD-1",
                    evidence_type=EvidenceType.TEST_REPORT,
                ),
            ]
        )
        coverage = self.tracker.get_coverage(cl)
        assert coverage["total_items"] == 1
        assert coverage["evidenced_items"] == 0
        assert coverage["coverage_percent"] == 0.0

    def test_get_coverage_with_evidence(self):
        self.tracker.link_evidence(
            checklist_item_id="X-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Report",
        )
        cl = _make_checklist(
            [
                ChecklistItem(
                    id="X-001",
                    regime=ComplianceRegime.UKCA,
                    category="test",
                    requirement="req",
                    standard="STD-1",
                    evidence_type=EvidenceType.TEST_REPORT,
                ),
                ChecklistItem(
                    id="X-002",
                    regime=ComplianceRegime.UKCA,
                    category="test",
                    requirement="req2",
                    standard="STD-2",
                    evidence_type=EvidenceType.DECLARATION,
                ),
            ]
        )
        coverage = self.tracker.get_coverage(cl)
        assert coverage["total_items"] == 2
        assert coverage["evidenced_items"] == 1
        assert coverage["coverage_percent"] == 50.0

    def test_get_missing_items(self):
        self.tracker.link_evidence(
            checklist_item_id="X-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Report",
        )
        items = [
            ChecklistItem(
                id="X-001",
                regime=ComplianceRegime.UKCA,
                category="a",
                requirement="r1",
                standard="S1",
                evidence_type=EvidenceType.TEST_REPORT,
            ),
            ChecklistItem(
                id="X-002",
                regime=ComplianceRegime.UKCA,
                category="b",
                requirement="r2",
                standard="S2",
                evidence_type=EvidenceType.DECLARATION,
            ),
        ]
        cl = _make_checklist(items)
        missing = self.tracker.get_missing_items(cl)
        assert len(missing) == 1
        assert missing[0].id == "X-002"

    def test_get_missing_items_none_missing(self):
        self.tracker.link_evidence(
            checklist_item_id="X-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="Report",
        )
        cl = _make_checklist(
            [
                ChecklistItem(
                    id="X-001",
                    regime=ComplianceRegime.UKCA,
                    category="a",
                    requirement="r1",
                    standard="S1",
                    evidence_type=EvidenceType.TEST_REPORT,
                ),
            ]
        )
        missing = self.tracker.get_missing_items(cl)
        assert len(missing) == 0


# ===========================================================================
# 5. Compliance agent tests
# ===========================================================================


class TestComplianceAgent:
    """Tests for ComplianceAgent dispatch."""

    @pytest.fixture
    def agent(self):
        return ComplianceAgent(regimes_dir=REGIMES_DIR)

    @pytest.mark.asyncio
    async def test_generate_checklist_task(self, agent):
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="test-proj",
                parameters={"markets": ["UKCA"]},
            )
        )
        assert result.success is True
        assert result.total_requirements >= 15
        assert result.coverage_percent == 0.0
        assert "checklist" in result.data

    @pytest.mark.asyncio
    async def test_generate_checklist_multi_market(self, agent):
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="test-proj",
                parameters={"markets": ["UKCA", "CE"]},
            )
        )
        assert result.success is True
        assert result.total_requirements > 0

    @pytest.mark.asyncio
    async def test_link_evidence_task(self, agent):
        # First generate a checklist
        await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="proj-ev",
                parameters={"markets": ["UKCA"]},
            )
        )
        # Then link evidence
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="link_evidence",
                project_id="proj-ev",
                parameters={
                    "checklist_item_id": "UKCA-SAF-001",
                    "evidence_type": "TEST_REPORT",
                    "title": "EN 62368-1 Test",
                },
            )
        )
        assert result.success is True
        assert "evidence" in result.data

    @pytest.mark.asyncio
    async def test_link_evidence_missing_fields(self, agent):
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="link_evidence",
                project_id="proj-ev",
                parameters={},
            )
        )
        assert result.success is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_get_coverage_task(self, agent):
        # Generate checklist first
        await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="proj-cov",
                parameters={"markets": ["PSTI"]},
            )
        )
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="get_coverage",
                project_id="proj-cov",
            )
        )
        assert result.success is True
        assert result.coverage_percent == 0.0

    @pytest.mark.asyncio
    async def test_get_coverage_no_checklist(self, agent):
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="get_coverage",
                project_id="nonexistent",
            )
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unsupported_task(self, agent):
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="unsupported",
                project_id="proj",
            )
        )
        assert result.success is False
        assert "Unsupported" in result.errors[0]

    @pytest.mark.asyncio
    async def test_coverage_after_evidence(self, agent):
        """Coverage should increase after linking evidence."""
        await agent.run_task(
            ComplianceTaskRequest(
                task_type="generate_checklist",
                project_id="proj-full",
                parameters={"markets": ["PSTI"]},
            )
        )
        # Link evidence to one item
        await agent.run_task(
            ComplianceTaskRequest(
                task_type="link_evidence",
                project_id="proj-full",
                parameters={
                    "checklist_item_id": "PSTI-PWD-001",
                    "evidence_type": "TEST_REPORT",
                    "title": "Password Test",
                },
            )
        )
        result = await agent.run_task(
            ComplianceTaskRequest(
                task_type="get_coverage",
                project_id="proj-full",
            )
        )
        assert result.success is True
        assert result.evidenced_count == 1
        assert result.coverage_percent > 0.0


# ===========================================================================
# 6. API route tests
# ===========================================================================


class TestComplianceAPI:
    """Tests for compliance API routes using FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api_gateway.compliance.routes import router

        app = FastAPI()
        app.include_router(router)

        # Reset agent state for each test
        import api_gateway.compliance.routes as routes_mod
        from domain_agents.compliance.agent import ComplianceAgent

        routes_mod._agent = ComplianceAgent(regimes_dir=REGIMES_DIR)

        return TestClient(app)

    def test_get_checklist_single_market(self, client):
        resp = client.get("/api/v1/compliance/proj-1/checklist?markets=UKCA")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "proj-1"
        assert data["total_items"] >= 15
        assert len(data["items"]) >= 15

    def test_get_checklist_multi_market(self, client):
        resp = client.get("/api/v1/compliance/proj-2/checklist?markets=UKCA,CE")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_items"] > 0
        assert "UKCA" in data["target_markets"]
        assert "CE" in data["target_markets"]

    def test_get_checklist_invalid_market(self, client):
        resp = client.get("/api/v1/compliance/proj-3/checklist?markets=INVALID")
        assert resp.status_code == 400

    def test_link_evidence_endpoint(self, client):
        # Generate checklist first
        client.get("/api/v1/compliance/proj-ev/checklist?markets=UKCA")
        # Link evidence
        resp = client.post(
            "/api/v1/compliance/proj-ev/evidence",
            json={
                "checklist_item_id": "UKCA-SAF-001",
                "evidence_type": "TEST_REPORT",
                "title": "Safety Report",
                "description": "EN 62368-1 test results",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["checklist_item_id"] == "UKCA-SAF-001"
        assert data["evidence_type"] == "TEST_REPORT"
        assert data["status"] == "UPLOADED"

    def test_get_evidence_endpoint(self, client):
        # Generate checklist and link evidence
        client.get("/api/v1/compliance/proj-ge/checklist?markets=UKCA")
        client.post(
            "/api/v1/compliance/proj-ge/evidence",
            json={
                "checklist_item_id": "UKCA-EMC-001",
                "evidence_type": "TEST_REPORT",
                "title": "EMC Report",
            },
        )
        resp = client.get("/api/v1/compliance/proj-ge/evidence/UKCA-EMC-001")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "EMC Report"

    def test_get_evidence_empty(self, client):
        resp = client.get("/api/v1/compliance/proj-x/evidence/NONEXISTENT")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_coverage_endpoint(self, client):
        # Generate checklist
        client.get("/api/v1/compliance/proj-cov/checklist?markets=PSTI")
        resp = client.get("/api/v1/compliance/proj-cov/coverage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "proj-cov"
        assert data["total_items"] >= 8
        assert data["coverage_percent"] == 0.0

    def test_get_coverage_no_checklist(self, client):
        resp = client.get("/api/v1/compliance/nonexistent/coverage")
        assert resp.status_code == 404

    def test_coverage_increases_after_evidence(self, client):
        """Coverage should increase after evidence is linked."""
        client.get("/api/v1/compliance/proj-inc/checklist?markets=PSTI")
        # Link evidence to one item
        client.post(
            "/api/v1/compliance/proj-inc/evidence",
            json={
                "checklist_item_id": "PSTI-PWD-001",
                "evidence_type": "TEST_REPORT",
                "title": "Password Test",
            },
        )
        resp = client.get("/api/v1/compliance/proj-inc/coverage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["evidenced_items"] == 1
        assert data["coverage_percent"] > 0.0


# ===========================================================================
# 7. Skill handler tests
# ===========================================================================


class TestGenerateChecklistSkill:
    """Tests for the generate_checklist skill handler."""

    def test_definition_json_exists(self):
        defn_path = (
            Path(__file__).resolve().parent.parent.parent
            / "domain_agents"
            / "compliance"
            / "skills"
            / "generate_checklist"
            / "definition.json"
        )
        assert defn_path.is_file()

    def test_definition_json_content(self):
        import json

        defn_path = (
            Path(__file__).resolve().parent.parent.parent
            / "domain_agents"
            / "compliance"
            / "skills"
            / "generate_checklist"
            / "definition.json"
        )
        data = json.loads(defn_path.read_text())
        assert data["name"] == "generate_checklist"
        assert data["domain"] == "compliance"
        assert data["phase"] == 1

    def test_schema_imports(self):
        from domain_agents.compliance.skills.generate_checklist.schema import (
            GenerateChecklistInput,
            GenerateChecklistOutput,
        )

        assert GenerateChecklistInput is not None
        assert GenerateChecklistOutput is not None

    def test_handler_imports(self):
        from domain_agents.compliance.skills.generate_checklist.handler import (
            GenerateChecklistHandler,
        )

        assert GenerateChecklistHandler is not None
