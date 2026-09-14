"""Capability-based CAD kernel backend resolution, shared across mechanical CAD skills.

Replaces the hardcoded, per-skill ``_TOOL_IDS``/``_resolve_backend`` pattern
(previously duplicated in ``generate_cad``) with a single lookup against
``McpBridge.list_tools(capability=...)``. A new kernel adapter (CadQuery,
FreeCAD, or a future third backend) becomes usable by every skill that calls
this helper the moment it registers a tool under one of the shared capability
tags (``cad_generation``, ``cad_operations``, ``cad_analysis``, ``cad_export``,
``cad_scripting``) — no code change here or in the calling skill.
"""

from __future__ import annotations

import structlog

from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)


async def resolve_cad_backend(mcp: McpBridge, capability: str, preferred: str) -> tuple[str, str]:
    """Resolve which CAD backend to use for ``capability``, preferring ``preferred``.

    Discovers candidates via ``mcp.list_tools(capability=...)`` rather than a
    hardcoded map, so any adapter that has registered a tool under this
    capability tag is a valid candidate, including one that didn't exist when
    this function was written. Backend name is derived from each candidate
    tool_id's prefix (e.g. ``"cadquery.execute_script"`` -> ``"cadquery"``).

    Args:
        mcp: Bridge to query for registered tools.
        capability: Capability tag to resolve (e.g. ``"cad_scripting"``).
        preferred: Backend name to prefer if it has registered a tool.

    Returns:
        Tuple of ``(backend_name, tool_id)``.

    Raises:
        RuntimeError: If no adapter has registered a tool under ``capability``.
    """
    candidates = await mcp.list_tools(capability=capability)
    by_backend = {tool["tool_id"].split(".", 1)[0]: tool["tool_id"] for tool in candidates}

    if preferred in by_backend:
        return preferred, by_backend[preferred]

    for backend, tool_id in by_backend.items():
        logger.warning(
            "cad_backend_fallback",
            capability=capability,
            preferred=preferred,
            fallback=backend,
        )
        return backend, tool_id

    raise RuntimeError(
        f"No CAD backend available for capability '{capability}' "
        f"(preferred backend was '{preferred}'; no adapter has registered a tool "
        f"under this capability)."
    )
