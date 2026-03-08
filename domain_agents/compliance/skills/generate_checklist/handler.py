"""Handler for the generate_checklist skill."""

from __future__ import annotations

from pathlib import Path

from skill_registry.skill_base import SkillBase

from domain_agents.compliance.checklist_generator import ChecklistGenerator

from .schema import GenerateChecklistInput, GenerateChecklistOutput


_DEFAULT_REGIMES_DIR = Path(__file__).resolve().parent.parent.parent / "regimes"


class GenerateChecklistHandler(SkillBase[GenerateChecklistInput, GenerateChecklistOutput]):
    """Generates a compliance checklist from YAML regime definitions."""

    input_type = GenerateChecklistInput
    output_type = GenerateChecklistOutput

    def __init__(self, context, regimes_dir: Path | None = None) -> None:  # type: ignore[override]
        super().__init__(context)
        self._regimes_dir = regimes_dir or _DEFAULT_REGIMES_DIR

    async def execute(self, input_data: GenerateChecklistInput) -> GenerateChecklistOutput:
        """Generate checklist for the requested markets."""
        self.logger.info(
            "Generating compliance checklist",
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

        return GenerateChecklistOutput(
            project_id=checklist.project_id,
            target_markets=checklist.target_markets,
            items=checklist.items,
            total_items=checklist.total_items,
            coverage_percent=checklist.coverage_percent,
            generated_at=checklist.generated_at,
        )
