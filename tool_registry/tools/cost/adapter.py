"""MCP adapter for the manufacturing cost estimate (FORGE-615).

``cost.estimate_manufacturing`` is arithmetic over figures the caller gives,
in-process and deterministic like ``power.check_budget``: no container, no
credentials, so it registers on every server unless disabled.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.cost.estimate import EstimateRequest, estimate

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.cost.adapter")


class CostServer(McpToolServer):
    """Serves ``cost.estimate_manufacturing``."""

    def __init__(self) -> None:
        super().__init__(adapter_id="cost", version="0.1.0")
        self.register_tool(
            manifest=ToolManifest(
                tool_id="cost.estimate_manufacturing",
                adapter_id="cost",
                name="Estimate Manufacturing Cost",
                description=(
                    "A rough per-unit and batch cost estimate for CNC milling or sheet metal, "
                    "for ranking routes before a real quote exists. Give quantity, currency "
                    "and each figure with its source: density and price per kg of the "
                    "material, the shop's hourly rate and setup hours; for CNC the stock "
                    "dimensions, part volume (freecad.measure) and material removal rate; "
                    "for sheet metal thickness, blank area, cut length, cut speed, bend count "
                    "and seconds per bend. A figure you do not give leaves what needs it "
                    "unknown; nothing is assumed. Always an estimate, never a price: say so. "
                    "'for_ranking' is ready for rank_manufacturing_processes."
                ),
                capability="cost_estimate",
                input_schema=_input_schema(),
                output_schema={"type": "object"},
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1),
            ),
            handler=self.estimate_manufacturing,
        )

    async def estimate_manufacturing(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("cost.estimate_manufacturing") as span:
            request = EstimateRequest.model_validate(arguments)
            span.set_attribute("cost.process", request.process)
            span.set_attribute("cost.quantity", request.quantity)
            result = estimate(request)
            logger.info(
                "manufacturing_cost_estimated",
                process=request.process,
                quantity=request.quantity,
                unknown=len(result["unknown_inputs"]),
            )
            return result


def _input_schema() -> dict[str, Any]:
    """The request model's schema with ``$defs`` inlined (some clients skip ``$ref``)."""
    schema = EstimateRequest.model_json_schema()
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
