"""Handler for the record_compliance_checklist skill."""

from __future__ import annotations

from pathlib import Path

from domain_agents.compliance.checklist_generator import ChecklistGenerator
from skill_registry.skill_base import SkillBase

from .schema import RecordComplianceChecklistInput, RecordComplianceChecklistOutput

_DEFAULT_REGIMES_DIR = Path(__file__).resolve().parent.parent.parent / "regimes"


class RecordComplianceChecklistHandler(
    SkillBase[RecordComplianceChecklistInput, RecordComplianceChecklistOutput]
):
    """Generates a compliance checklist (same YAML-regime logic as
    generate_checklist) and persists it as a COMPLIANCE_CHECKLIST work
    product via twin.commit_compliance_checklist -- the persisted twin of
    what generate_checklist already computes in-session."""

    input_type = RecordComplianceChecklistInput
    output_type = RecordComplianceChecklistOutput

    def __init__(self, context, regimes_dir: Path | None = None) -> None:  # type: ignore[override]
        super().__init__(context)
        self._regimes_dir = regimes_dir or _DEFAULT_REGIMES_DIR

    async def validate_preconditions(self, input_data: RecordComplianceChecklistInput) -> list[str]:
        errors: list[str] = []
        if not await self.context.mcp.is_available("twin.commit_compliance_checklist"):
            errors.append("twin.commit_compliance_checklist tool is not available")
        return errors

    async def execute(
        self, input_data: RecordComplianceChecklistInput
    ) -> RecordComplianceChecklistOutput:
        self.logger.info(
            "Recording compliance checklist",
            project_id=input_data.project_id,
            markets=[m.value for m in input_data.target_markets],
        )
        generator = ChecklistGenerator()
        generator.load_regimes(self._regimes_dir)
        checklist = generator.generate_checklist(
            project_id=input_data.project_id,
            product_category=input_data.product_category,
            markets=input_data.target_markets,
        )

        result = await self.context.mcp.invoke(
            "twin.commit_compliance_checklist",
            {
                "name": f"{input_data.project_id} Compliance Checklist",
                "target_markets": [m.value for m in checklist.target_markets],
                "items": [i.model_dump(mode="json") for i in checklist.items],
                "coverage_percent": checklist.coverage_percent,
                "project_id": input_data.project_id,
            },
        )
        return RecordComplianceChecklistOutput(
            node_id=result["node_id"],
            target_markets=checklist.target_markets,
            items=checklist.items,
            total_items=checklist.total_items,
            coverage_percent=checklist.coverage_percent,
            generated_at=checklist.generated_at,
        )
