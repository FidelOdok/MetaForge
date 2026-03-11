"""Handler for the find_alternates skill."""

from __future__ import annotations

import structlog

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillBase

from .schema import FindAlternatesInput, FindAlternatesOutput

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.supply_chain.skills.find_alternates")


class FindAlternatesHandler(SkillBase[FindAlternatesInput, FindAlternatesOutput]):
    """Finds and ranks alternate parts for a given MPN."""

    input_type = FindAlternatesInput
    output_type = FindAlternatesOutput

    async def execute(self, input_data: FindAlternatesInput) -> FindAlternatesOutput:
        """Find alternates for the specified part."""
        from domain_agents.supply_chain.alt_parts import AlternatePartsFinder

        with tracer.start_as_current_span("find_alternates.execute") as span:
            span.set_attribute("part.mpn", input_data.mpn)
            span.set_attribute("candidates.count", len(input_data.distributor_results))

            finder = AlternatePartsFinder()
            result = finder.find_alternates(
                mpn=input_data.mpn,
                specs=input_data.specs,
                distributor_results=input_data.distributor_results,
            )

            logger.info(
                "Alternates search complete",
                mpn=input_data.mpn,
                alternates_found=len(result.alternates),
            )

            return FindAlternatesOutput(result=result)
