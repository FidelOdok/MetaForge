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

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from observability.metrics import MetricsCollector
from orchestrator.harness.providers import (
    CredentialStore,
    HarnessProviderConfig,
    RotationStrategy,
)
from orchestrator.harness.runs import InMemoryRunStore
from orchestrator.harness.runtime import HarnessRuntime, OnApprovalRequest
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
    # Three-tier permissions, "ask" (production-harness audit follow-up): the
    # model must wait for an interactive human decision before this tool runs.
    requires_approval: bool = False


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
    clock: Callable[[], float] = time.time,
    rotation_strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
    skills_dir: Path | None = None,
    metrics: MetricsCollector | None = None,
    runs: InMemoryRunStore | None = None,
    on_approval_request: OnApprovalRequest | None = None,
    approval_timeout_seconds: float = 120.0,
    approval_poll_interval: float = 1.0,
    approval_sleep: Callable[[float], Awaitable[None]] | None = None,
) -> AgentContext:
    """Assemble the tools + skills layer into a ready `AgentContext`.

    `native_tools` register under their own name; `mcp_tools` are
    ``(server, def)`` pairs registered as ``mcp_<server>_<tool>``. Skills are
    discovered from ``skills_dir`` if given.

    ``runs``/``on_approval_request``/``approval_*`` (production-harness audit
    follow-up) configure the third permission tier, "ask" — see
    ``HarnessRuntime.build``/``_await_approval`` for the mechanism. ``runs``
    should be a process-level, shared store (not a fresh one per turn) so a
    separate approval-decision request can reach the same live run.
    """
    tools = ToolRegistry()
    for t in native_tools:
        tools.register_native(
            t.name,
            description=t.description,
            input_schema=t.input_schema,
            handler=t.handler,
            required_gates=t.required_gates,
            requires_approval=t.requires_approval,
        )
    for server, t in mcp_tools:
        tools.register_mcp(
            server,
            t.name,
            description=t.description,
            input_schema=t.input_schema,
            handler=t.handler,
            required_gates=t.required_gates,
            requires_approval=t.requires_approval,
        )

    runtime = HarnessRuntime.build(
        provider_config,
        tools=tools,
        gate_check=gate_check,
        credentials=credentials,
        session_id=session_id,
        clock=clock,
        rotation_strategy=rotation_strategy,
        metrics=metrics,
        runs=runs,
        on_approval_request=on_approval_request,
        approval_timeout_seconds=approval_timeout_seconds,
        approval_poll_interval=approval_poll_interval,
        approval_sleep=approval_sleep or asyncio.sleep,
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
