"""Tests for BOM risk scoring and alternate parts engine (MET-177, MET-178)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from domain_agents.supply_chain.alt_parts import AlternatePartsFinder
from domain_agents.supply_chain.models import (
    BOMRiskReport,
    LifecycleStatus,
    PartRiskScore,
    RiskFactor,
    RiskLevel,
)
from domain_agents.supply_chain.risk_scorer import BOMRiskScorer, _classify_risk_level

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def scorer() -> BOMRiskScorer:
    return BOMRiskScorer()


@pytest.fixture
def finder() -> AlternatePartsFinder:
    return AlternatePartsFinder()


def _make_part(
    mpn: str = "TEST-001",
    manufacturer: str = "TestCorp",
    num_sources: int = 3,
    lead_time_weeks: float = 1,
    lifecycle: str = "active",
    prices: list[float] | None = None,
    stock: int = 10000,
    moq: int = 100,
    rohs_compliant: bool = True,
    reach_compliant: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """Helper to build a part data dict with sensible defaults."""
    d: dict[str, Any] = {
        "mpn": mpn,
        "manufacturer": manufacturer,
        "num_sources": num_sources,
        "lead_time_weeks": lead_time_weeks,
        "lifecycle": lifecycle,
        "prices": prices or [1.0, 1.0],
        "stock": stock,
        "moq": moq,
        "rohs_compliant": rohs_compliant,
        "reach_compliant": reach_compliant,
    }
    d.update(extra)
    return d


# ===========================================================================
# Risk level classification
# ===========================================================================


class TestRiskLevelClassification:
    def test_low_boundary(self) -> None:
        assert _classify_risk_level(0) == RiskLevel.LOW
        assert _classify_risk_level(25) == RiskLevel.LOW

    def test_medium_boundary(self) -> None:
        assert _classify_risk_level(26) == RiskLevel.MEDIUM
        assert _classify_risk_level(50) == RiskLevel.MEDIUM

    def test_high_boundary(self) -> None:
        assert _classify_risk_level(51) == RiskLevel.HIGH
        assert _classify_risk_level(75) == RiskLevel.HIGH

    def test_critical_boundary(self) -> None:
        assert _classify_risk_level(76) == RiskLevel.CRITICAL
        assert _classify_risk_level(100) == RiskLevel.CRITICAL


# ===========================================================================
# Single-source scoring
# ===========================================================================


class TestSingleSourceScoring:
    def test_one_distributor_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_single_source({"num_sources": 1})
        assert factor.score == 100
        assert factor.name == "single_source"

    def test_two_distributors_scores_50(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_single_source({"num_sources": 2})
        assert factor.score == 50

    def test_three_plus_distributors_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_single_source({"num_sources": 3})
        assert factor.score == 0
        factor = scorer._score_single_source({"num_sources": 10})
        assert factor.score == 0

    def test_missing_num_sources_defaults_to_1(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_single_source({})
        assert factor.score == 100

    def test_weight_is_correct(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_single_source({"num_sources": 1})
        assert factor.weight == 0.25


# ===========================================================================
# Lead time scoring
# ===========================================================================


class TestLeadTimeScoring:
    def test_short_lead_time_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lead_time({"lead_time_weeks": 1})
        assert factor.score == 0

    def test_moderate_lead_time_scores_50(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lead_time({"lead_time_weeks": 4})
        assert factor.score == 50
        factor = scorer._score_lead_time({"lead_time_weeks": 8})
        assert factor.score == 50

    def test_long_lead_time_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lead_time({"lead_time_weeks": 12})
        assert factor.score == 100

    def test_boundary_at_2_weeks(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lead_time({"lead_time_weeks": 1.9})
        assert factor.score == 0
        factor = scorer._score_lead_time({"lead_time_weeks": 2})
        assert factor.score == 50

    def test_weight_is_correct(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lead_time({"lead_time_weeks": 1})
        assert factor.weight == 0.20


# ===========================================================================
# Lifecycle scoring
# ===========================================================================


class TestLifecycleScoring:
    def test_active_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "active"})
        assert factor.score == 0

    def test_nrnd_scores_50(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "nrnd"})
        assert factor.score == 50

    def test_eol_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "eol"})
        assert factor.score == 100

    def test_obsolete_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "obsolete"})
        assert factor.score == 100

    def test_unknown_scores_50(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "unknown"})
        assert factor.score == 50

    def test_invalid_lifecycle_defaults_to_unknown(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "foobar"})
        assert factor.score == 50

    def test_weight_is_correct(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_lifecycle({"lifecycle": "active"})
        assert factor.weight == 0.20


# ===========================================================================
# Stock level scoring
# ===========================================================================


class TestStockLevelScoring:
    def test_below_moq_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_stock_level({"stock": 5, "moq": 10})
        assert factor.score == 100

    def test_below_10x_moq_scores_50(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_stock_level({"stock": 50, "moq": 10})
        assert factor.score == 50

    def test_abundant_stock_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_stock_level({"stock": 1000, "moq": 10})
        assert factor.score == 0

    def test_weight_is_correct(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_stock_level({"stock": 1000, "moq": 10})
        assert factor.weight == 0.10


# ===========================================================================
# Compliance scoring
# ===========================================================================


class TestComplianceScoring:
    def test_fully_compliant_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_compliance({"rohs_compliant": True, "reach_compliant": True})
        assert factor.score == 0

    def test_missing_both_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_compliance({"rohs_compliant": False, "reach_compliant": False})
        assert factor.score == 100

    def test_partial_compliance_scores_50(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_compliance({"rohs_compliant": True, "reach_compliant": False})
        assert factor.score == 50


# ===========================================================================
# Price volatility scoring
# ===========================================================================


class TestPriceVolatilityScoring:
    def test_stable_prices_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_price_volatility({"prices": [1.0, 1.0, 1.0]})
        assert factor.score == 0

    def test_volatile_prices_scores_100(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_price_volatility({"prices": [1.0, 5.0, 10.0]})
        assert factor.score == 100

    def test_no_prices_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_price_volatility({"prices": []})
        assert factor.score == 0

    def test_single_price_scores_0(self, scorer: BOMRiskScorer) -> None:
        factor = scorer._score_price_volatility({"prices": [5.0]})
        assert factor.score == 0


# ===========================================================================
# Full part scoring
# ===========================================================================


class TestPartScoring:
    def test_low_risk_part(self, scorer: BOMRiskScorer) -> None:
        part = _make_part()
        result = scorer.score_part(part)
        assert result.risk_level == RiskLevel.LOW
        assert result.overall_score <= 25
        assert result.flagged is False

    def test_critical_risk_part(self, scorer: BOMRiskScorer) -> None:
        part = _make_part(
            num_sources=1,
            lead_time_weeks=12,
            lifecycle="obsolete",
            stock=0,
            moq=100,
            rohs_compliant=False,
            reach_compliant=False,
            prices=[1.0, 5.0, 10.0],
        )
        result = scorer.score_part(part)
        assert result.risk_level == RiskLevel.CRITICAL
        assert result.overall_score >= 76
        assert result.flagged is True

    def test_part_has_six_factors(self, scorer: BOMRiskScorer) -> None:
        result = scorer.score_part(_make_part())
        assert len(result.factors) == 6

    def test_mpn_and_manufacturer_propagated(self, scorer: BOMRiskScorer) -> None:
        result = scorer.score_part(_make_part(mpn="ABC-123", manufacturer="ACME"))
        assert result.mpn == "ABC-123"
        assert result.manufacturer == "ACME"

    def test_score_clamped_0_to_100(self, scorer: BOMRiskScorer) -> None:
        result = scorer.score_part(_make_part())
        assert 0 <= result.overall_score <= 100


# ===========================================================================
# Full BOM scoring
# ===========================================================================


class TestBOMScoring:
    def test_empty_bom_scores_0(self, scorer: BOMRiskScorer) -> None:
        report = scorer.score_bom([], project_id="test-project")
        assert report.total_parts == 0
        assert report.overall_score == 0
        assert report.critical_count == 0

    def test_all_low_risk_bom(self, scorer: BOMRiskScorer) -> None:
        parts = [_make_part(mpn=f"PART-{i}") for i in range(5)]
        report = scorer.score_bom(parts, project_id="test")
        assert report.total_parts == 5
        assert report.overall_score <= 25
        assert report.critical_count == 0
        assert report.low_count == 5

    def test_mixed_risk_bom(self, scorer: BOMRiskScorer) -> None:
        parts = [
            _make_part(mpn="LOW-1"),
            _make_part(
                mpn="CRIT-1",
                num_sources=1,
                lead_time_weeks=12,
                lifecycle="obsolete",
                stock=0,
                moq=100,
                rohs_compliant=False,
                reach_compliant=False,
                prices=[1.0, 5.0, 10.0],
            ),
        ]
        report = scorer.score_bom(parts, project_id="mixed")
        assert report.total_parts == 2
        assert report.critical_count >= 1

    def test_bom_with_one_critical_flagged(self, scorer: BOMRiskScorer) -> None:
        parts = [
            _make_part(mpn="SAFE-1"),
            _make_part(
                mpn="RISKY-1",
                num_sources=1,
                lead_time_weeks=20,
                lifecycle="eol",
                rohs_compliant=False,
                reach_compliant=False,
            ),
        ]
        report = scorer.score_bom(parts, project_id="flagged-test")
        flagged = [p for p in report.part_scores if p.flagged]
        assert len(flagged) >= 1

    def test_project_id_propagated(self, scorer: BOMRiskScorer) -> None:
        report = scorer.score_bom([], project_id="my-project")
        assert report.project_id == "my-project"

    def test_counts_add_up(self, scorer: BOMRiskScorer) -> None:
        parts = [_make_part(mpn=f"P-{i}", num_sources=i + 1) for i in range(4)]
        report = scorer.score_bom(parts)
        total = report.critical_count + report.high_count + report.medium_count + report.low_count
        assert total == report.total_parts

    def test_generated_at_present(self, scorer: BOMRiskScorer) -> None:
        report = scorer.score_bom([])
        assert report.generated_at is not None


# ===========================================================================
# Alternate parts finder
# ===========================================================================


class TestAlternatePartsFinder:
    def test_no_candidates_returns_empty(self, finder: AlternatePartsFinder) -> None:
        result = finder.find_alternates("TEST-001", {"package": "SOIC-8"}, [])
        assert len(result.alternates) == 0
        assert "No suitable alternates" in result.recommendation

    def test_same_mpn_excluded(self, finder: AlternatePartsFinder) -> None:
        candidates = [
            {"mpn": "TEST-001", "package": "SOIC-8", "manufacturer": "A", "stock": 100},
        ]
        result = finder.find_alternates("TEST-001", {"package": "SOIC-8"}, candidates)
        assert len(result.alternates) == 0

    def test_incompatible_package_excluded(self, finder: AlternatePartsFinder) -> None:
        candidates = [
            {"mpn": "ALT-001", "package": "QFN-16", "manufacturer": "B", "stock": 100},
        ]
        result = finder.find_alternates("TEST-001", {"package": "SOIC-8"}, candidates)
        assert len(result.alternates) == 0

    def test_compatible_alternate_found(self, finder: AlternatePartsFinder) -> None:
        candidates = [
            {
                "mpn": "ALT-001",
                "package": "SOIC-8",
                "manufacturer": "AltCorp",
                "stock": 5000,
                "price": 0.95,
                "lead_time_weeks": 1,
                "num_sources": 3,
                "lifecycle": "active",
                "rohs_compliant": True,
                "reach_compliant": True,
            },
        ]
        result = finder.find_alternates(
            "TEST-001",
            {"package": "SOIC-8", "price": 1.0},
            candidates,
        )
        assert len(result.alternates) == 1
        assert result.alternates[0].mpn == "ALT-001"

    def test_max_3_alternates_returned(self, finder: AlternatePartsFinder) -> None:
        candidates = [
            {
                "mpn": f"ALT-{i}",
                "package": "SOIC-8",
                "manufacturer": f"Corp{i}",
                "stock": 1000 * (i + 1),
                "price": 1.0,
                "lead_time_weeks": 1,
                "num_sources": 3,
                "lifecycle": "active",
                "rohs_compliant": True,
                "reach_compliant": True,
            }
            for i in range(5)
        ]
        result = finder.find_alternates(
            "TEST-001",
            {"package": "SOIC-8", "price": 1.0},
            candidates,
        )
        assert len(result.alternates) <= 3

    def test_alternates_ranked_by_composite_score(self, finder: AlternatePartsFinder) -> None:
        # One with good availability, one with bad
        candidates = [
            {
                "mpn": "BAD-STOCK",
                "package": "SOIC-8",
                "manufacturer": "B",
                "stock": 0,
                "price": 2.0,
                "lead_time_weeks": 12,
                "num_sources": 1,
                "lifecycle": "eol",
                "rohs_compliant": False,
                "reach_compliant": False,
            },
            {
                "mpn": "GOOD-ALT",
                "package": "SOIC-8",
                "manufacturer": "A",
                "stock": 10000,
                "price": 0.8,
                "lead_time_weeks": 1,
                "num_sources": 5,
                "lifecycle": "active",
                "rohs_compliant": True,
                "reach_compliant": True,
            },
        ]
        result = finder.find_alternates(
            "TEST-001",
            {"package": "SOIC-8", "price": 1.0},
            candidates,
        )
        assert len(result.alternates) >= 1
        assert result.alternates[0].mpn == "GOOD-ALT"

    def test_original_risk_score_in_result(self, finder: AlternatePartsFinder) -> None:
        result = finder.find_alternates("TEST-001", {"package": "SOIC-8"}, [])
        assert isinstance(result.original_risk_score, int)

    def test_compatibility_score_is_int(self, finder: AlternatePartsFinder) -> None:
        candidates = [
            {
                "mpn": "ALT-001",
                "package": "SOIC-8",
                "manufacturer": "X",
                "stock": 100,
                "num_sources": 3,
                "lifecycle": "active",
                "rohs_compliant": True,
                "reach_compliant": True,
            },
        ]
        result = finder.find_alternates("TEST-001", {"package": "SOIC-8"}, candidates)
        if result.alternates:
            assert isinstance(result.alternates[0].compatibility_score, int)


# ===========================================================================
# Skill handler execution (score_bom_risk)
# ===========================================================================


class TestScoreBomRiskHandler:
    def test_handler_executes_successfully(self) -> None:
        from domain_agents.supply_chain.skills.score_bom_risk.handler import (
            ScoreBomRiskHandler,
        )
        from domain_agents.supply_chain.skills.score_bom_risk.schema import (
            BOMItem,
            ScoreBomRiskInput,
        )
        from skill_registry.skill_base import SkillContext

        ctx = SkillContext(
            twin=MagicMock(),
            mcp=MagicMock(),
            logger=MagicMock(),
            session_id=uuid4(),
        )
        handler = ScoreBomRiskHandler(ctx)
        skill_input = ScoreBomRiskInput(
            project_id="test-proj",
            bom_items=[
                BOMItem(
                    mpn="R-100K",
                    manufacturer="Yageo",
                    quantity=10,
                    distributor_data={
                        "num_sources": 3,
                        "lead_time_weeks": 1,
                        "lifecycle": "active",
                        "stock": 50000,
                        "moq": 100,
                        "rohs_compliant": True,
                        "reach_compliant": True,
                    },
                ),
            ],
        )

        result = asyncio.get_event_loop().run_until_complete(handler.run(skill_input))
        assert result.success
        assert result.data is not None
        assert result.data.report.total_parts == 1


class TestFindAlternatesHandler:
    def test_handler_executes_successfully(self) -> None:
        from domain_agents.supply_chain.skills.find_alternates.handler import (
            FindAlternatesHandler,
        )
        from domain_agents.supply_chain.skills.find_alternates.schema import (
            FindAlternatesInput,
        )
        from skill_registry.skill_base import SkillContext

        ctx = SkillContext(
            twin=MagicMock(),
            mcp=MagicMock(),
            logger=MagicMock(),
            session_id=uuid4(),
        )
        handler = FindAlternatesHandler(ctx)
        skill_input = FindAlternatesInput(
            mpn="TEST-001",
            specs={"package": "SOIC-8"},
            distributor_results=[
                {
                    "mpn": "ALT-001",
                    "package": "SOIC-8",
                    "manufacturer": "AltCorp",
                    "stock": 5000,
                    "num_sources": 3,
                    "lifecycle": "active",
                    "rohs_compliant": True,
                    "reach_compliant": True,
                },
            ],
        )

        result = asyncio.get_event_loop().run_until_complete(handler.run(skill_input))
        assert result.success
        assert result.data is not None
        assert len(result.data.result.alternates) == 1


# ===========================================================================
# Agent hardcoded dispatch
# ===========================================================================


class TestSupplyChainAgentDispatch:
    def test_unsupported_task_type_returns_error(self) -> None:
        from domain_agents.supply_chain.agent import (
            SupplyChainAgent,
            TaskRequest,
        )

        agent = SupplyChainAgent(
            twin=MagicMock(),
            mcp=MagicMock(),
        )
        request = TaskRequest(task_type="unknown_task")
        result = asyncio.get_event_loop().run_until_complete(agent.run_task(request))
        assert not result.success
        assert "Unsupported task type" in result.errors[0]

    def test_score_bom_risk_dispatch(self) -> None:
        from domain_agents.supply_chain.agent import (
            SupplyChainAgent,
            TaskRequest,
        )

        agent = SupplyChainAgent(
            twin=MagicMock(),
            mcp=MagicMock(),
        )
        request = TaskRequest(
            task_type="score_bom_risk",
            parameters={
                "project_id": "test",
                "bom_items": [
                    {
                        "mpn": "CAP-100N",
                        "manufacturer": "Murata",
                        "quantity": 5,
                        "distributor_data": {
                            "num_sources": 4,
                            "lead_time_weeks": 1,
                            "lifecycle": "active",
                            "stock": 100000,
                            "moq": 10,
                            "rohs_compliant": True,
                            "reach_compliant": True,
                        },
                    }
                ],
            },
        )
        result = asyncio.get_event_loop().run_until_complete(agent.run_task(request))
        assert result.success
        assert len(result.skill_results) == 1

    def test_find_alternates_dispatch(self) -> None:
        from domain_agents.supply_chain.agent import (
            SupplyChainAgent,
            TaskRequest,
        )

        agent = SupplyChainAgent(
            twin=MagicMock(),
            mcp=MagicMock(),
        )
        request = TaskRequest(
            task_type="find_alternates",
            parameters={
                "mpn": "IC-REG-3V3",
                "specs": {"package": "SOT-223"},
                "distributor_results": [],
            },
        )
        result = asyncio.get_event_loop().run_until_complete(agent.run_task(request))
        assert result.success

    def test_missing_bom_items_returns_error(self) -> None:
        from domain_agents.supply_chain.agent import (
            SupplyChainAgent,
            TaskRequest,
        )

        agent = SupplyChainAgent(
            twin=MagicMock(),
            mcp=MagicMock(),
        )
        request = TaskRequest(
            task_type="score_bom_risk",
            parameters={"project_id": "test"},
        )
        result = asyncio.get_event_loop().run_until_complete(agent.run_task(request))
        assert not result.success
        assert "bom_items" in result.errors[0]

    def test_missing_mpn_returns_error(self) -> None:
        from domain_agents.supply_chain.agent import (
            SupplyChainAgent,
            TaskRequest,
        )

        agent = SupplyChainAgent(
            twin=MagicMock(),
            mcp=MagicMock(),
        )
        request = TaskRequest(
            task_type="find_alternates",
            parameters={"specs": {}},
        )
        result = asyncio.get_event_loop().run_until_complete(agent.run_task(request))
        assert not result.success
        assert "mpn" in result.errors[0]


# ===========================================================================
# Model validation
# ===========================================================================


class TestModels:
    def test_lifecycle_status_values(self) -> None:
        assert LifecycleStatus.ACTIVE == "active"
        assert LifecycleStatus.NRND == "nrnd"
        assert LifecycleStatus.EOL == "eol"
        assert LifecycleStatus.OBSOLETE == "obsolete"
        assert LifecycleStatus.UNKNOWN == "unknown"

    def test_risk_factor_validation(self) -> None:
        rf = RiskFactor(name="test", weight=0.5, score=75, description="desc")
        assert rf.weight == 0.5
        assert rf.score == 75

    def test_part_risk_score_model(self) -> None:
        prs = PartRiskScore(
            mpn="X",
            overall_score=50,
            risk_level=RiskLevel.MEDIUM,
        )
        assert prs.flagged is False
        assert prs.factors == []

    def test_bom_risk_report_defaults(self) -> None:
        report = BOMRiskReport(project_id="test")
        assert report.total_parts == 0
        assert report.overall_score == 0
