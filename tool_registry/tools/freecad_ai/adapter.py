"""FreeCAD AI MCP adapter (MET-525 / MET-526).

Fronts the external FreeCAD AI authoring + kinematics toolset (PartDesign
modelling, assemblies with real joints, parametric expressions, DFM skills) as a
MetaForge MCP adapter, so agents and the dashboard reach it through the same
tool registry, schema validation, and digital-thread capture as every other
tool.

The **wire transport** to the FreeCAD AI server is *injected* — the adapter
forwards calls opaquely and is decoupled from whether the server speaks HTTP,
stdio, or MCP. When no transport is configured every tool fails cleanly with a
"not configured" error (the same shape as an adapter being down), so the
adapter is safe to register unconditionally.

Transport contract (what a FreeCAD AI backend must honour for ``http_transport``):
    POST  {base_url}/tool/call   JSON {"tool": <name>, "arguments": {...}}
      ->  200  JSON  (the tool result object, returned to the caller as-is)

This is the foundational slice (MET-526): a curated tool surface + the seam.
The full surface and request/response schema mapping are MET-527; the stateful
FreeCAD session lifecycle is MET-528.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from tool_registry.mcp_server.handlers import ResourceLimits, ToolHandler, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)

_ADAPTER = "freecad_ai"

# (tool_name, arguments) -> result dict. Injected; the adapter never assumes a
# concrete wire format.
FreecadAiTransport = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# Curated surface for MET-526: authoring + assembly/joints + parametric +
# inspection + export — the tools that matter for the Tier-2/3 solver and the
# stub-replacement goals. Full ~45-tool surface is MET-527.
#   (tool_name, description, capability)
_TOOLS: tuple[tuple[str, str, str], ...] = (
    (
        "create_primitive",
        "Create a primitive solid (box, cylinder, sphere, cone, torus)",
        "cad_author",
    ),
    ("create_body", "Create a PartDesign Body for parametric modelling", "cad_author"),
    (
        "create_sketch",
        "Create a sketch (lines/circles/arcs/rectangles + constraints)",
        "cad_author",
    ),
    ("pad_sketch", "Extrude a sketch into a solid", "cad_author"),
    ("pocket_sketch", "Cut a pocket from a sketch", "cad_author"),
    ("revolve_sketch", "Revolve a sketch around an axis", "cad_author"),
    ("boolean_operation", "Fuse / Cut / Common between two objects", "cad_author"),
    ("transform_object", "Move and/or rotate an object", "cad_author"),
    ("fillet_edges", "Round edges", "cad_author"),
    ("measure", "Volume, area, bounding box, distance, edge listing", "cad_inspect"),
    ("describe_model", "Geometry summary: dimensions, wall thickness, hollow/solid", "cad_inspect"),
    ("create_variable_set", "Create a VarSet of named parametric variables", "cad_parametric"),
    (
        "set_expression",
        "Bind an object property to an expression (parametric link)",
        "cad_parametric",
    ),
    ("create_assembly", "Create an Assembly with a grounded base part", "cad_assembly"),
    ("add_part_to_assembly", "Add a part to an existing assembly", "cad_assembly"),
    (
        "add_assembly_joint",
        "Joint two parts (Fixed/Revolute/Cylindrical/Slider/Ball)",
        "cad_assembly",
    ),
    ("export_model", "Export the model to STEP / STL / IGES", "cad_export"),
)


class FreecadAiServer(McpToolServer):
    """MCP adapter that forwards a curated FreeCAD AI tool surface over an
    injected transport. Unconfigured transport → every tool errors cleanly."""

    def __init__(self, transport: FreecadAiTransport | None = None, version: str = "0.1.0") -> None:
        super().__init__(adapter_id=_ADAPTER, version=version)
        self._transport = transport
        for name, description, capability in _TOOLS:
            self.register_tool(
                manifest=ToolManifest(
                    tool_id=f"{_ADAPTER}.{name}",
                    adapter_id=_ADAPTER,
                    name=name.replace("_", " ").title(),
                    description=description,
                    capability=capability,
                    # Arguments are forwarded opaquely; the FreeCAD AI server
                    # owns the authoritative per-tool schema (mapping = MET-527).
                    input_schema={"type": "object", "additionalProperties": True},
                    phase=2,
                    resource_limits=ResourceLimits(max_cpu_seconds=120),
                ),
                handler=self._make_handler(name),
            )

    def _make_handler(self, tool_name: str) -> ToolHandler:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            return await self._forward(tool_name, arguments)

        return handler

    async def _forward(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._transport is None:
            # Surfaced as -32001 by the MCP dispatcher — mirrors adapter-down.
            raise RuntimeError(
                "FreeCAD AI backend not configured (set FREECAD_AI_URL); "
                "see MET-526 for the transport contract"
            )
        result = await self._transport(tool_name, arguments)
        logger.info("freecad_ai_forwarded", tool=tool_name)
        return result


def http_transport(base_url: str, api_key: str | None = None) -> FreecadAiTransport:
    """Build an HTTP transport honouring the documented contract:
    ``POST {base_url}/tool/call`` with ``{"tool", "arguments"}`` → JSON result."""
    url = base_url.rstrip("/") + "/tool/call"
    headers = {"X-API-Key": api_key} if api_key else {}

    async def transport(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import httpx  # lazy — only needed when a real transport is wired

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url, json={"tool": tool_name, "arguments": arguments}, headers=headers
            )
            resp.raise_for_status()
            data: Any = resp.json()
            return data if isinstance(data, dict) else {"result": data}

    return transport


def create_freecad_ai_server() -> FreecadAiServer | None:
    """Build the adapter from the environment (opt-in, like freecad/kicad).

    Returns ``None`` when ``FREECAD_AI_URL`` is unset, so callers register the
    adapter only when a backend is available.
    """
    base_url = os.environ.get("FREECAD_AI_URL")
    if not base_url:
        return None
    transport = http_transport(base_url, os.environ.get("FREECAD_AI_API_KEY"))
    logger.info("freecad_ai_adapter_configured", base_url=base_url)
    return FreecadAiServer(transport=transport)
