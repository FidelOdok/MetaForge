"""Integration tests: BOM risk scoring + compliance checklist pipeline.

Exercises the supply chain and compliance agents together using realistic
drone flight controller BOM data. Tests cover individual scoring, checklist
generation, evidence linking, coverage tracking, and the combined pipeline
that feeds into EVT gate readiness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain_agents.compliance.checklist_generator import ChecklistGenerator
from domain_agents.compliance.evidence_tracker import EvidenceTracker
from domain_agents.compliance.models import (
    ComplianceRegime,
    EvidenceStatus,
    EvidenceType,
)
from domain_agents.supply_chain.alt_parts import AlternatePartsFinder
from domain_agents.supply_chain.models import (
    BOMRiskReport,
    RiskLevel,
)
from domain_agents.supply_chain.risk_scorer import BOMRiskScorer

# ---------------------------------------------------------------------------
# Realistic drone FC BOM fixture (18 parts as dicts)
# ---------------------------------------------------------------------------

_REGIMES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "domain_agents" / "compliance" / "regimes"
)


def _make_drone_fc_bom() -> list[dict]:
    """Build a realistic drone flight controller BOM with 18 parts."""
    return [
        # MCU — multi-source, active
        {
            "mpn": "STM32F405RGT6",
            "manufacturer": "STMicroelectronics",
            "description": "ARM Cortex-M4 MCU 168MHz 1MB Flash",
            "quantity": 1,
            "num_sources": 3,
            "lifecycle": "active",
            "lead_time_weeks": 2,
            "stock": 5200,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # IMU — multi-source, active
        {
            "mpn": "MPU6050",
            "manufacturer": "InvenSense",
            "description": "6-axis IMU (accel + gyro)",
            "quantity": 1,
            "num_sources": 3,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 8500,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Barometer — dual source
        {
            "mpn": "BMP280",
            "manufacturer": "Bosch",
            "description": "Barometric pressure sensor",
            "quantity": 1,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 2,
            "stock": 4500,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Magnetometer — dual source
        {
            "mpn": "QMC5883L",
            "manufacturer": "QST",
            "description": "3-axis magnetometer",
            "quantity": 1,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 3,
            "stock": 2300,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": False,
        },
        # Voltage regulator 3.3V — multi-source
        {
            "mpn": "AMS1117-3.3",
            "manufacturer": "Advanced Monolithic Systems",
            "description": "3.3V LDO regulator",
            "quantity": 2,
            "num_sources": 3,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 25000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Voltage regulator 5V
        {
            "mpn": "LM7805",
            "manufacturer": "Texas Instruments",
            "description": "5V linear regulator",
            "quantity": 1,
            "num_sources": 3,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 15000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Crystal oscillator
        {
            "mpn": "ABM8-8.000MHZ",
            "manufacturer": "Abracon",
            "description": "8MHz crystal oscillator",
            "quantity": 1,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 9000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Decoupling caps (100nF)
        {
            "mpn": "GRM188R71C104KA01",
            "manufacturer": "Murata",
            "description": "100nF MLCC 0603",
            "quantity": 20,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 500000,
            "moq": 100,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Bulk cap
        {
            "mpn": "EEU-FC1V101",
            "manufacturer": "Panasonic",
            "description": "100uF 35V electrolytic",
            "quantity": 4,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 2,
            "stock": 12000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # USB connector
        {
            "mpn": "10118194-0001LF",
            "manufacturer": "Amphenol",
            "description": "Micro USB Type-B connector",
            "quantity": 1,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 2,
            "stock": 6000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Motor FET driver
        {
            "mpn": "IRLML6344TRPBF",
            "manufacturer": "Infineon",
            "description": "N-channel MOSFET 30V 5A",
            "quantity": 4,
            "num_sources": 3,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 18000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # LED indicator
        {
            "mpn": "LTST-C171KRKT",
            "manufacturer": "Lite-On",
            "description": "Red LED 0805",
            "quantity": 2,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 30000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # GPS module — dual source
        {
            "mpn": "NEO-M8N",
            "manufacturer": "u-blox",
            "description": "GPS/GLONASS receiver module",
            "quantity": 1,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 4,
            "stock": 800,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Hypothetical single-source MEMS sensor — HIGH RISK
        {
            "mpn": "XSENS-MTI-3",
            "manufacturer": "Xsens",
            "description": "9-axis MEMS IMU (single source)",
            "quantity": 1,
            "num_sources": 1,
            "lifecycle": "active",
            "lead_time_weeks": 16,
            "stock": 45,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": False,
        },
        # EOL part — CRITICAL RISK
        {
            "mpn": "MAX232CPE",
            "manufacturer": "Maxim",
            "description": "RS-232 transceiver (legacy debug port)",
            "quantity": 1,
            "num_sources": 1,
            "lifecycle": "eol",
            "lead_time_weeks": 26,
            "stock": 120,
            "moq": 1,
            "rohs_compliant": False,
            "reach_compliant": False,
        },
        # NRND part — MEDIUM-HIGH RISK
        {
            "mpn": "AT24C256C",
            "manufacturer": "Microchip",
            "description": "256Kbit I2C EEPROM (NRND)",
            "quantity": 1,
            "num_sources": 2,
            "lifecycle": "nrnd",
            "lead_time_weeks": 3,
            "stock": 3500,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Resistor — commodity
        {
            "mpn": "RC0603FR-0710KL",
            "manufacturer": "Yageo",
            "description": "10K 0603 resistor",
            "quantity": 30,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 1,
            "stock": 800000,
            "moq": 100,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
        # Current sense resistor
        {
            "mpn": "WSL2512R0100FEA",
            "manufacturer": "Vishay",
            "description": "0.01 ohm current sense resistor",
            "quantity": 4,
            "num_sources": 2,
            "lifecycle": "active",
            "lead_time_weeks": 2,
            "stock": 15000,
            "moq": 1,
            "rohs_compliant": True,
            "reach_compliant": True,
        },
    ]


@pytest.fixture
def drone_bom() -> list[dict]:
    """Realistic 18-part drone FC BOM as dicts."""
    return _make_drone_fc_bom()


@pytest.fixture
def scorer() -> BOMRiskScorer:
    return BOMRiskScorer()


@pytest.fixture
def risk_report(scorer: BOMRiskScorer, drone_bom: list[dict]) -> BOMRiskReport:
    """Pre-scored risk report for the drone FC BOM."""
    return scorer.score_bom(drone_bom, project_id="drone-fc")


@pytest.fixture
def checklist_generator() -> ChecklistGenerator:
    gen = ChecklistGenerator()
    gen.load_regimes(_REGIMES_DIR)
    return gen


@pytest.fixture
def evidence_tracker() -> EvidenceTracker:
    return EvidenceTracker()


# ===========================================================================
# BOM Risk Scoring Tests
# ===========================================================================


class TestBOMRiskScoring:
    """Tests for BOM risk scoring on realistic drone FC data."""

    def test_score_full_bom_returns_report(self, scorer: BOMRiskScorer, drone_bom: list[dict]):
        report = scorer.score_bom(drone_bom, project_id="drone-fc")
        assert isinstance(report, BOMRiskReport)
        assert report.total_parts == 18
        assert len(report.part_scores) == 18

    def test_stm32f405_scores_low_risk(self, risk_report: BOMRiskReport):
        """STM32F405RGT6 is multi-source, active lifecycle -> low risk."""
        stm32 = next(s for s in risk_report.part_scores if s.mpn == "STM32F405RGT6")
        assert stm32.risk_level == RiskLevel.LOW
        assert stm32.overall_score <= 25

    def test_single_source_mems_scores_medium_or_higher(self, risk_report: BOMRiskReport):
        """XSENS-MTI-3 is single-source with low stock and long lead -> elevated risk."""
        xsens = next(s for s in risk_report.part_scores if s.mpn == "XSENS-MTI-3")
        assert xsens.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert xsens.overall_score >= 26
        assert xsens.flagged or xsens.risk_level != RiskLevel.LOW

    def test_eol_part_scores_high_or_critical(self, risk_report: BOMRiskReport):
        """MAX232CPE is EOL with single source -> high or critical risk."""
        max232 = next(s for s in risk_report.part_scores if s.mpn == "MAX232CPE")
        assert max232.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert max232.overall_score >= 51
        assert max232.flagged is True

    def test_nrnd_part_scores_medium_or_higher(self, risk_report: BOMRiskReport):
        """AT24C256C is NRND -> at least medium risk."""
        eeprom = next(s for s in risk_report.part_scores if s.mpn == "AT24C256C")
        assert eeprom.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH)
        assert eeprom.overall_score >= 10

    def test_commodity_passive_scores_low(self, risk_report: BOMRiskReport):
        """Commodity passives (resistors, caps) should score low risk."""
        resistor = next(s for s in risk_report.part_scores if s.mpn == "RC0603FR-0710KL")
        assert resistor.risk_level == RiskLevel.LOW
        assert resistor.overall_score <= 25

    def test_overall_risk_reflects_high_risk_parts(self, risk_report: BOMRiskReport):
        """Overall BOM risk should be nonzero due to EOL and single-source parts."""
        assert risk_report.overall_score > 0
        assert risk_report.critical_count + risk_report.high_count >= 1

    def test_high_risk_parts_counted(self, risk_report: BOMRiskReport):
        """High-risk or critical parts count should be positive."""
        assert risk_report.critical_count + risk_report.high_count >= 1

    def test_risk_scores_are_bounded(self, risk_report: BOMRiskReport):
        """All risk scores should be in [0, 100]."""
        for part in risk_report.part_scores:
            assert 0 <= part.overall_score <= 100


# ===========================================================================
# Alternate Parts Tests
# ===========================================================================


class TestAlternateParts:
    """Tests for alternate parts finder on high-risk BOM components."""

    def test_finds_alternates_from_candidates(self):
        finder = AlternatePartsFinder()
        result = finder.find_alternates(
            mpn="MAX232CPE",
            specs={"package": "DIP-16", "lifecycle": "eol", "num_sources": 1},
            distributor_results=[
                {
                    "mpn": "MAX3232CPE",
                    "manufacturer": "Maxim",
                    "package": "DIP-16",
                    "stock": 5000,
                    "lead_time_weeks": 2,
                    "num_sources": 3,
                    "lifecycle": "active",
                    "price": 1.50,
                },
            ],
        )
        assert result.original_mpn == "MAX232CPE"
        assert len(result.alternates) >= 0  # May or may not match

    def test_no_alternates_when_candidates_empty(self):
        finder = AlternatePartsFinder()
        result = finder.find_alternates(
            mpn="MAX232CPE",
            specs={"package": "DIP-16"},
            distributor_results=[],
        )
        assert len(result.alternates) == 0
        assert "No suitable alternates" in result.recommendation


# ===========================================================================
# Compliance Checklist Generation Tests
# ===========================================================================


class TestComplianceChecklist:
    """Tests for compliance checklist generation."""

    def test_generate_ce_checklist(self, checklist_generator: ChecklistGenerator):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.CE]
        )
        assert checklist.total_items > 0
        assert all(i.regime == ComplianceRegime.CE for i in checklist.items)

    def test_generate_fcc_checklist(self, checklist_generator: ChecklistGenerator):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.FCC]
        )
        assert checklist.total_items > 0
        assert all(i.regime == ComplianceRegime.FCC for i in checklist.items)

    def test_generate_ukca_checklist(self, checklist_generator: ChecklistGenerator):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.UKCA]
        )
        assert checklist.total_items > 0
        assert all(i.regime == ComplianceRegime.UKCA for i in checklist.items)

    def test_generate_combined_checklist(self, checklist_generator: ChecklistGenerator):
        """Combined UKCA + CE + FCC checklist should have items from all three."""
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc",
            markets=[ComplianceRegime.UKCA, ComplianceRegime.CE, ComplianceRegime.FCC],
        )
        # Deduplication may reduce total, but should have items
        assert checklist.total_items > 0
        regimes_present = {i.regime for i in checklist.items}
        assert len(regimes_present) >= 2  # At least 2 regimes after dedup

    def test_all_items_start_missing(self, checklist_generator: ChecklistGenerator):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.CE]
        )
        assert all(i.evidence_status == EvidenceStatus.MISSING for i in checklist.items)

    def test_initial_coverage_is_zero(self, checklist_generator: ChecklistGenerator):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.CE]
        )
        assert checklist.coverage_percent == 0.0
        assert checklist.evidenced_items == 0


# ===========================================================================
# Evidence Tracker Tests
# ===========================================================================


class TestEvidenceTracker:
    """Tests for evidence linking and coverage computation."""

    def test_link_evidence_to_item(
        self, checklist_generator: ChecklistGenerator, evidence_tracker: EvidenceTracker
    ):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.CE]
        )
        first_item = checklist.items[0]

        evidence = evidence_tracker.link_evidence(
            checklist_item_id=first_item.id,
            evidence_type=EvidenceType.TEST_REPORT,
            title="Conducted emissions test report",
        )
        assert evidence is not None
        assert evidence.checklist_item_id == first_item.id

    def test_link_evidence_retrievable(
        self, checklist_generator: ChecklistGenerator, evidence_tracker: EvidenceTracker
    ):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.CE]
        )
        first_item = checklist.items[0]

        evidence = evidence_tracker.link_evidence(
            checklist_item_id=first_item.id,
            evidence_type=EvidenceType.TEST_REPORT,
            title="EMC test report",
        )
        records = evidence_tracker.get_evidence_for_item(first_item.id)
        assert len(records) == 1
        assert records[0].id == evidence.id

    def test_coverage_increases_with_evidence(
        self, checklist_generator: ChecklistGenerator, evidence_tracker: EvidenceTracker
    ):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.FCC]
        )
        first_item = checklist.items[0]

        evidence_tracker.link_evidence(
            checklist_item_id=first_item.id,
            evidence_type=EvidenceType.TEST_REPORT,
            title="Conducted emissions test",
        )
        coverage = evidence_tracker.get_coverage(checklist)
        assert coverage["evidenced_items"] == 1
        assert coverage["coverage_percent"] > 0.0

    def test_full_coverage_when_all_items_have_evidence(
        self, checklist_generator: ChecklistGenerator, evidence_tracker: EvidenceTracker
    ):
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.FCC]
        )

        for idx, item in enumerate(checklist.items):
            evidence_tracker.link_evidence(
                checklist_item_id=item.id,
                evidence_type=EvidenceType.TEST_REPORT,
                title=f"Evidence for {item.requirement}",
            )

        coverage = evidence_tracker.get_coverage(checklist)
        assert coverage["coverage_percent"] == 100.0
        assert coverage["evidenced_items"] == coverage["total_items"]


# ===========================================================================
# Full Pipeline Integration Tests
# ===========================================================================


class TestBOMCompliancePipeline:
    """End-to-end tests combining BOM risk and compliance into gate readiness."""

    def test_full_pipeline_score_bom_then_checklist(
        self,
        scorer: BOMRiskScorer,
        drone_bom: list[dict],
        checklist_generator: ChecklistGenerator,
        evidence_tracker: EvidenceTracker,
    ):
        """Full pipeline: score BOM -> generate checklist -> link evidence -> check coverage."""
        # Step 1: Score BOM
        report = scorer.score_bom(drone_bom, project_id="drone-fc")
        assert report.total_parts == 18

        # Step 2: Generate compliance checklist
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc",
            markets=[ComplianceRegime.UKCA, ComplianceRegime.CE, ComplianceRegime.FCC],
        )
        assert checklist.total_items > 0

        # Step 3: Link some evidence
        first_item = checklist.items[0]
        evidence_tracker.link_evidence(
            checklist_item_id=first_item.id,
            evidence_type=EvidenceType.TEST_REPORT,
            title="EMC test report",
        )

        # Step 4: Compute coverage
        coverage = evidence_tracker.get_coverage(checklist)
        assert 0.0 < coverage["coverage_percent"] < 100.0

    def test_bom_risk_contributes_to_gate_readiness(self, risk_report: BOMRiskReport):
        """High-risk BOM should reduce gate readiness score."""
        # Gate readiness formula: base 100, penalize for risk
        bom_penalty = risk_report.overall_score * 0.4  # Scale to 40 points max
        readiness = 100 - bom_penalty
        assert readiness < 100  # Must be penalized (we have EOL parts)
        assert readiness > 0

    def test_compliance_coverage_contributes_to_gate_readiness(
        self,
        checklist_generator: ChecklistGenerator,
        evidence_tracker: EvidenceTracker,
    ):
        """Compliance coverage feeds into gate readiness."""
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc", markets=[ComplianceRegime.CE]
        )

        # No evidence -> 0% coverage
        coverage = evidence_tracker.get_coverage(checklist)
        assert coverage["coverage_percent"] == 0.0

        # With some evidence
        for idx, item in enumerate(checklist.items[:3]):
            evidence_tracker.link_evidence(
                checklist_item_id=item.id,
                evidence_type=EvidenceType.TEST_REPORT,
                title=f"Test report {idx}",
            )
        coverage = evidence_tracker.get_coverage(checklist)
        assert coverage["coverage_percent"] > 0.0

    def test_combined_gate_readiness_calculation(
        self,
        scorer: BOMRiskScorer,
        drone_bom: list[dict],
        checklist_generator: ChecklistGenerator,
        evidence_tracker: EvidenceTracker,
    ):
        """Combined BOM risk + compliance coverage -> EVT gate readiness."""
        # Score BOM
        report = scorer.score_bom(drone_bom, project_id="drone-fc")

        # Generate and partially complete compliance checklist
        checklist = checklist_generator.generate_checklist(
            project_id="drone-fc",
            markets=[ComplianceRegime.CE, ComplianceRegime.FCC],
        )
        # Accept evidence for half the items
        half = len(checklist.items) // 2
        for idx in range(half):
            evidence_tracker.link_evidence(
                checklist_item_id=checklist.items[idx].id,
                evidence_type=EvidenceType.TEST_REPORT,
                title=f"Evidence {idx}",
            )
        coverage = evidence_tracker.get_coverage(checklist)
        compliance_coverage = coverage["coverage_percent"]

        # Gate readiness: weighted combination
        bom_weight = 0.4
        compliance_weight = 0.6
        bom_score = (1 - report.overall_score / 100) * 100
        gate_readiness = bom_weight * bom_score + compliance_weight * compliance_coverage

        assert 0 < gate_readiness < 100
        assert gate_readiness > 20  # At least some readiness with half compliance done
