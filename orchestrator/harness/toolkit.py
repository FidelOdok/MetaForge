"""Agent runtime assembly — tools + skills layer (MET-548).

`build_agent_runtime` is the composition point the chat backend and CLI call:
it builds a `HarnessRuntime`, registers a set of native tools, loads `SKILL.md`
skills from a directory, and returns both bundled as an `AgentContext`.

Native tools are passed in as `NativeToolDef`s whose `handler` wraps whatever
service the caller has (twin search, knowledge retrieval, an MCP-bridged tool).
Keeping the concrete services *injected* means this module stays dependency-
light and unit-testable, and the same assembly works for native + MCP tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from orchestrator.harness.providers import CredentialStore, HarnessProviderConfig
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.skills import SkillRegistry
from orchestrator.harness.tools import GateCheck, Handler, ToolRegistry

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class NativeToolDef:
    """A native tool to register at assembly time."""

    name: str
    description: str
    input_schema: dict[str, object]
    handler: Handler
    required_gates: tuple[str, ...] = ()


@dataclass
class AgentContext:
    """Everything an agent turn needs: runtime (models + tools + runs) + skills."""

    runtime: HarnessRuntime
    skills: SkillRegistry = field(default_factory=SkillRegistry)


def build_agent_runtime(
    provider_config: HarnessProviderConfig | None = None,
    *,
    native_tools: Sequence[NativeToolDef] = (),
    mcp_tools: Sequence[tuple[str, NativeToolDef]] = (),
    gate_check: GateCheck | None = None,
    credentials: CredentialStore | None = None,
    session_id: str = "default",
    skills_dir: Path | None = None,
) -> AgentContext:
    """Assemble the tools + skills layer into a ready `AgentContext`.

    `native_tools` register under their own name; `mcp_tools` are
    ``(server, def)`` pairs registered as ``mcp_<server>_<tool>``. Skills are
    discovered from ``skills_dir`` if given.
    """
    tools = ToolRegistry()
    for t in native_tools:
        tools.register_native(
            t.name,
            description=t.description,
            input_schema=t.input_schema,
            handler=t.handler,
            required_gates=t.required_gates,
        )
    for server, t in mcp_tools:
        tools.register_mcp(
            server,
            t.name,
            description=t.description,
            input_schema=t.input_schema,
            handler=t.handler,
            required_gates=t.required_gates,
        )

    runtime = HarnessRuntime.build(
        provider_config,
        tools=tools,
        gate_check=gate_check,
        credentials=credentials,
        session_id=session_id,
    )

    skills = SkillRegistry()
    if skills_dir is not None and skills_dir.exists():
        count = skills.load_dir(skills_dir)
        logger.info("agent_runtime_skills_loaded", count=count, dir=str(skills_dir))

    logger.info(
        "agent_runtime_built",
        native_tools=len(native_tools),
        mcp_tools=len(mcp_tools),
        skills=len(skills.names()),
    )
    return AgentContext(runtime=runtime, skills=skills)
