"""Knowledge MCP tool adapter (MET-335).

Exposes ``knowledge.search`` and ``knowledge.ingest`` as MCP tools so
external harnesses (Claude Code, Codex) can reach the L1 knowledge
layer through the standardised wire protocol.
"""

from tool_registry.tools.knowledge.adapter import KnowledgeServer

__all__ = ["KnowledgeServer"]
