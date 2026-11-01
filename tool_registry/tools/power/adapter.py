"""MCP adapter for the power budget calculator (FORGE-544).

``check_power_budget`` was listed as a skill while both electronics agents
answered "not yet implemented", and no tool computed a budget, so a plugin
client did the arithmetic by hand. ``power.check_budget`` is that
arithmetic, in-process and deterministic: it needs no container and no
credentials, so it registers on every server unless disabled.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.power.budget import BudgetRequest, BudgetResult, check_budget
from tool_registry.tools.power.runtime import RuntimeRequest, RuntimeResult, battery_runtime

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.power.adapter")


class PowerServer(McpToolServer):
    """Serves ``power.check_budget`` and ``power.runtime``."""

    def __init__(self) -> None:
        super().__init__(adapter_id="power", version="0.1.0")
        self.register_tool(
            manifest=ToolManifest(
                tool_id="power.check_budget",
                adapter_id="power",
                name="Check Power Budget",
                description=(
                    "Static worst-case power budget per supply rail. Give every rail "
                    "(voltage, source_kind supply/ldo/switching, rated_current_ma, "
                    "input_rail for a regulator, efficiency for a switching one), every "
                    "load (rail, current_ma or power_mw, with its source) and the "
                    "derating rule. Regulator input current is carried upstream. A "
                    "figure you do not give is unknown, never assumed: a rail that "
                    "depends on one is not_established. With each regulator's "
                    "theta_ja_c_per_w and tj_max_c and the ambient_c, it also returns each "
                    "regulator's dissipation and junction temperature and a separate "
                    "thermal_verdict (LDO (Vin - Vout) * I + Vin * Iq; switching "
                    "Pout * (1/eta - 1)). Pure arithmetic; it reads nothing from the design."
                ),
                capability="power_budget",
                input_schema=_input_schema(),
                output_schema=BudgetResult.model_json_schema(),
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1),
            ),
            handler=self.check_budget,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="power.runtime",
                adapter_id="power",
                name="Battery Runtime",
                description=(
                    "Battery runtime from usable energy over the average load at the battery "
                    "terminals (capacity_wh, or capacity_mah with nominal_voltage_v; "
                    "usable_fraction; average_load_mw, e.g. power.check_budget's "
                    "source_power_mw), the rated capacity a required_runtime_h needs, and "
                    "the peak current against the battery's continuous rating. Every figure "
                    "is {value, source}; one not given is unknown and the verdict is "
                    "not_established. Pure arithmetic."
                ),
                capability="battery_runtime",
                input_schema=_inline(RuntimeRequest.model_json_schema()),
                output_schema=RuntimeResult.model_json_schema(),
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1),
            ),
            handler=self.runtime,
        )

    async def check_budget(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("power.check_budget") as span:
            request = BudgetRequest.model_validate(arguments)
            span.set_attribute("power.rails", len(request.rails))
            span.set_attribute("power.loads", len(request.loads))
            result = check_budget(request)
            span.set_attribute("power.verdict", result.verdict)
            logger.info(
                "power_budget_checked",
                rails=len(request.rails),
                loads=len(request.loads),
                verdict=result.verdict,
                worst_rail=result.worst_rail,
            )
            return result.model_dump(mode="json")

    async def runtime(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("power.runtime") as span:
            result = battery_runtime(RuntimeRequest.model_validate(arguments))
            span.set_attribute("power.verdict", result.verdict)
            logger.info(
                "power_runtime_checked",
                verdict=result.verdict,
                runtime_h=result.runtime_h,
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")


def _input_schema() -> dict[str, Any]:
    """The request model's JSON schema with its ``$defs`` inlined.

    Some MCP clients do not resolve ``$ref``; the rail and load shapes are
    small enough to repeat.
    """
    return _inline(BudgetRequest.model_json_schema())


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` with its ``$defs`` inlined."""
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.split("/")[-1]])
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    inlined: dict[str, Any] = inline(schema)
    return inlined
