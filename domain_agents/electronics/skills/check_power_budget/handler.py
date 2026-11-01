"""Handler for the check_power_budget skill (FORGE-544)."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import CheckPowerBudgetInput, CheckPowerBudgetOutput


class CheckPowerBudgetHandler(SkillBase[CheckPowerBudgetInput, CheckPowerBudgetOutput]):
    """Checks each supply rail's worst-case load against its derated rating.

    The arithmetic is the ``power.check_budget`` MCP tool's; this skill
    passes the rails, loads and derating through and summarises the verdict.
    Both electronics agents used to answer "not yet implemented".
    """

    input_type = CheckPowerBudgetInput
    output_type = CheckPowerBudgetOutput

    async def validate_preconditions(self, input_data: CheckPowerBudgetInput) -> list[str]:
        if not await self.context.mcp.is_available("power.check_budget"):
            return ["power.check_budget tool is not available"]
        return []

    async def execute(self, input_data: CheckPowerBudgetInput) -> CheckPowerBudgetOutput:
        self.logger.info(
            "Checking power budget",
            rails=len(input_data.rails),
            loads=len(input_data.loads),
            derating=input_data.derating,
        )
        result: dict[str, Any] = await self.context.mcp.invoke(
            "power.check_budget",
            {
                "rails": input_data.rails,
                "loads": input_data.loads,
                "derating": input_data.derating,
            },
            timeout=30,
        )
        rails = list(result.get("rails", []))
        return CheckPowerBudgetOutput(
            work_product_id=input_data.work_product_id,
            verdict=result["verdict"],
            passed=bool(result["passed"]),
            derating=float(result.get("derating", input_data.derating)),
            rails=rails,
            worst_rail=result.get("worst_rail"),
            source_power_mw=result.get("source_power_mw"),
            summary=_summary(result["verdict"], rails, result.get("worst_rail")),
        )

    async def validate_output(self, output: CheckPowerBudgetOutput) -> list[str]:
        if output.passed != (output.verdict == "pass"):
            return [f"passed={output.passed} disagrees with verdict={output.verdict}"]
        return []


def _summary(verdict: str, rails: list[dict[str, Any]], worst: str | None) -> str:
    failing = [r["name"] for r in rails if r.get("status") == "fail"]
    open_ = [r["name"] for r in rails if r.get("status") == "not_established"]
    parts = [f"Power budget {verdict.upper().replace('_', ' ')} over {len(rails)} rail(s)."]
    if failing:
        parts.append(f"Over budget: {', '.join(failing)}.")
    if open_:
        parts.append(f"Not established (unknown figures): {', '.join(open_)}.")
    if worst:
        parts.append(f"Worst rail: {worst}.")
    return " ".join(parts)
