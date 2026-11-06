"""Handler for the design_cad_iterative skill.

Starts (or, with ``wait``, runs) the CAD design loop through MCP
(``twin.design_cad_loop``) and reports what it did. The loop itself runs in
the gateway: scripts execute only inside the CadQuery adapter container, the
views are rendered by the render adapter, and the result is a proposal a
person accepts. This handler never accepts anything.
"""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import DesignCadIterativeInput, DesignCadIterativeOutput

TOOL = "twin.design_cad_loop"


class DesignCadIterativeHandler(SkillBase[DesignCadIterativeInput, DesignCadIterativeOutput]):
    """Refine one component's CAD in the self-validating design loop."""

    input_type = DesignCadIterativeInput
    output_type = DesignCadIterativeOutput

    async def validate_preconditions(self, input_data: DesignCadIterativeInput) -> list[str]:
        errors: list[str] = []
        if not await self.context.mcp.is_available(TOOL):
            errors.append(f"{TOOL} tool is not available")
        return errors

    async def execute(self, input_data: DesignCadIterativeInput) -> DesignCadIterativeOutput:
        arguments: dict[str, Any] = {
            k: v
            for k, v in input_data.model_dump(mode="json").items()
            if v not in (None, "", {}) or k in ("wait", "allow_working_values")
        }
        self.logger.info(
            "Starting the CAD design loop",
            project_id=input_data.project_id,
            sketch_key=input_data.sketch_key,
            component_id=input_data.component_id,
            max_iterations=input_data.max_iterations,
        )
        raw = await self.context.mcp.invoke(TOOL, arguments, timeout=900)
        result: dict[str, Any] = raw.get("data", raw) if isinstance(raw, dict) else {}
        iterations = result.get("iterations")
        return DesignCadIterativeOutput(
            job_id=result.get("job_id"),
            status=str(result.get("status") or ""),
            node_id=result.get("node_id"),
            stop_reason=result.get("stop_reason"),
            stop_detail=result.get("stop_detail"),
            iterations=len(iterations) if isinstance(iterations, list) else 0,
            questions=list(result.get("questions") or []),
            result=result.get("result"),
        )
