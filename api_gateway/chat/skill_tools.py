"""Skill-layer tools for the chat harness (MET-548 follow-up).

The harness (``harness_backend.py``) only ever saw raw MCP/adapter primitives
(``freecad.pad_sketch``, ``cadquery.execute_script``, ...) via
``mcp_tools_from_bridge`` -- the Pydantic-schema-validated skill layer
(``domain_agents/*/skills/*/handler.py``, e.g. ``generate_cad_ir``) was
invisible to it, reachable only from the separate legacy pydantic-ai path.
This module bridges the gap the same way ``make_set_project_scope_tool``
bridges a hand-built native tool: it turns each ``SkillRegistration`` from
``skill_registry.registry.SkillRegistry`` into a harness ``NativeToolDef``,
with no per-skill hardcoding (unlike ``domain_agents/mechanical/pydantic_ai_agent.py``'s
one-``@agent.tool``-function-per-skill pattern).

Gated behind ``METAFORGE_CHAT_SKILLS`` (see ``harness_backend.chat_skills_enabled``)
since it is newer, less-tested code than the MCP-bridge path and touches the
Digital Twin directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import structlog

from orchestrator.harness import NativeToolDef
from skill_registry.mcp_bridge import McpBridge
from skill_registry.registry import SkillRegistration, SkillRegistry
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)

_registry: SkillRegistry | None = None


async def _get_registry() -> SkillRegistry:
    """Lazily discover + cache the process-wide skill registry.

    Discovery does file reads + dynamic imports per skill -- expensive to
    repeat every chat turn, so it runs once per process, not once per call.
    """
    global _registry
    if _registry is None:
        reg = SkillRegistry()
        await reg.discover()
        _registry = reg
    return _registry


def _tool_for_registration(
    reg: SkillRegistration,
    *,
    twin: Any,
    mcp_bridge: McpBridge,
    session_id: str,
    branch: str,
) -> NativeToolDef:
    try:
        sid = UUID(session_id)
    except ValueError:
        sid = UUID(int=0)

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        ctx = SkillContext(
            twin=twin,
            mcp=mcp_bridge,
            logger=logger.bind(skill=reg.name),
            session_id=sid,
            branch=branch,
            domain=reg.domain,
        )
        result = await reg.handler_class(ctx).run(arguments)
        if not result.success:
            return {"success": False, "errors": result.errors}
        data = result.data.model_dump(mode="json") if result.data is not None else {}
        return {"success": True, **data}

    return NativeToolDef(
        name=f"skill_{reg.domain}_{reg.name}",
        description=reg.description,
        input_schema=reg.input_schema.model_json_schema(),
        handler=handler,
    )


async def skill_tools_from_registry(
    *,
    twin: Any,
    mcp_bridge: McpBridge,
    session_id: str,
    branch: str = "main",
    domain: str | Sequence[str] = "mechanical",
    registry: SkillRegistry | None = None,
) -> list[NativeToolDef]:
    """Adapt registered skills into harness ``NativeToolDef``s.

    ``domain`` restricts which skills are exposed -- mechanical only for now
    (Phase-1's first vertical), widening later is a call-site change, not a
    rewrite. ``registry`` is injectable for tests; production callers omit it
    and get the lazily-discovered process-wide registry.
    """
    reg_source = registry if registry is not None else await _get_registry()
    domains = {domain} if isinstance(domain, str) else set(domain)
    registrations = await reg_source.list_skills()
    return [
        _tool_for_registration(
            reg, twin=twin, mcp_bridge=mcp_bridge, session_id=session_id, branch=branch
        )
        for reg in registrations
        if reg.domain in domains
    ]
