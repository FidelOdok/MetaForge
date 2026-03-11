"""BOM risk scoring engine.

Evaluates supply chain risk for individual parts and complete BOMs
using weighted risk factors: single-source, lead time, lifecycle,
price volatility, stock level, and compliance gaps.
"""

from __future__ import annotations

import math
from typing import Any

import structlog

from observability.tracing import get_tracer

from .models import (
    BOMRiskReport,
    LifecycleStatus,
    PartRiskScore,
    RiskFactor,
    RiskLevel,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.supply_chain.risk_scorer")


def _classify_risk_level(score: int) -> RiskLevel:
    """Classify a numeric score into a risk level.

    Thresholds:
        0-25  = low
        26-50 = medium
        51-75 = high
        76-100 = critical
    """
    if score <= 25:
        return RiskLevel.LOW
    elif score <= 50:
        return RiskLevel.MEDIUM
    elif score <= 75:
        return RiskLevel.HIGH
    else:
        return RiskLevel.CRITICAL


class BOMRiskScorer:
    """Scores supply chain risk for parts and BOMs.

    Risk factors and their weights:
        - Single-source:     0.25
        - Lead time:         0.20
        - Lifecycle:         0.20
        - Price volatility:  0.15
        - Stock level:       0.10
        - Compliance gaps:   0.10
    """

    # Factor weights
    WEIGHT_SINGLE_SOURCE = 0.25
    WEIGHT_LEAD_TIME = 0.20
    WEIGHT_LIFECYCLE = 0.20
    WEIGHT_PRICE_VOLATILITY = 0.15
    WEIGHT_STOCK_LEVEL = 0.10
    WEIGHT_COMPLIANCE = 0.10

    def _score_single_source(self, part_data: dict[str, Any]) -> RiskFactor:
        """Score single-source risk based on number of distributors.

        1 distributor  -> 100 (critical)
        2 distributors -> 50  (medium)
        3+ distributors -> 0  (low)
        """
        num_sources = part_data.get("num_sources", 1)
        if isinstance(num_sources, str):
            num_sources = int(num_sources)

        if num_sources <= 1:
            score = 100
            desc = "Single-source part — high supply chain risk"
        elif num_sources == 2:
            score = 50
            desc = "Dual-source — moderate supply chain risk"
        else:
            score = 0
            desc = f"Multi-source ({num_sources} distributors) — low risk"

        return RiskFactor(
            name="single_source",
            weight=self.WEIGHT_SINGLE_SOURCE,
            score=score,
            description=desc,
        )

    def _score_lead_time(self, part_data: dict[str, Any]) -> RiskFactor:
        """Score lead time risk.

        < 2 weeks  -> 0
        2-8 weeks  -> 50
        > 8 weeks  -> 100
        """
        lead_time_weeks = part_data.get("lead_time_weeks", 0)
        if isinstance(lead_time_weeks, str):
            lead_time_weeks = float(lead_time_weeks)

        if lead_time_weeks < 2:
            score = 0
            desc = f"Short lead time ({lead_time_weeks} weeks)"
        elif lead_time_weeks <= 8:
            score = 50
            desc = f"Moderate lead time ({lead_time_weeks} weeks)"
        else:
            score = 100
            desc = f"Long lead time ({lead_time_weeks} weeks) — supply risk"

        return RiskFactor(
            name="lead_time",
            weight=self.WEIGHT_LEAD_TIME,
            score=score,
            description=desc,
        )

    def _score_lifecycle(self, part_data: dict[str, Any]) -> RiskFactor:
        """Score lifecycle risk.

        active   -> 0
        NRND     -> 50
        EOL/obsolete -> 100
        unknown  -> 50
        """
        lifecycle_raw = part_data.get("lifecycle", "unknown")
        try:
            lifecycle = LifecycleStatus(lifecycle_raw.lower())
        except ValueError:
            lifecycle = LifecycleStatus.UNKNOWN

        lifecycle_scores = {
            LifecycleStatus.ACTIVE: 0,
            LifecycleStatus.NRND: 50,
            LifecycleStatus.EOL: 100,
            LifecycleStatus.OBSOLETE: 100,
            LifecycleStatus.UNKNOWN: 50,
        }

        score = lifecycle_scores[lifecycle]
        desc = f"Lifecycle status: {lifecycle.value}"

        return RiskFactor(
            name="lifecycle",
            weight=self.WEIGHT_LIFECYCLE,
            score=score,
            description=desc,
        )

    def _score_price_volatility(self, part_data: dict[str, Any]) -> RiskFactor:
        """Score price volatility based on price std dev across distributors.

        Uses coefficient of variation (std_dev / mean) when prices are available.
        """
        prices = part_data.get("prices", [])
        if not prices or len(prices) < 2:
            return RiskFactor(
                name="price_volatility",
                weight=self.WEIGHT_PRICE_VOLATILITY,
                score=0,
                description="Insufficient price data to assess volatility",
            )

        prices_float = [float(p) for p in prices]
        mean_price = sum(prices_float) / len(prices_float)
        if mean_price <= 0:
            return RiskFactor(
                name="price_volatility",
                weight=self.WEIGHT_PRICE_VOLATILITY,
                score=0,
                description="Invalid price data",
            )

        variance = sum((p - mean_price) ** 2 for p in prices_float) / len(prices_float)
        std_dev = math.sqrt(variance)
        cv = std_dev / mean_price  # Coefficient of variation

        # CV thresholds: < 0.1 -> low, 0.1-0.3 -> medium, > 0.3 -> high
        if cv < 0.1:
            score = 0
            desc = f"Stable pricing (CV={cv:.2f})"
        elif cv < 0.3:
            score = 50
            desc = f"Moderate price volatility (CV={cv:.2f})"
        else:
            score = 100
            desc = f"High price volatility (CV={cv:.2f})"

        return RiskFactor(
            name="price_volatility",
            weight=self.WEIGHT_PRICE_VOLATILITY,
            score=score,
            description=desc,
        )

    def _score_stock_level(self, part_data: dict[str, Any]) -> RiskFactor:
        """Score stock level risk.

        < MOQ       -> 100
        < 10x MOQ   -> 50
        >= 10x MOQ  -> 0
        """
        stock = part_data.get("stock", 0)
        moq = part_data.get("moq", 1)
        if isinstance(stock, str):
            stock = int(stock)
        if isinstance(moq, str):
            moq = int(moq)
        if moq <= 0:
            moq = 1

        if stock < moq:
            score = 100
            desc = f"Stock ({stock}) below MOQ ({moq})"
        elif stock < 10 * moq:
            score = 50
            desc = f"Limited stock ({stock}), less than 10x MOQ ({moq})"
        else:
            score = 0
            desc = f"Abundant stock ({stock})"

        return RiskFactor(
            name="stock_level",
            weight=self.WEIGHT_STOCK_LEVEL,
            score=score,
            description=desc,
        )

    def _score_compliance(self, part_data: dict[str, Any]) -> RiskFactor:
        """Score compliance gaps.

        Missing RoHS or REACH -> 100
        All present -> 0
        """
        has_rohs = part_data.get("rohs_compliant", False)
        has_reach = part_data.get("reach_compliant", False)

        if has_rohs and has_reach:
            score = 0
            desc = "RoHS and REACH compliant"
        elif has_rohs or has_reach:
            score = 50
            missing = "REACH" if has_rohs else "RoHS"
            desc = f"Missing {missing} compliance"
        else:
            score = 100
            desc = "Missing RoHS and REACH compliance"

        return RiskFactor(
            name="compliance",
            weight=self.WEIGHT_COMPLIANCE,
            score=score,
            description=desc,
        )

    def score_part(self, part_data: dict[str, Any]) -> PartRiskScore:
        """Score risk for a single part.

        Args:
            part_data: Dictionary with part information including:
                - mpn: Manufacturer Part Number
                - manufacturer: Part manufacturer
                - num_sources: Number of distributors
                - lead_time_weeks: Lead time in weeks
                - lifecycle: Lifecycle status string
                - prices: List of prices from different sources
                - stock: Current stock quantity
                - moq: Minimum order quantity
                - rohs_compliant: bool
                - reach_compliant: bool

        Returns:
            PartRiskScore with overall score and individual factors.
        """
        with tracer.start_as_current_span("score_part") as span:
            mpn = part_data.get("mpn", "UNKNOWN")
            manufacturer = part_data.get("manufacturer", "")
            span.set_attribute("part.mpn", mpn)

            factors = [
                self._score_single_source(part_data),
                self._score_lead_time(part_data),
                self._score_lifecycle(part_data),
                self._score_price_volatility(part_data),
                self._score_stock_level(part_data),
                self._score_compliance(part_data),
            ]

            # Weighted average
            weighted_sum = sum(f.weight * f.score for f in factors)
            total_weight = sum(f.weight for f in factors)
            overall_score = round(weighted_sum / total_weight) if total_weight > 0 else 0
            overall_score = max(0, min(100, overall_score))

            risk_level = _classify_risk_level(overall_score)
            flagged = risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

            logger.info(
                "Part risk scored",
                mpn=mpn,
                overall_score=overall_score,
                risk_level=risk_level.value,
                flagged=flagged,
            )

            return PartRiskScore(
                mpn=mpn,
                manufacturer=manufacturer,
                overall_score=overall_score,
                risk_level=risk_level,
                factors=factors,
                flagged=flagged,
            )

    def score_bom(self, parts: list[dict[str, Any]], project_id: str = "") -> BOMRiskReport:
        """Score risk for an entire BOM.

        Args:
            parts: List of part data dictionaries.
            project_id: Project identifier.

        Returns:
            BOMRiskReport with overall and per-part scores.
        """
        with tracer.start_as_current_span("score_bom") as span:
            span.set_attribute("bom.total_parts", len(parts))

            part_scores: list[PartRiskScore] = []
            for part_data in parts:
                part_score = self.score_part(part_data)
                part_scores.append(part_score)

            total_parts = len(part_scores)

            # Count by risk level
            critical_count = sum(1 for p in part_scores if p.risk_level == RiskLevel.CRITICAL)
            high_count = sum(1 for p in part_scores if p.risk_level == RiskLevel.HIGH)
            medium_count = sum(1 for p in part_scores if p.risk_level == RiskLevel.MEDIUM)
            low_count = sum(1 for p in part_scores if p.risk_level == RiskLevel.LOW)

            # Overall BOM score = average of part scores (0 if empty)
            if total_parts > 0:
                overall_score = round(sum(p.overall_score for p in part_scores) / total_parts)
            else:
                overall_score = 0

            overall_score = max(0, min(100, overall_score))

            logger.info(
                "BOM risk scored",
                project_id=project_id,
                total_parts=total_parts,
                overall_score=overall_score,
                critical=critical_count,
                high=high_count,
            )

            return BOMRiskReport(
                project_id=project_id,
                total_parts=total_parts,
                overall_score=overall_score,
                critical_count=critical_count,
                high_count=high_count,
                medium_count=medium_count,
                low_count=low_count,
                part_scores=part_scores,
            )
