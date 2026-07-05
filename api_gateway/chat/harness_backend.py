"""Harness-backed chat turn (MET-548, surface A).

When ``METAFORGE_CHAT_HARNESS`` is enabled, a chat message is answered by the
MET-547 harness — a ReAct loop over a provider pipeline (retry + failover) with
gate-enforced tools — instead of the direct pydantic-ai agent call. The chat
REST/SSE contract is unchanged; only the internals swap, behind the flag, so
the existing dashboard chat UI drives the harness.

``run_chat_turn`` takes an injectable ``invoke`` so it unit-tests without
network; production defaults to the real provider adapters.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import structlog

from orchestrator.harness import AgentContext, NativeToolDef, build_agent_runtime
from orchestrator.harness.policy import ModelPolicy
from orchestrator.harness.providers import (
    CredentialStore,
    HarnessProviderConfig,
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
    RotationStrategy,
    UnknownProviderError,
    default_invoke,
    default_stream,
    resolve_provider,
)
from orchestrator.harness.providers.pipeline import Invoke, StreamInvoke
from orchestrator.harness.react import run_react
from orchestrator.harness.tools import Handler
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)

_TRUTHY = {"1", "true", "on", "yes"}


async def mcp_tools_from_bridge(
    bridge: McpBridge, enabled: set[str] | None = None
) -> list[tuple[str, NativeToolDef]]:
    """Adapt a provider's MCP bridge tools into harness ``NativeToolDef``s.

    Each bridge tool becomes an ``mcp_<server>_<tool>`` entry whose handler
    invokes it through the bridge. The bridge's ``list_tools`` gives no input
    schema, so a permissive object schema is used (the model supplies args, the
    bridge validates). Registering these lets the chat harness *drive tools*,
    not just converse — subject to a live bridge being wired into the gateway.

    ``enabled`` (a set of tool ids) restricts which tools are registered — the
    chat UI's tools/connectors selector passes the user's choice; ``None`` means
    register all available.
    """
    tools = await bridge.list_tools()
    defs: list[tuple[str, NativeToolDef]] = []
    for entry in tools:
        tool_id = str(entry.get("tool_id") or entry.get("name") or "").strip()
        if not tool_id:
            continue
        if enabled is not None and tool_id not in enabled:
            continue
        server, _, tool = tool_id.partition(".")
        if not tool:
            server, tool = "mcp", tool_id
        capability = entry.get("capability")
        description = f"{tool_id} ({capability})" if capability else tool_id

        def _make_handler(tid: str) -> Handler:
            async def handler(arguments: dict[str, object]) -> object:
                return await bridge.invoke(tid, dict(arguments))

            return handler

        defs.append(
            (
                server,
                NativeToolDef(
                    name=tool,
                    description=description,
                    input_schema={"type": "object"},
                    handler=_make_handler(tool_id),
                ),
            )
        )
    return defs


def chat_harness_enabled() -> bool:
    """True when chat should route through the harness (env flag)."""
    return os.environ.get("METAFORGE_CHAT_HARNESS", "").strip().lower() in _TRUTHY


def rotation_strategy_from_env() -> RotationStrategy:
    """Credential rotation strategy from METAFORGE_ROTATION_STRATEGY (default round_robin)."""
    raw = (os.environ.get("METAFORGE_ROTATION_STRATEGY") or "").strip().lower()
    try:
        return RotationStrategy(raw) if raw else RotationStrategy.ROUND_ROBIN
    except ValueError:
        logger.warning("unknown_rotation_strategy", value=raw)
        return RotationStrategy.ROUND_ROBIN


def provider_config_from_env(
    *, provider: str | None = None, model: str | None = None
) -> HarnessProviderConfig:
    """Build a single-role provider config, honoring per-turn UI overrides.

    ``provider``/``model`` (from the chat selector) win over the METAFORGE_LLM_*
    env defaults. When the selected provider matches the env-configured one, the
    env key/base_url overrides (METAFORGE_LLM_API_KEY / _BASE_URL) apply; when a
    *different* provider is selected, its own registry key env + base_url are
    used, so switching models in the UI uses that provider's credentials.
    """
    env_provider = (os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip().lower()
    prov = (provider or env_provider or "anthropic").strip().lower()
    mdl = (model or os.environ.get("METAFORGE_LLM_MODEL") or "claude-opus-4-8").strip()

    if prov == env_provider or not env_provider:
        api_key_env: str | None = "METAFORGE_LLM_API_KEY"
        base_url = (os.environ.get("METAFORGE_LLM_BASE_URL") or "").strip() or None
    else:
        # Different provider than the env default → use its registry credentials.
        api_key_env = None
        base_url = None
    try:
        spec = resolve_provider(prov, mdl, base_url=base_url, api_key_env=api_key_env)
    except UnknownProviderError:
        spec = ProviderSpec(
            name=prov,
            model=mdl,
            api_key_env=api_key_env or "METAFORGE_LLM_API_KEY",
            base_url=base_url,
        )
    return HarnessProviderConfig(
        slots=RoleModelSlots(slots={"generator": [spec]}), retry=RetryPolicy(), rotor=None
    )


async def _build_context(
    session_id: str,
    store: CredentialStore,
    mcp_bridge: McpBridge | None,
    *,
    provider: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
) -> AgentContext:
    """Assemble the harness runtime with per-turn provider/model + tool selection.

    ``provider``/``model`` override the env defaults; ``enabled_tools`` (tool ids
    from the UI's connectors selector) restricts which MCP tools are registered
    (``None`` = all available)."""
    enabled = set(enabled_tools) if enabled_tools is not None else None
    mcp_tools = await mcp_tools_from_bridge(mcp_bridge, enabled) if mcp_bridge is not None else []
    return build_agent_runtime(
        provider_config_from_env(provider=provider, model=model),
        credentials=store,
        session_id=session_id,
        rotation_strategy=rotation_strategy_from_env(),
        mcp_tools=mcp_tools,
    )


async def run_chat_turn(
    user_content: str,
    *,
    invoke: Invoke = default_invoke,
    max_steps: int = 6,
    session_id: str = "chat",
    credentials: CredentialStore | None = None,
    mcp_bridge: McpBridge | None = None,
    provider: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
) -> str:
    """Answer a chat message via the harness ReAct loop. Returns the reply text.

    A credential store is attached so that when a provider has multiple stored
    credentials they rotate (and dead ones are blacklisted) per session; with an
    empty/absent store this is a no-op, so the default path is unchanged. When an
    ``mcp_bridge`` is given, its tools are registered so the loop can call them.
    ``provider``/``model``/``enabled_tools`` are the chat UI's per-turn selection.
    """
    store = credentials if credentials is not None else CredentialStore()
    ctx = await _build_context(
        session_id, store, mcp_bridge, provider=provider, model=model, enabled_tools=enabled_tools
    )
    policy = ModelPolicy(ctx.runtime, role="generator", invoke=invoke)
    result = await run_react(ctx.runtime, policy, user_content, max_steps=max_steps)
    logger.info("chat_harness_turn", status=result.status, steps=len(result.steps))
    if result.status == "completed":
        return str(result.output)
    return "I couldn't converge on an answer within the step budget."


_FALLBACK_ANSWER = "I couldn't converge on an answer within the step budget."
_STREAM_SYSTEM = "You are MetaForge's assistant. Write the final answer to the user, clearly."


async def run_chat_turn_streaming(
    user_content: str,
    *,
    on_delta: Callable[[str], Awaitable[None]],
    invoke: Invoke = default_invoke,
    stream_invoke: StreamInvoke = default_stream,
    max_steps: int = 6,
    session_id: str = "chat",
    credentials: CredentialStore | None = None,
    mcp_bridge: McpBridge | None = None,
    provider: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
) -> str:
    """Run the ReAct loop, then stream the final answer token-by-token (Option B).

    ReAct runs to completion on the non-streaming (rotation-protected) path; the
    resolved answer is then re-rendered by a single dedicated streaming call,
    with each delta pushed via ``on_delta``. Falls back to emitting the computed
    answer as one delta if streaming fails or yields nothing. Returns the full
    assembled text (what the caller should persist). When an ``mcp_bridge`` is
    given, its tools are registered so the loop can call them.
    """
    store = credentials if credentials is not None else CredentialStore()
    ctx = await _build_context(
        session_id, store, mcp_bridge, provider=provider, model=model, enabled_tools=enabled_tools
    )
    policy = ModelPolicy(ctx.runtime, role="generator", invoke=invoke)
    result = await run_react(ctx.runtime, policy, user_content, max_steps=max_steps)
    logger.info("chat_harness_stream_turn", status=result.status, steps=len(result.steps))

    if result.status != "completed":
        await on_delta(_FALLBACK_ANSWER)
        return _FALLBACK_ANSWER

    answer = str(result.output)
    request = {
        "system": _STREAM_SYSTEM,
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": answer},
            {"role": "user", "content": "Write your final answer to the user now."},
        ],
    }
    collected: list[str] = []
    try:
        async for delta in ctx.runtime.stream_complete("generator", request, stream_invoke):
            collected.append(delta)
            await on_delta(delta)
    except Exception as exc:  # noqa: BLE001 - streaming is best-effort; fall back below
        logger.warning("chat_stream_failed_falling_back", error=str(exc))

    if collected:
        return "".join(collected)
    # Streaming failed or produced nothing → emit the already-computed answer.
    await on_delta(answer)
    return answer
