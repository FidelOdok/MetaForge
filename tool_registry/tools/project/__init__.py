"""Project MCP adapter (MET-427).

Exposes ``project.create`` / ``project.list`` / ``project.get`` over
MCP so Claude Code, IDE plugins, and UAT harnesses can manage
projects without going through the gateway HTTP API.
"""

from tool_registry.tools.project.adapter import ProjectServer

__all__ = ["ProjectServer"]
