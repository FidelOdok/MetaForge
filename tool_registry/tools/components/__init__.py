"""Component catalog MCP tool adapter (MET-436).

Exposes ``component.search_parametric`` and ``component.search_intent`` as
MCP tools so agents/CLI/dashboard can reach the parametric component
catalog and the intent-search orchestrator through the standardised wire
protocol.
"""

from tool_registry.tools.components.adapter import ComponentServer

__all__ = ["ComponentServer"]
