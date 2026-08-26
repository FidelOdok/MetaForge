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

import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import structlog

from api_gateway.chat.backend import ChatBackend
from api_gateway.chat.scope import ScopeResolutionError, apply_thread_scope, resolve_project
from api_gateway.chat.skill_tools import skill_tools_from_registry
from api_gateway.chat.tool_approvals import get_approval_store
from observability.metrics import MetricsCollector
from orchestrator.harness import AgentContext, NativeToolDef, build_agent_runtime
from orchestrator.harness.compression import default_token_count, summarize_trajectory
from orchestrator.harness.native_tools import NATIVE_SYSTEM, run_native_tools
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
from orchestrator.harness.providers.adapters import default_stream_events
from orchestrator.harness.providers.auth_store import AuthStore
from orchestrator.harness.providers.pipeline import Invoke, StreamInvoke
from orchestrator.harness.providers.registry import ANTHROPIC, OPENAI, get_profile
from orchestrator.harness.react import run_react
from orchestrator.harness.runtime import OnApprovalRequest
from orchestrator.harness.tools import Handler
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)

_TRUTHY = {"1", "true", "on", "yes"}


_DETACH_QUERIES = {"none", "off", "clear", "-"}


def make_set_project_scope_tool(thread_id: str, backend: ChatBackend) -> NativeToolDef:
    """``chat.set_project_scope`` — the agent's side of MET-580.

    A user asking in prose to "switch to project X" previously did nothing:
    the model just answered conversationally with no protocol effect (MET-578
    fixed the human ``/project`` path; this is the agent path). The handler
    closes over ``thread_id`` so the model never supplies (or mis-supplies)
    which thread to rescope — it only ever names the project (or asks to leave
    one, mirroring ``/project none``).
    """

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("project")
        if not query or not isinstance(query, str):
            raise ValueError("chat.set_project_scope: 'project' is required (non-empty string)")

        if query.strip().lower() in _DETACH_QUERIES:
            try:
                await apply_thread_scope(
                    backend, thread_id, scope_kind="assistant", scope_entity_id=thread_id
                )
            except ScopeResolutionError as exc:
                raise ValueError(str(exc)) from exc
            logger.info("chat_scope_tool_detached", thread_id=thread_id)
            return {
                "scope_kind": "assistant",
                "project_id": None,
                "project_name": None,
                "instruction": (
                    "Left the project. Tell the user explicitly — don't just continue "
                    "as if it happened silently."
                ),
            }

        try:
            project = await resolve_project(query)
            await apply_thread_scope(
                backend,
                thread_id,
                scope_kind="project",
                scope_entity_id=project.id,
                project_name=project.name,
            )
        except ScopeResolutionError as exc:
            # Surfaced to the model as a tool error, same as any bad argument —
            # it must relay the failure, not claim the switch happened.
            raise ValueError(str(exc)) from exc
        logger.info("chat_scope_tool_switched", thread_id=thread_id, project_id=project.id)
        return {
            "scope_kind": "project",
            "project_id": project.id,
            "project_name": project.name,
            "instruction": (
                f"Scope switched to project '{project.name}'. Tell the user explicitly "
                "that you switched — don't just continue as if it happened silently."
            ),
        }

    return NativeToolDef(
        name="chat.set_project_scope",
        description=(
            "Rescope THIS conversation to a project, given its id, exact name, or a "
            "unique substring of the name — e.g. the user says 'switch to the Foo "
            "project'. Pass 'none' to leave the current project. An ambiguous or "
            "unknown name is refused (never guessed); relay the refusal to the user "
            "rather than picking one. On success, explicitly tell the user you switched."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Project id, exact name, or unique substring of the name. "
                        "'none' to leave the current project."
                    ),
                }
            },
            "required": ["project"],
        },
        handler=handler,
    )


# Three-tier permissions, "ask" (production-harness audit follow-up): a
# starter, conservative set of tool ids that mutate persistent state outside
# the ephemeral adapter workspace. Everything else stays auto-allow. Deliberately
# narrow for v1 -- expanding tier assignment to more tools (or to skill-layer
# tools, which currently bypass this since they call twin.commit_geometry
# internally rather than as a separately model-callable step) is flagged as
# a real, known gap, not silently dropped.
_REQUIRES_APPROVAL_TOOL_IDS = frozenset(
    {
        "twin.commit_geometry",
        "twin.record_decision",
        "project.create",
        "project.update",
        "project.delete",
    }
)


async def mcp_tools_from_bridge(
    bridge: McpBridge, enabled: set[str] | None = None
) -> list[tuple[str, NativeToolDef]]:
    """Adapt a provider's MCP bridge tools into harness ``NativeToolDef``s.

    Each bridge tool becomes an ``mcp_<server>_<tool>`` entry whose handler
    invokes it through the bridge. The bridge's ``list_tools`` carries each
    tool's ``input_schema`` (from its ``ToolManifest``); we surface it verbatim
    so the model knows the tool's required/optional parameters. Tools that
    declare no usable object schema fall back to a permissive one (the model
    supplies args, the bridge validates). Registering these lets the chat
    harness *drive tools*, not just converse — subject to a live bridge being
    wired into the gateway.

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

        # Surface the tool's real parameter schema so the model can supply the
        # required arguments. Fall back to a permissive object schema only when
        # the manifest declares none (an empty dict or a non-object schema).
        raw_schema = entry.get("input_schema")
        if isinstance(raw_schema, dict) and raw_schema.get("type") == "object":
            input_schema = raw_schema
        else:
            input_schema = {"type": "object"}

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
                    input_schema=input_schema,
                    handler=_make_handler(tool_id),
                    requires_approval=tool_id in _REQUIRES_APPROVAL_TOOL_IDS,
                ),
            )
        )
    return defs


_FALSY = {"0", "false", "off", "no"}


def native_tools_enabled(provider: str | None = None) -> bool:
    """Whether this turn uses native tool-calling instead of JSON-in-text ReAct.

    ``METAFORGE_NATIVE_TOOLS`` forces it on/off. Otherwise the default is ON for
    providers whose adapter parses native ``tool_calls`` (OpenAI-compatible and
    Anthropic) and OFF for the rest (Gemini/Bedrock/...), which stay on the ReAct
    path until their adapters gain native tool support.
    """
    raw = os.environ.get("METAFORGE_NATIVE_TOOLS", "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    prov = (provider or os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip().lower()
    if not prov:
        return False
    try:
        return get_profile(prov).api_family in (OPENAI, ANTHROPIC)
    except Exception:  # noqa: BLE001 - unknown provider → conservative ReAct default
        return False


def chat_harness_enabled() -> bool:
    """True when chat should route through the harness (env flag)."""
    return os.environ.get("METAFORGE_CHAT_HARNESS", "").strip().lower() in _TRUTHY


def chat_skills_enabled() -> bool:
    """True when skill-layer tools (generate_cad_ir, etc.) are exposed to the
    harness loop, alongside its existing raw MCP/adapter tools (env flag).

    Independent of ``chat_harness_enabled`` -- this is newer, less-tested code
    that touches the Digital Twin directly, so it must be separately
    toggleable/rollback-able without disabling the harness entirely.
    """
    return os.environ.get("METAFORGE_CHAT_SKILLS", "").strip().lower() in _TRUTHY


_DEFAULT_CHAT_MAX_STEPS = 24
_DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS = 1800.0


def trace_token_budget(provider: str | None, model: str | None) -> int:
    """Within-turn context budget for the tool loop (MET-568).

    ~60% of the model's context window, reserving the rest for system prompt,
    tool schemas, history, and the answer. Clamped to a sane floor so tiny or
    unknown windows still leave the loop room to work.
    """
    return max(8_000, int(context_window_for(provider, model) * 0.6))


def chat_max_steps() -> int:
    """Tool-call budget for a chat turn (``METAFORGE_CHAT_MAX_STEPS``, default 24).

    Six was far too few for real agentic work — a multi-part CAD assembly alone
    needs ~15-20 tool calls, so the agent hit the ceiling and bailed to prose.
    Configurable so heavy tasks can raise it further without a code change.
    """
    raw = (os.environ.get("METAFORGE_CHAT_MAX_STEPS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_CHAT_MAX_STEPS


def chat_wall_clock_seconds() -> float | None:
    """Hard wall-clock budget for a chat turn (``METAFORGE_CHAT_WALL_CLOCK_SECONDS``).

    Production-harness audit follow-up: only step-count and context-size were
    ever bounded — a turn stuck in slow-but-not-erroring calls had no ceiling
    at all. ``None`` (the default) keeps the historical unbounded behavior;
    this is opt-in, not a surprise behavior change for existing deployments.
    """
    raw = (os.environ.get("METAFORGE_CHAT_WALL_CLOCK_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def chat_max_cost_usd() -> float | None:
    """Hard dollar-spend budget for a chat turn (``METAFORGE_CHAT_MAX_COST_USD``).

    Production-harness audit follow-up. Only enforced on the native-tools
    path (the default for Anthropic/OpenAI-family providers) — ``run_react``
    never tallies provider-reported token usage at all today, a separate,
    pre-existing gap this doesn't fix. Priced against
    ``orchestrator.harness.providers.pricing.DEFAULT_PRICING`` (illustrative,
    not authoritative — see that module). ``None`` (the default) keeps the
    historical unbounded behavior.
    """
    raw = (os.environ.get("METAFORGE_CHAT_MAX_COST_USD") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def chat_approval_timeout_seconds() -> float:
    """How long a `requires_approval` tool call (``twin.commit_geometry``,
    etc.) waits for a decision before denying by default
    (``METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS``, default 1800 = 30 minutes).

    The prior 120s default (``HarnessRuntime``'s own fallback) assumed a live
    approval UI a human could click within two minutes -- none exists yet on
    either the dashboard or the TUI (both surfaces only show the *result* of
    a chat turn, not a mid-turn approval prompt), so every real interactive
    session's `twin.commit_geometry` call was silently denied on a timer the
    user had no way to beat. Confirmed live: a TUI user's multi-part CAD
    commit was denied twice in one session, forcing a full geometry rebuild
    each time. Deny-by-default itself is correct (fail-safe, never silently
    proceeds) -- this only widens the window a human realistically has to
    notice and approve out-of-band (e.g. via a polling script) before that
    fail-safe kicks in.
    """
    raw = (os.environ.get("METAFORGE_CHAT_APPROVAL_TIMEOUT_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_CHAT_APPROVAL_TIMEOUT_SECONDS


def _cost_target(ctx: AgentContext) -> tuple[str, str]:
    """The (provider, model) this turn's primary candidate resolves to, for
    spend-cap pricing lookup. A mid-turn provider fallback would be priced
    against this same pair — a documented approximation, not exact billing."""
    try:
        primary = ctx.runtime.providers.resolve("generator")[0]
    except (KeyError, IndexError):
        return "", ""
    return primary.name, primary.model


def rotation_strategy_from_env() -> RotationStrategy:
    """Credential rotation strategy from METAFORGE_ROTATION_STRATEGY (default round_robin)."""
    raw = (os.environ.get("METAFORGE_ROTATION_STRATEGY") or "").strip().lower()
    try:
        return RotationStrategy(raw) if raw else RotationStrategy.ROUND_ROBIN
    except ValueError:
        logger.warning("unknown_rotation_strategy", value=raw)
        return RotationStrategy.ROUND_ROBIN


def resolve_active_provider(provider: str | None = None) -> str:
    """The provider a turn will actually run on, after full precedence.

    Per-turn arg → auth-store durable ``selection`` → ``METAFORGE_LLM_PROVIDER``
    → ``"anthropic"``. This is the single source of truth shared by
    ``provider_config_from_env`` (which builds the invoke chain from it) and
    the native-vs-ReAct path decision.

    MET-575 (live-caught): the path decision used to consult only the per-turn
    arg and the env var. On a gateway whose auth store selected ``openai-codex``
    while env said ``openrouter``, every turn picked the NATIVE tool path (env
    provider is OpenAI-family) but was SERVED by ``codex_invoke`` (selection
    wins in the config), which cannot forward native tool schemas — so the
    model saw zero tools while the context meter honestly counted 87
    registered, and twin writes were fabricated or refused. Resolving the same
    provider for both decisions keeps the path and the adapter in agreement.
    """
    store = AuthStore()
    selection = store.get_selection()
    sel_provider = selection.provider.strip().lower() if selection else ""
    env_provider = (os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip().lower()
    return ((provider or "").strip().lower()) or sel_provider or env_provider or "anthropic"


def provider_config_from_env(
    *, provider: str | None = None, model: str | None = None
) -> HarnessProviderConfig:
    """Build the 'generator' role's provider fallback chain from (in precedence
    order): per-turn UI override → the gateway auth store (`forge auth login` /
    `use`) → the METAFORGE_LLM_* env defaults.

    The active provider/model come from an explicit arg, else the store's durable
    ``selection``, else env. When a provider has a raw key in the store it is
    injected into the ``ProviderSpec`` (``_require_key`` prefers it over env), so
    a CLI login takes effect on the next turn with no restart. Env remains the
    fallback everywhere, so an empty store changes nothing.

    Live-caught (MET-10): this used to return a single-element candidate list,
    so a turn died outright on the FIRST error from whichever provider won
    precedence -- a rate limit, an expired key, an out-of-credit account --
    with no fallback even when a perfectly good provider was configured right
    next to it. Returns a real chain instead: primary, then the env default
    (if it's a different provider), then every other provider with a stored
    raw key, each deduped by id.
    """
    store = AuthStore()
    selection = store.get_selection()
    env_provider = (os.environ.get("METAFORGE_LLM_PROVIDER") or "").strip().lower()
    sel_provider = selection.provider.strip().lower() if selection else ""
    prov = resolve_active_provider(provider)
    # Only take the selection's model when it belongs to the active provider.
    sel_model = selection.model if (selection and sel_provider == prov) else None
    default_model = (os.environ.get("METAFORGE_LLM_MODEL") or "claude-opus-4-8").strip()
    mdl = (model or sel_model or default_model).strip()

    def _is_env_default(name: str) -> bool:
        return name == env_provider or not env_provider

    def _spec_for(name: str, model_: str) -> ProviderSpec:
        if _is_env_default(name):
            api_key_env: str | None = "METAFORGE_LLM_API_KEY"
            base_url = (os.environ.get("METAFORGE_LLM_BASE_URL") or "").strip() or None
        else:
            # Different provider than the env default → use its registry credentials.
            api_key_env = None
            base_url = None
        try:
            spec = resolve_provider(name, model_, base_url=base_url, api_key_env=api_key_env)
        except UnknownProviderError:
            spec = ProviderSpec(
                name=name,
                model=model_,
                api_key_env=api_key_env or "METAFORGE_LLM_API_KEY",
                base_url=base_url,
            )
        # Inject a stored raw key (and its base_url) for this provider, if logged in.
        stored = store.get_credential(name)
        if stored is not None:
            spec = replace(spec, api_key=stored.api_key, base_url=stored.base_url or spec.base_url)
        # MET-655 remainder: so the pipeline can skip a later fallback candidate
        # whose window is provably too small too, instead of resending the same
        # oversized request and getting an identical context-length rejection.
        spec = replace(spec, max_context_tokens=context_window_for(name, model_))
        return spec

    candidates = [_spec_for(prov, mdl)]
    seen = {prov}
    env_default = env_provider or "anthropic"
    if env_default not in seen:
        candidates.append(_spec_for(env_default, default_model))
        seen.add(env_default)
    for other in sorted(store.configured_providers() - seen):
        candidates.append(_spec_for(other, mdl))
        seen.add(other)

    return HarnessProviderConfig(
        slots=RoleModelSlots(slots={"generator": candidates}), retry=RetryPolicy(), rotor=None
    )


def _tool_families(runtime: Any) -> list[str]:
    """Distinct tool families registered on the runtime (``mcp_twin_*`` -> ``twin``)."""
    families: set[str] = set()
    for t in runtime.tools.all_tools():
        name = str(t.name)
        if name.startswith("mcp_"):
            parts = name.split("_")
            families.add(parts[1] if len(parts) > 1 else name)
        else:
            families.add(name)
    return sorted(families)


def build_system_prompt(runtime: Any, *, project_brief: str | None = None) -> str:
    """Layered system prompt for the native path (MET-566).

    Sections: identity/rules -> current date -> capability summary (tool
    families from the registry) -> project brief -> response guidance. The
    project brief moves here from the fake ``[project context]`` user/assistant
    history pair (the ReAct path keeps that pair as its fallback). Everything
    here is stable per thread (date at day granularity, brief per project) so
    provider prompt caches hit across turns; per-turn volatile content — the
    retrieved-context block — stays in the message stream instead.
    """
    sections = [NATIVE_SYSTEM, f"Current date: {datetime.now(UTC).date().isoformat()}."]
    families = _tool_families(runtime)
    if families:
        sections.append(
            "You have tools from these families available: " + ", ".join(families) + "."
        )
    if project_brief:
        sections.append(f"[project context]\n{project_brief}")
    sections.append(
        "Ground your answers in the project context and any retrieved knowledge "
        "provided in the conversation; when you rely on a retrieved fragment, "
        "cite its bracketed source id."
    )
    return "\n\n".join(sections)


def _brief_pair(project_brief: str) -> list[dict[str, Any]]:
    """ReAct fallback: the project brief as a leading history exchange."""
    return [
        {"role": "user", "content": f"{_BRIEF_MARKER}\n{project_brief}"},
        {
            "role": "assistant",
            "content": "Understood — I'll work within that project and scope new work to it.",
        },
    ]


def _context_pair(context_block: str) -> list[dict[str, Any]]:
    """Retrieved context as a trailing history exchange, just before the goal.

    Volatile per-turn content (it depends on the current message) — kept in
    the message stream, not the system prompt, so the system prompt stays
    cache-stable across turns (MET-566).
    """
    return [
        {"role": "user", "content": f"{_CONTEXT_MARKER}\n{context_block}"},
        {
            "role": "assistant",
            "content": "Noted — I'll ground my answer in that retrieved context where relevant.",
        },
    ]


def _apply_turn_context(
    *,
    runtime: Any,
    native: bool,
    history: list[dict[str, Any]] | None,
    project_brief: str | None,
    context_block: str | None,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Place the brief + retrieved context per path -> (system, history).

    Native: brief lives in the layered system prompt. ReAct: brief stays a
    leading history pair (the harness-core ReAct system prompt is not ours to
    extend). Retrieved context is a trailing pair on both paths.
    """
    system = build_system_prompt(runtime, project_brief=project_brief if native else None)
    parts: list[dict[str, Any]] = []
    if not native and project_brief:
        parts.extend(_brief_pair(project_brief))
    parts.extend(history or [])
    if context_block:
        parts.extend(_context_pair(context_block))
    return system, (parts or None)


async def _build_context(
    session_id: str,
    store: CredentialStore,
    mcp_bridge: McpBridge | None,
    *,
    provider: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
    chat_backend: ChatBackend | None = None,
    twin: Any = None,
    metrics: MetricsCollector | None = None,
    on_approval_request: OnApprovalRequest | None = None,
) -> AgentContext:
    """Assemble the harness runtime with per-turn provider/model + tool selection.

    ``provider``/``model`` override the env defaults; ``enabled_tools`` (tool ids
    from the UI's connectors selector) restricts which MCP tools are registered
    (``None`` = all available). ``chat_backend``, when given, registers
    ``chat.set_project_scope`` (MET-580) closed over THIS turn's ``session_id`` —
    which is the live chat thread's id — so the agent can rescope its own
    thread on a clear user request without ever being trusted to supply (or
    possibly mis-supply) which thread that is. ``twin``, when given and
    ``chat_skills_enabled()``, also registers the mechanical skill-layer tools
    (``generate_cad_ir``, etc. — MET-548 follow-up) alongside the MCP tools.
    ``metrics``, when given, instruments every model/tool call the resulting
    runtime makes (production-harness audit follow-up). The resulting
    runtime always shares the process-level tool-approval store
    (``get_approval_store()``) so a `requires_approval` tool call can be
    resolved by a separate ``POST /v1/chat/tool_approvals/{run_id}`` request;
    ``on_approval_request``, when given, is notified the moment such a call
    pauses."""
    enabled = set(enabled_tools) if enabled_tools is not None else None
    mcp_tools = await mcp_tools_from_bridge(mcp_bridge, enabled) if mcp_bridge is not None else []
    native_tools = (
        [make_set_project_scope_tool(session_id, chat_backend)] if chat_backend is not None else []
    )
    if chat_skills_enabled() and twin is not None and mcp_bridge is not None:
        native_tools = native_tools + await skill_tools_from_registry(
            twin=twin, mcp_bridge=mcp_bridge, session_id=session_id
        )
    return build_agent_runtime(
        provider_config_from_env(provider=provider, model=model),
        credentials=store,
        session_id=session_id,
        rotation_strategy=rotation_strategy_from_env(),
        native_tools=native_tools,
        mcp_tools=mcp_tools,
        metrics=metrics,
        runs=get_approval_store(),
        on_approval_request=on_approval_request,
        approval_timeout_seconds=chat_approval_timeout_seconds(),
    )


async def run_chat_turn(
    user_content: str,
    *,
    invoke: Invoke = default_invoke,
    max_steps: int | None = None,
    session_id: str = "chat",
    credentials: CredentialStore | None = None,
    mcp_bridge: McpBridge | None = None,
    provider: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    project_brief: str | None = None,
    context_block: str | None = None,
    chat_backend: ChatBackend | None = None,
    twin: Any = None,
    metrics: MetricsCollector | None = None,
    wall_clock_seconds: float | None = None,
) -> str:
    """Answer a chat message via the harness ReAct loop. Returns the reply text.

    A credential store is attached so that when a provider has multiple stored
    credentials they rotate (and dead ones are blacklisted) per session; with an
    empty/absent store this is a no-op, so the default path is unchanged. When an
    ``mcp_bridge`` is given, its tools are registered so the loop can call them.
    ``provider``/``model``/``enabled_tools`` are the chat UI's per-turn selection.
    ``history`` is the prior conversation so multi-turn chats keep context.
    ``project_brief`` / ``context_block`` are placed per path (MET-566) — see
    ``_apply_turn_context``. ``chat_backend``, when given, registers
    ``chat.set_project_scope`` (MET-580). ``twin``, when given, also registers
    the skill-layer tools when ``chat_skills_enabled()`` (MET-548 follow-up).
    ``metrics``, when given, records a turn-duration metric. ``wall_clock_seconds``
    (defaults to :func:`chat_wall_clock_seconds`) hard-bounds the loop.
    """
    steps = max_steps if max_steps is not None else chat_max_steps()
    store = credentials if credentials is not None else CredentialStore()
    turn_start = time.monotonic()
    wall_clock = wall_clock_seconds if wall_clock_seconds is not None else chat_wall_clock_seconds()
    deadline = turn_start + wall_clock if wall_clock is not None else None
    ctx = await _build_context(
        session_id,
        store,
        mcp_bridge,
        provider=provider,
        model=model,
        enabled_tools=enabled_tools,
        chat_backend=chat_backend,
        twin=twin,
        metrics=metrics,
    )
    # MET-575: decide the path from the RESOLVED provider (arg → auth-store
    # selection → env), not the raw arg — see resolve_active_provider.
    native = native_tools_enabled(resolve_active_provider(provider))
    system, full_history = _apply_turn_context(
        runtime=ctx.runtime,
        native=native,
        history=history,
        project_brief=project_brief,
        context_block=context_block,
    )
    if native:
        cost_provider, cost_model = _cost_target(ctx)
        result = await run_native_tools(
            ctx.runtime,
            user_content,
            role="generator",
            invoke=invoke,
            max_steps=steps,
            system=system,
            history=full_history,
            # MET-568: bound within-turn growth; older tool exchanges fold
            # into a synopsis once the estimate crosses the budget.
            max_context_tokens=trace_token_budget(provider, model),
            deadline=deadline,
            max_cost_usd=chat_max_cost_usd(),
            cost_provider=cost_provider,
            cost_model=cost_model,
        )
    else:
        policy = ModelPolicy(
            ctx.runtime,
            role="generator",
            invoke=invoke,
            history=full_history,
            trace_token_budget=trace_token_budget(provider, model),
        )
        result = await run_react(
            ctx.runtime, policy, user_content, max_steps=steps, deadline=deadline
        )
    logger.info(
        "chat_harness_turn",
        status=result.status,
        steps=len(result.steps),
        stop_reason=result.stop_reason,
    )
    if metrics is not None:
        try:
            metrics.record_harness_turn(
                "native" if native else "react", result.stop_reason, time.monotonic() - turn_start
            )
        except Exception:  # noqa: BLE001 - metrics must never break a turn
            pass
    if result.output:
        return str(result.output)
    if result.stop_reason in ("max_steps", "timeout", "budget_exceeded"):
        return summarize_trajectory(result.steps)
    return _FALLBACK_ANSWER


_FALLBACK_ANSWER = "I couldn't converge on an answer within the step budget."
_STREAM_CHUNK_CHARS = 48


def _chunk_text(text: str, size: int = _STREAM_CHUNK_CHARS) -> list[str]:
    """Split *text* into ~``size``-char chunks on word boundaries, losslessly.

    Used to emit the loop's final answer as incremental deltas without a
    second model call; ``"".join(_chunk_text(t)) == t`` always holds.
    """
    pieces = re.findall(r"\S+\s*|\s+", text)
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        buf += piece
        if len(buf) >= size:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _json_safe(value: Any, *, _depth: int = 0) -> Any:
    """Coerce an arbitrary tool observation into a JSON-serializable shape.

    Tool results are usually dicts/strings, but a handler can return anything;
    fall back to ``str()`` so the step event never fails to serialize. Bounded
    recursion keeps a pathological nested object from blowing the stack.
    """
    if _depth > 6:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, _depth=_depth + 1) for v in value]
    return str(value)


def _step_to_dict(step: Any, index: int) -> dict[str, Any]:
    """Serialize a ReActStep into a JSON-safe event payload.

    The final step (``tool_call is None``) carries the answer as its
    observation — omitted here since the answer is streamed separately; only its
    thought is surfaced as closing reasoning.
    """
    tool_call = step.tool_call
    is_final = tool_call is None
    return {
        "index": index,
        "thought": step.thought or "",
        "tool": tool_call.name if tool_call is not None else None,
        "arguments": _json_safe(tool_call.arguments) if tool_call is not None else None,
        "observation": None if is_final else _json_safe(step.observation),
        "error": step.error,
        "final": is_final,
    }


# Approximate context-window sizes (in tokens) keyed by a substring of the model
# id, longest/most-specific first. Heuristic — override with METAFORGE_CONTEXT_WINDOW.
# Only used to report headroom in context.stats; nothing gates on it.
_MODEL_WINDOWS: tuple[tuple[str, int], ...] = (
    ("[1m]", 1_000_000),
    ("gpt-4.1", 1_000_000),
    ("llama-4", 1_000_000),
    ("gemini", 1_000_000),
    ("gpt-5.5", 400_000),
    ("gpt-5", 400_000),
    ("opus", 200_000),
    ("sonnet", 200_000),
    ("haiku", 200_000),
    ("claude", 200_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("qwen", 128_000),
    ("deepseek", 128_000),
    ("mistral", 32_000),
    ("gpt-4", 8_192),
)
_DEFAULT_WINDOW = 128_000
# MET-655: live-caught the `openai-codex` provider rejecting a 137k-token
# request with "input exceeds the context window" while the harness's own
# budget (derived from the raw model's advertised window below) thought it
# had 240k of headroom to work with. `context_window_for` matches on MODEL
# only, so "gpt-5.5" always resolved to the raw API's 400k figure even
# through this provider's own, apparently much smaller, effective limit.
# 100k is a conservative placeholder comfortably under the observed failure
# point (137k) -- not a confirmed number from OpenAI Codex docs, since none
# were found; tighten once the real limit is known.
_PROVIDER_WINDOWS: tuple[tuple[str, int], ...] = (("codex", 100_000),)
_BRIEF_MARKER = "[project context]"
_CONTEXT_MARKER = "[retrieved context]"


def context_window_for(provider: str | None, model: str | None) -> int:
    """Best-effort context-window size (tokens) for a provider/model.

    ``METAFORGE_CONTEXT_WINDOW`` overrides everything (for local/unknown models);
    otherwise a provider-specific override wins (a provider can enforce a
    materially smaller real limit than the underlying model's advertised
    window), then the first matching model-id substring, else a safe default.
    """
    override = os.getenv("METAFORGE_CONTEXT_WINDOW", "").strip()
    if override.isdigit():
        return int(override)
    p = (provider or "").lower()
    for key, window in _PROVIDER_WINDOWS:
        if key in p:
            return window
    m = (model or "").lower()
    for key, window in _MODEL_WINDOWS:
        if key in m:
            return window
    return _DEFAULT_WINDOW


def _tools_payload(runtime: Any) -> tuple[int, int]:
    """(#tools registered, token estimate of their schemas as sent to the model)."""
    parts: list[str] = []
    n = 0
    for t in runtime.tools.all_tools():
        n += 1
        schema = t.input_schema if isinstance(t.input_schema, dict) else {}
        parts.append(f"{t.name}\n{t.description or ''}\n{json.dumps(schema, default=str)}")
    return n, default_token_count("\n".join(parts))


def compute_context_stats(
    *,
    runtime: Any,
    system: str,
    history: list[dict[str, Any]] | None,
    user_content: str,
    provider: str | None,
    model: str | None,
    tools_available: int,
    availability: dict[str, int] | None = None,
    project_brief: str | None = None,
    context_block: str | None = None,
) -> dict[str, Any]:
    """Snapshot of what goes into this turn's context window vs. what's available.

    Token counts are heuristic (~4 chars/token; ``estimated=True``) until tiktoken
    lands. The project brief is bucketed separately from real conversation —
    either from the explicit ``project_brief`` arg (MET-566: brief lives in the
    system prompt / a path-injected pair, no longer in the caller's history) or
    by sniffing a legacy leading ``[project context]`` pair. ``context_block``
    (retrieved knowledge fragments) gets its own bucket. Each bucket reports
    tokens, and where meaningful ``items_included`` vs ``items_available`` (work
    products, history turns, tools) — i.e. what's shown vs. what exists.
    """
    avail = availability or {}
    hist = history or []
    brief_msgs: list[dict[str, Any]] = []
    convo_msgs = hist
    if hist and str(hist[0].get("content", "")).startswith(_BRIEF_MARKER):
        brief_msgs, convo_msgs = hist[:2], hist[2:]

    def _tok(msgs: list[dict[str, Any]]) -> int:
        return default_token_count("\n".join(str(m.get("content", "")) for m in msgs))

    sys_tok = default_token_count(system or "")
    if project_brief:
        brief_tok = default_token_count(project_brief)
    elif brief_msgs:
        brief_tok = _tok(brief_msgs)
    else:
        brief_tok = 0
    context_tok = default_token_count(context_block) if context_block else 0
    convo_tok = _tok(convo_msgs)
    n_tools, tools_tok = _tools_payload(runtime)
    msg_tok = default_token_count(user_content or "")
    used = sys_tok + brief_tok + context_tok + convo_tok + tools_tok + msg_tok
    window = context_window_for(provider, model)

    components: list[dict[str, Any]] = [
        {"key": "system", "label": "System prompt", "tokens": sys_tok},
    ]
    if project_brief or brief_msgs or avail.get("work_products_total"):
        brief_comp: dict[str, Any] = {
            "key": "project_brief",
            "label": "Project brief",
            "tokens": brief_tok,
        }
        if "work_products_total" in avail:
            brief_comp["items_included"] = avail.get("work_products_shown", 0)
            brief_comp["items_available"] = avail["work_products_total"]
            brief_comp["items_label"] = "work products"
        components.append(brief_comp)
    if context_block:
        components.append(
            {
                "key": "retrieved_context",
                "label": "Retrieved knowledge",
                "tokens": context_tok,
            }
        )
    history_comp: dict[str, Any] = {
        "key": "history",
        "label": "Conversation history",
        "tokens": convo_tok,
        "items_included": len(convo_msgs),
        "items_label": "turns",
    }
    if "history_turns_total" in avail:
        history_comp["items_available"] = avail["history_turns_total"]
    components.append(history_comp)
    components.append(
        {
            "key": "tools",
            "label": "Tool schemas",
            "tokens": tools_tok,
            "items_included": n_tools,
            "items_available": tools_available,
            "items_label": "tools",
        }
    )
    components.append({"key": "message", "label": "Current message", "tokens": msg_tok})

    return {
        "provider": provider or "(default)",
        "model": model or "(default)",
        "window": window,
        "used": used,
        "available": max(0, window - used),
        "utilization": round(used / window, 4) if window else None,
        "components": components,
        "estimated": True,
    }


async def run_chat_turn_streaming(
    user_content: str,
    *,
    on_delta: Callable[[str], Awaitable[None]],
    on_step: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_thinking: Callable[[str, str], Awaitable[None]] | None = None,
    on_action_started: Callable[[str], Awaitable[None]] | None = None,
    on_context: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_approval_request: OnApprovalRequest | None = None,
    invoke: Invoke = default_invoke,
    stream_invoke: StreamInvoke = default_stream,
    max_steps: int | None = None,
    session_id: str = "chat",
    credentials: CredentialStore | None = None,
    mcp_bridge: McpBridge | None = None,
    provider: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    availability: dict[str, int] | None = None,
    project_brief: str | None = None,
    context_block: str | None = None,
    chat_backend: ChatBackend | None = None,
    twin: Any = None,
    metrics: MetricsCollector | None = None,
    wall_clock_seconds: float | None = None,
) -> str:
    """Run the agent loop, then emit its final answer as chunked deltas.

    The loop runs to completion on the non-streaming (rotation-protected)
    path; its resolved answer is then pushed via ``on_delta`` in word-boundary
    chunks. The answer streamed is exactly the answer the loop produced — it
    is NOT re-generated by a second model call (the old design re-rendered it
    context-free, which could drift from the loop's real conclusion; MET-565).
    Returns the full text (what the caller should persist). ``stream_invoke``
    is retained for signature compatibility but no longer used. When an
    ``mcp_bridge`` is given, its tools are registered so the loop can call
    them. ``history`` is the prior conversation so multi-turn chats keep
    context. ``project_brief`` / ``context_block`` (MET-566) are placed per
    path by ``_apply_turn_context``: the brief joins the layered system prompt
    on the native path (history-pair fallback on ReAct); retrieved context is
    a trailing history pair on both. ``chat_backend``, when given, registers
    ``chat.set_project_scope`` (MET-580) so the agent can rescope THIS thread
    on a clear user request. ``twin``, when given, also registers the
    skill-layer tools when ``chat_skills_enabled()`` (MET-548 follow-up).
    ``metrics``, when given, records a turn-duration metric.
    ``wall_clock_seconds`` (defaults to :func:`chat_wall_clock_seconds`)
    hard-bounds the loop. ``on_approval_request`` (production-harness audit
    follow-up), when given, is notified ``(run_id, tool, arguments)`` the
    moment a `requires_approval` tool call pauses — resolved by a separate
    ``POST /v1/chat/tool_approvals/{run_id}`` request, never by this turn.
    """
    steps = max_steps if max_steps is not None else chat_max_steps()
    store = credentials if credentials is not None else CredentialStore()
    turn_start = time.monotonic()
    wall_clock = wall_clock_seconds if wall_clock_seconds is not None else chat_wall_clock_seconds()
    deadline = turn_start + wall_clock if wall_clock is not None else None
    ctx = await _build_context(
        session_id,
        store,
        mcp_bridge,
        provider=provider,
        model=model,
        enabled_tools=enabled_tools,
        chat_backend=chat_backend,
        twin=twin,
        metrics=metrics,
        on_approval_request=on_approval_request,
    )

    # MET-575: decide the path from the RESOLVED provider (arg → auth-store
    # selection → env), not the raw arg — see resolve_active_provider.
    native = native_tools_enabled(resolve_active_provider(provider))
    system, full_history = _apply_turn_context(
        runtime=ctx.runtime,
        native=native,
        history=history,
        project_brief=project_brief,
        context_block=context_block,
    )

    # Emit a context-window snapshot for this turn before the loop runs, so a
    # client can show what's going into the model vs. what's available. Best-effort:
    # a bad stats computation must never break the turn.
    tools_available = len(ctx.runtime.tools.all_tools())
    if on_context is not None:
        try:
            if mcp_bridge is not None:
                tools_available = len(await mcp_bridge.list_tools())
            stats = compute_context_stats(
                runtime=ctx.runtime,
                system=NATIVE_SYSTEM,
                history=history,
                user_content=user_content,
                provider=provider,
                model=model,
                tools_available=tools_available,
                availability=availability,
                project_brief=project_brief,
                context_block=context_block,
            )
            # Production-harness audit follow-up: this was only ever pushed
            # live over SSE — if no client was listening, it was computed and
            # thrown away. Logging it means the data survives regardless.
            logger.info("chat_context_stats", phase="pre", **stats)
            await on_context(stats)
        except Exception as exc:  # noqa: BLE001 — telemetry must not break the turn
            logger.warning("chat_context_stats_failed", error=str(exc))

    # MET-591/592: fan typed provider-stream events out to the dedicated
    # notifier callbacks. None when no observer wants them (streaming off).
    stream_event_cb: Any = None
    if on_thinking is not None or on_action_started is not None:

        async def stream_event_cb(event: dict[str, Any]) -> None:
            etype = event.get("type")
            if etype == "text_delta" and on_thinking is not None:
                await on_thinking(str(event.get("text", "")), "draft")
            elif etype == "thinking_delta" and on_thinking is not None:
                await on_thinking(str(event.get("text", "")), "reasoning")
            elif etype == "action_started" and on_action_started is not None:
                await on_action_started(str(event.get("name", "")))

    # MET-590: stream each step AS IT HAPPENS. Long agentic turns (multi-part
    # CAD builds) previously emitted their whole trace only after the loop —
    # clients saw nothing for minutes, and their hard request timeouts killed
    # healthy turns (disconnect cancels the turn by design, so all work was
    # lost). Live steps give clients both visibility and a liveness signal.
    live_step: Any = None
    if on_step is not None:
        _cb = on_step

        async def live_step(step: Any, index: int) -> None:
            try:
                await _cb(_step_to_dict(step, index))
            except Exception as exc:  # noqa: BLE001 — legibility is non-critical
                logger.warning("chat_step_emit_failed", index=index, error=str(exc))

    if native:
        cost_provider, cost_model = _cost_target(ctx)
        result = await run_native_tools(
            ctx.runtime,
            user_content,
            role="generator",
            invoke=invoke,
            max_steps=steps,
            system=system,
            history=full_history,
            # MET-568: bound within-turn growth; older tool exchanges fold
            # into a synopsis once the estimate crosses the budget.
            max_context_tokens=trace_token_budget(provider, model),
            on_step=live_step,
            # MET-591/592: token-level liveness — typed deltas stream to the
            # client while each model call generates (event-streaming
            # providers only; others fall back to the non-streaming invoke).
            on_stream_event=stream_event_cb,
            stream_events=default_stream_events if stream_event_cb is not None else None,
            deadline=deadline,
            max_cost_usd=chat_max_cost_usd(),
            cost_provider=cost_provider,
            cost_model=cost_model,
        )
    else:
        policy = ModelPolicy(
            ctx.runtime,
            role="generator",
            invoke=invoke,
            history=full_history,
            trace_token_budget=trace_token_budget(provider, model),
        )
        result = await run_react(
            ctx.runtime, policy, user_content, max_steps=steps, on_step=live_step, deadline=deadline
        )
    logger.info(
        "chat_harness_stream_turn",
        status=result.status,
        steps=len(result.steps),
        stop_reason=result.stop_reason,
    )
    if metrics is not None:
        try:
            metrics.record_harness_turn(
                "native" if native else "react", result.stop_reason, time.monotonic() - turn_start
            )
        except Exception:  # noqa: BLE001 - metrics must never break a turn
            pass

    # MET-568: re-emit context stats AFTER the loop so the meter reflects what
    # the turn actually consumed (the pre-loop snapshot can't see tool-result
    # growth). Same shape plus phase="final" and a trace-tokens estimate.
    if on_context is not None:
        try:
            trace_tokens = sum(
                default_token_count(str(st.observation or st.error or "") + str(st.thought or ""))
                for st in result.steps
            )
            final_stats = compute_context_stats(
                runtime=ctx.runtime,
                system=NATIVE_SYSTEM,
                history=history,
                user_content=user_content,
                provider=provider,
                model=model,
                tools_available=tools_available,
                availability=availability,
                project_brief=project_brief,
                context_block=context_block,
            )
            final_stats["phase"] = "final"
            final_stats["trace_tokens"] = trace_tokens
            # MET-596: real provider-reported token usage for the whole turn
            # (summed across the loop's model calls), alongside the estimates.
            if getattr(result, "usage", None):
                final_stats["usage"] = result.usage
            final_stats["used"] = int(final_stats.get("used", 0)) + trace_tokens
            final_stats["available"] = max(
                0, int(final_stats.get("window", 0)) - int(final_stats["used"])
            )
            # Production-harness audit follow-up: persist this even when no
            # SSE client is listening (see the pre-loop stats block above).
            logger.info("chat_context_stats", **final_stats)
            await on_context(final_stats)
        except Exception as exc:  # noqa: BLE001 — telemetry must not break the turn
            logger.warning("chat_context_stats_final_failed", error=str(exc))

    # MET-590: steps were already streamed live during the loop (live_step),
    # so no post-loop re-emit — duplicates would double-render the timeline.

    answer = str(result.output or "").strip()
    # Never stream an empty answer — a completed turn with no final text (weak
    # model, or an empty `final`), or one that hit the step cap / wall-clock
    # deadline without ever producing output, must still say something real
    # rather than a bare non-answer (production-harness audit follow-up: this
    # used to throw away the full trajectory even though it was in memory).
    if not answer:
        answer = (
            summarize_trajectory(result.steps)
            if result.stop_reason in ("max_steps", "timeout", "budget_exceeded")
            else _FALLBACK_ANSWER
        )
        await on_delta(answer)
        return answer
    # Emit the loop's own answer as chunked deltas. This used to re-generate
    # the final text with a second, context-free model call (no history, no
    # tool results), and stream THAT — which could drift from or hallucinate
    # past what the loop actually concluded, and cost an extra call per turn
    # (MET-565). The loop's answer IS the answer; chunking keeps the
    # incremental-render UX for SSE clients.
    for chunk in _chunk_text(answer):
        await on_delta(chunk)
    return answer
