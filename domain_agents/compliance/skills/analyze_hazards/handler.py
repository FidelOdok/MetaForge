"""Handler for the analyze_hazards skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import AnalyzeHazardsInput, AnalyzeHazardsOutput

# Mirrors structured_document_recorder.py's risk-scoring thresholds -- kept
# local rather than imported, since domain_agents may not import api_gateway
# (see domain_agents/CLAUDE.md's layer rules).
_RISK_LEVELS = [(20, "critical"), (12, "high"), (6, "medium"), (0, "low")]


def _risk_level(score: int) -> str:
    for threshold, label in _RISK_LEVELS:
        if score >= threshold:
            return label
    return "low"  # pragma: no cover — unreachable, thresholds bottom out at 0


class AnalyzeHazardsHandler(SkillBase[AnalyzeHazardsInput, AnalyzeHazardsOutput]):
    """Persists a hazard/risk log as a HAZARD_ANALYSIS work product via
    twin.commit_hazard_analysis, which does the actual risk-score
    computation and rendering -- this skill validates the input shape and
    triggers that commit."""

    input_type = AnalyzeHazardsInput
    output_type = AnalyzeHazardsOutput

    async def validate_preconditions(self, input_data: AnalyzeHazardsInput) -> list[str]:
        errors: list[str] = []
        if not await self.context.mcp.is_available("twin.commit_hazard_analysis"):
            errors.append("twin.commit_hazard_analysis tool is not available")
        return errors

    async def execute(self, input_data: AnalyzeHazardsInput) -> AnalyzeHazardsOutput:
        self.logger.info(
            "Analyzing hazards",
            project_id=input_data.project_id,
            system_name=input_data.system_name,
            hazard_count=len(input_data.hazards),
        )
        result = await self.context.mcp.invoke(
            "twin.commit_hazard_analysis",
            {
                "name": f"{input_data.system_name} Hazard Analysis",
                "system_name": input_data.system_name,
                "hazards": [h.model_dump() for h in input_data.hazards],
                "project_id": input_data.project_id,
            },
        )
        highest = int(result.get("highest_risk_score", 0))
        return AnalyzeHazardsOutput(
            node_id=result["node_id"],
            hazard_count=int(result.get("hazard_count", len(input_data.hazards))),
            highest_risk_score=highest,
            overall_risk_level=_risk_level(highest),
            unmitigated_count=int(result.get("unmitigated_count", 0)),
        )
