"""MCP adapter for fixed-wing aircraft arithmetic (FORGE-670).

``aircraft.wing_sizing``, ``aircraft.cruise_performance``,
``aircraft.trim_stability`` and ``aircraft.electric_endurance`` are classical
first-order estimates over figures the caller gives, in-process and
deterministic like ``power.check_budget``: no container, no credentials. Each
figure carries its source; one not given is unknown and the verdict is
``not_established``. A vortex-lattice code or a flight simulation outranks
every one of them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from pydantic import BaseModel

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.aircraft.aero import (
    CruiseRequest,
    CruiseResult,
    EnduranceRequest,
    EnduranceResult,
    TrimRequest,
    TrimResult,
    WingRequest,
    WingResult,
    cruise_performance,
    electric_endurance,
    trim_stability,
    wing_sizing,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.aircraft.adapter")

_LIMITS = ResourceLimits(max_memory_mb=128, max_cpu_seconds=5, max_disk_mb=1)

_FIGURES = "Every figure is {value, source}; one not given is unknown: not_established."


class AircraftServer(McpToolServer):
    """Serves the four ``aircraft.*`` estimates."""

    def __init__(self) -> None:
        super().__init__(adapter_id="aircraft", version="0.1.0")
        self._tool(
            "aircraft.wing_sizing",
            "Size a Wing for its Stall Speed",
            "Wing area that holds the stall speed at the maximum take-off mass "
            "(S = 2 m g / (rho Vs^2 CL_max)), or the stall speed of a chosen area, with the "
            "span and mean chord from the aspect ratio. Give the mass, the stall-speed limit "
            "from the requirements, CL_max from airfoil or flap data, and the air density.",
            "wing_sizing",
            WingRequest,
            WingResult,
            self.wing_sizing,
        )
        self._tool(
            "aircraft.cruise_performance",
            "Cruise Drag Polar and Power",
            "Parabolic drag polar CD = CD0 + CL^2 / (pi e AR) at the cruise lift coefficient: "
            "lift-to-drag, drag, shaft power and electrical power through the propulsive "
            "efficiency, and the best lift-to-drag ratio and its speed. Give the all-up mass, "
            "wing area, aspect ratio, Oswald efficiency, CD0 (drag build-up), air density, "
            "cruise speed and propulsive efficiency. An estimate: a vortex-lattice or panel "
            "code outranks it.",
            "aerodynamic_analysis",
            CruiseRequest,
            CruiseResult,
            self.cruise_performance,
        )
        self._tool(
            "aircraft.trim_stability",
            "Trim and Static Stability",
            "Stick-fixed neutral point and static margin of a conventional wing and tail "
            "(Helmbold lift slopes, elliptic downwash estimate), and the tail lift coefficient "
            "that trims the cruise condition against the tail's usable CL. Give both surfaces' "
            "aspect ratios and section lift slopes, the tail volume coefficient and efficiency, "
            "the wing a.c. and CG positions (fraction of MAC), the wing Cm_ac, the cruise CL "
            "and the required static margin. Fuselage contributions are neglected; an AVL "
            "eigenmode analysis outranks it.",
            "stability_analysis",
            TrimRequest,
            TrimResult,
            self.trim_stability,
        )
        self._tool(
            "aircraft.electric_endurance",
            "Electric Cruise Endurance",
            "Cruise endurance and range of a battery-electric aircraft: usable battery energy "
            "over the propulsion plus avionics power. With a required endurance it also "
            "returns the capacity that would meet it. Steady cruise only: a mission "
            "simulation outranks it.",
            "endurance_analysis",
            EnduranceRequest,
            EnduranceResult,
            self.electric_endurance,
        )

    def _tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        capability: str,
        request: type[BaseModel],
        result: type[BaseModel],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id=tool_id,
                adapter_id="aircraft",
                name=name,
                description=f"{description} {_FIGURES}",
                capability=capability,
                input_schema=_inline(request.model_json_schema()),
                output_schema=result.model_json_schema(),
                phase=1,
                resource_limits=_LIMITS,
            ),
            handler=handler,
        )

    async def wing_sizing(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("aircraft.wing_sizing") as span:
            result = wing_sizing(WingRequest.model_validate(arguments))
            span.set_attribute("aircraft.verdict", result.verdict)
            logger.info(
                "aircraft_wing_sized",
                verdict=result.verdict,
                wing_area_m2=result.wing_area_m2,
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")

    async def cruise_performance(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("aircraft.cruise_performance") as span:
            result = cruise_performance(CruiseRequest.model_validate(arguments))
            span.set_attribute("aircraft.verdict", result.verdict)
            logger.info(
                "aircraft_cruise_computed",
                verdict=result.verdict,
                lift_to_drag=result.lift_to_drag,
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")

    async def trim_stability(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("aircraft.trim_stability") as span:
            result = trim_stability(TrimRequest.model_validate(arguments))
            span.set_attribute("aircraft.verdict", result.verdict)
            logger.info(
                "aircraft_trim_checked",
                verdict=result.verdict,
                static_margin=result.static_margin,
                unknowns=len(result.unknowns),
            )
            return result.model_dump(mode="json")

    async def electric_endurance(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("aircraft.electric_endurance") as span:
            result = electric_endurance(EnduranceRequest.model_validate(arguments))
            span.set_attribute("aircraft.verdict", result.verdict)
            logger.info(
                "aircraft_endurance_computed",
                verdict=result.verdict,
                endurance_min=result.endurance_min,
                unknowns=len(result.unknowns),
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
