"""Alternate parts finder engine.

Searches for and ranks alternate parts based on compatibility,
availability, price, and risk reduction.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer

from .models import AlternatePart, AlternatePartsResult
from .risk_scorer import BOMRiskScorer

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.supply_chain.alt_parts")


class AlternatePartsFinder:
    """Finds and ranks alternate parts for supply chain risk mitigation.

    Matching criteria:
        - Same package (required)
        - Same or better specs (required)
        - Different manufacturer (preferred)

    Ranking weights:
        - Compatibility: 40%
        - Availability:  30%
        - Price:         20%
        - Risk reduction: 10%
    """

    WEIGHT_COMPATIBILITY = 0.40
    WEIGHT_AVAILABILITY = 0.30
    WEIGHT_PRICE = 0.20
    WEIGHT_RISK_REDUCTION = 0.10

    MAX_ALTERNATES = 3

    def __init__(self) -> None:
        self._scorer = BOMRiskScorer()

    def _score_compatibility(
        self, candidate: dict[str, Any], specs: dict[str, Any]
    ) -> int:
        """Score how compatible a candidate is with the original specs.

        Returns 0-100 where 100 = perfect match.
        """
        score = 100

        # Package must match (required)
        orig_package = specs.get("package", "").lower()
        cand_package = candidate.get("package", "").lower()
        if orig_package and cand_package and orig_package != cand_package:
            return 0  # Incompatible package

        # Check key spec parameters
        spec_keys = ["voltage_rating", "current_rating", "capacitance",
                      "resistance", "tolerance", "temperature_range"]
        checked = 0
        matched = 0
        for key in spec_keys:
            if key in specs:
                checked += 1
                orig_val = specs[key]
                cand_val = candidate.get(key)
                if cand_val is not None:
                    # For numeric specs, candidate should meet or exceed
                    try:
                        if float(cand_val) >= float(orig_val):
                            matched += 1
                        else:
                            score -= 15  # Below spec
                    except (ValueError, TypeError):
                        if str(cand_val).lower() == str(orig_val).lower():
                            matched += 1
                        else:
                            score -= 10
                else:
                    score -= 5  # Missing spec data

        # Different manufacturer is preferred
        orig_mfr = specs.get("manufacturer", "").lower()
        cand_mfr = candidate.get("manufacturer", "").lower()
        if orig_mfr and cand_mfr and orig_mfr != cand_mfr:
            score = min(score + 5, 100)  # Slight bonus for diversification

        return max(0, min(100, score))

    def _score_availability(self, candidate: dict[str, Any]) -> int:
        """Score candidate availability (0-100 where 100 = best)."""
        stock = candidate.get("stock", 0)
        if isinstance(stock, str):
            stock = int(stock) if stock.isdigit() else 0
        lead_time = candidate.get("lead_time_weeks", 0)
        if isinstance(lead_time, str):
            lead_time = float(lead_time)

        score = 100
        if stock <= 0:
            score -= 60
        elif stock < 100:
            score -= 30
        elif stock < 1000:
            score -= 10

        if lead_time > 8:
            score -= 40
        elif lead_time > 2:
            score -= 20

        return max(0, min(100, score))

    def _score_price(self, candidate: dict[str, Any], original_price: float) -> int:
        """Score price competitiveness (0-100, higher = better/cheaper)."""
        cand_price = candidate.get("price", 0)
        if isinstance(cand_price, str):
            try:
                cand_price = float(cand_price)
            except ValueError:
                return 50  # Unknown

        if original_price <= 0 or cand_price <= 0:
            return 50  # Can't compare

        ratio = cand_price / original_price
        if ratio <= 0.8:
            return 100  # Significantly cheaper
        elif ratio <= 1.0:
            return 80  # Cheaper or same
        elif ratio <= 1.2:
            return 60  # Slightly more expensive
        elif ratio <= 1.5:
            return 40  # Moderately more expensive
        else:
            return 20  # Significantly more expensive

    def _price_comparison_label(self, candidate: dict[str, Any], original_price: float) -> str:
        """Generate human-readable price comparison."""
        cand_price = candidate.get("price", 0)
        try:
            cand_price = float(cand_price)
        except (ValueError, TypeError):
            return "unknown"

        if original_price <= 0 or cand_price <= 0:
            return "unknown"

        ratio = cand_price / original_price
        if ratio < 0.95:
            return "lower"
        elif ratio <= 1.05:
            return "similar"
        else:
            return "higher"

    def _estimate_risk_reduction(
        self, candidate: dict[str, Any], original_score: int
    ) -> int:
        """Estimate how much the risk score would drop if this alternate is used."""
        candidate_score = self._scorer.score_part(candidate)
        reduction = original_score - candidate_score.overall_score
        return max(0, reduction)

    def find_alternates(
        self,
        mpn: str,
        specs: dict[str, Any],
        distributor_results: list[dict[str, Any]],
    ) -> AlternatePartsResult:
        """Find and rank alternate parts for a given MPN.

        Args:
            mpn: Original manufacturer part number.
            specs: Original part specifications (package, voltage_rating, etc.).
            distributor_results: List of candidate parts from distributor search,
                each a dict with mpn, manufacturer, package, stock, price,
                lead_time_weeks, etc.

        Returns:
            AlternatePartsResult with ranked alternates (top 3).
        """
        with tracer.start_as_current_span("find_alternates") as span:
            span.set_attribute("part.mpn", mpn)
            span.set_attribute("candidates.count", len(distributor_results))

            # Score original part for risk comparison
            original_part_data = dict(specs)
            original_part_data["mpn"] = mpn
            original_score_obj = self._scorer.score_part(original_part_data)
            original_risk_score = original_score_obj.overall_score

            original_price = 0.0
            try:
                original_price = float(specs.get("price", 0))
            except (ValueError, TypeError):
                pass

            # Score and rank candidates
            scored_candidates: list[tuple[float, AlternatePart]] = []

            for candidate in distributor_results:
                cand_mpn = candidate.get("mpn", "")
                if cand_mpn == mpn:
                    continue  # Skip the original part

                compatibility = self._score_compatibility(candidate, specs)
                if compatibility == 0:
                    continue  # Incompatible package, skip

                availability = self._score_availability(candidate)
                price_score = self._score_price(candidate, original_price)
                risk_reduction = self._estimate_risk_reduction(candidate, original_risk_score)

                # Risk reduction score normalized to 0-100
                risk_reduction_score = min(100, risk_reduction * 2)

                # Weighted composite score
                composite = (
                    self.WEIGHT_COMPATIBILITY * compatibility
                    + self.WEIGHT_AVAILABILITY * availability
                    + self.WEIGHT_PRICE * price_score
                    + self.WEIGHT_RISK_REDUCTION * risk_reduction_score
                )

                alt = AlternatePart(
                    mpn=cand_mpn,
                    manufacturer=candidate.get("manufacturer", ""),
                    compatibility_score=compatibility,
                    availability=f"stock: {candidate.get('stock', 'unknown')}",
                    price_comparison=self._price_comparison_label(candidate, original_price),
                    risk_reduction=risk_reduction,
                    notes=candidate.get("notes", ""),
                )

                scored_candidates.append((composite, alt))

            # Sort by composite score descending, take top N
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            top_alternates = [alt for _, alt in scored_candidates[: self.MAX_ALTERNATES]]

            # Generate recommendation
            if not top_alternates:
                recommendation = (
                    f"No suitable alternates found for {mpn}. "
                    "Consider contacting distributors for custom sourcing."
                )
            elif original_risk_score > 50 and top_alternates[0].risk_reduction > 10:
                recommendation = (
                    f"Recommend substituting {mpn} with {top_alternates[0].mpn} "
                    f"to reduce risk by {top_alternates[0].risk_reduction} points."
                )
            else:
                recommendation = (
                    f"Found {len(top_alternates)} alternate(s) for {mpn}. "
                    "Current part is acceptable but alternates are available."
                )

            logger.info(
                "Alternates found",
                mpn=mpn,
                candidates=len(distributor_results),
                alternates=len(top_alternates),
            )

            return AlternatePartsResult(
                original_mpn=mpn,
                original_risk_score=original_risk_score,
                alternates=top_alternates,
                recommendation=recommendation,
            )
