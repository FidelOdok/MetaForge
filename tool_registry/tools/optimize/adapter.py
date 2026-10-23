"""MCP adapter for bounded design-variable search (composable flows follow-up).

``optimize.suggest`` is deterministic search arithmetic, in-process like
``power.check_budget``: no container, no credentials. It never evaluates an
objective; the caller evaluates each suggested point with the real analysis
tools and passes the results back (see ``search.py``).
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.optimize.search import SearchRequest, SearchResult, suggest

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.optimize.adapter")


class OptimizeServer(McpToolServer):
    """Serves ``optimize.suggest``."""

    def __init__(self) -> None:
        super().__init__(adapter_id="optimize", version="0.1.0")
        self.register_tool(
            manifest=ToolManifest(
                tool_id="optimize.suggest",
                adapter_id="optimize",
                name="Suggest Next Design Point",
                description=(
                    "Search one bounded design variable for the best value of an objective, "
                    "ask and tell. Give the variable (name, lower, upper, unit, and where the "
                    "bounds come from), the objective name, minimize or maximize, the method "
                    "(golden_section for a unimodal objective, grid otherwise) and every point "
                    "evaluated so far (x, objective, feasible, source). It returns the next x "
                    "to evaluate, or 'converged' with the best point. It never computes an "
                    "objective: evaluate each point with the real analysis tool (thermal, FEA, "
                    "power budget) and call again with the result. A point whose checks fail "
                    "is feasible=false and is never the best."
                ),
                capability="design_optimisation",
                input_schema=_inline(SearchRequest.model_json_schema()),
                output_schema=SearchResult.model_json_schema(),
                phase=1,
                resource_limits=ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1),
            ),
            handler=self.suggest,
        )

    async def suggest(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("optimize.suggest") as span:
            request = SearchRequest.model_validate(arguments)
            span.set_attribute("optimize.method", request.method)
            span.set_attribute("optimize.evaluations", len(request.evaluations))
            result = suggest(request)
            span.set_attribute("optimize.status", result.status)
            logger.info(
                "optimize_suggested",
                variable=request.variable.name,
                method=request.method,
                evaluations=len(request.evaluations),
                status=result.status,
                next_x=result.next_x,
            )
            return result.model_dump(mode="json")


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """``$defs`` inlined: some MCP clients do not resolve ``$ref``."""
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
