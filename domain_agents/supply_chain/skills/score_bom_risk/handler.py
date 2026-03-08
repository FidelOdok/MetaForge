"""Handler for the score_bom_risk skill."""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import BOMItem, ScoreBomRiskInput, ScoreBomRiskOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.supply_chain.skills.score_bom_risk")


class ScoreBomRiskHandler(SkillBase[ScoreBomRiskInput, ScoreBomRiskOutput]):
    """Scores supply chain risk for a BOM."""

    input_type = ScoreBomRiskInput
    output_type = ScoreBomRiskOutput

    async def execute(self, input_data: ScoreBomRiskInput) -> ScoreBomRiskOutput:
        """Score risk for all BOM items."""
        from domain_agents.supply_chain.risk_scorer import BOMRiskScorer

        with tracer.start_as_current_span("score_bom_risk.execute") as span:
            span.set_attribute("project.id", input_data.project_id)
            span.set_attribute("bom.items_count", len(input_data.bom_items))

            scorer = BOMRiskScorer()

            # Convert BOM items to part data dicts for the scorer
            parts_data: list[dict[str, Any]] = []
            for item in input_data.bom_items:
                part_data: dict[str, Any] = {
                    "mpn": item.mpn,
                    "manufacturer": item.manufacturer,
                    "quantity": item.quantity,
                    "description": item.description,
                }
                # Merge distributor data if available
                if item.distributor_data:
                    part_data.update(item.distributor_data)

                parts_data.append(part_data)

            report = scorer.score_bom(parts_data, project_id=input_data.project_id)

            logger.info(
                "BOM risk scoring complete",
                project_id=input_data.project_id,
                total_parts=report.total_parts,
                overall_score=report.overall_score,
            )

            return ScoreBomRiskOutput(report=report)
