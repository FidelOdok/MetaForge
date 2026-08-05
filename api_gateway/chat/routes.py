"""Chat REST endpoints for the MetaForge Gateway.

Provides CRUD operations on chat channels, threads, and messages.
Storage is delegated to a ``ChatBackend`` — either PostgreSQL (when
``DATABASE_URL`` is set) or an in-memory fallback.

When a user message is posted, the handler routes it to the appropriate
domain agent (if an LLM is configured) and appends the agent's response
to the thread.

Endpoints live under ``/v1/chat``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api_gateway.chat.agent_router import default_router
from api_gateway.chat.backend import ChatBackend, InMemoryChatBackend
from api_gateway.chat.harness_backend import (
    chat_harness_enabled,
    run_chat_turn_streaming,
)
from api_gateway.chat.models import (
    ChatMessageRecord,
    ChatThreadRecord,
)
from api_gateway.chat.schemas import (
    ChannelListResponse,
    ChannelResponse,
    CreateThreadRequest,
    MessageResponse,
    SendMessageRequest,
    ThreadListResponse,
    ThreadResponse,
    ThreadSummaryResponse,
)
from api_gateway.chat.streaming import (
    notify_agent_done,
    notify_agent_step,
    notify_agent_typing,
    notify_context_stats,
    notify_message_delta,
    stream_manager,
    stream_thread,
)

# MET-575: import the ACCESSOR, never the module attribute. A
# ``from ... import _backend as _project_backend`` binds the in-memory
# instance that exists at import time; when server startup swaps the real
# (Postgres) backend in via ``init_project_backend``, the alias silently
# keeps pointing at the empty in-memory store — so every project-scoped
# chat lost its brief (get_project always missed) on any real deployment.
from api_gateway.projects.routes import get_project_backend
from domain_agents.base_agent import get_llm_model, is_llm_available
from domain_agents.mechanical.pydantic_ai_agent import (
    MechanicalAgentDeps,
    run_agent,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import InMemoryMcpBridge, McpBridge
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.chat.routes")

_T = TypeVar("_T")


async def _run_cancellable_on_disconnect(
    is_disconnected: Callable[[], Awaitable[bool]],
    coro: Awaitable[_T],
    *,
    poll_interval: float = 1.0,
) -> _T:
    """Run *coro*, cancelling it the first time *is_disconnected* reports true.

    A chat turn runs synchronously inside this POST and can take minutes (the
    harness may make many tool calls) -- without this, a client that
    disconnects early (TUI abort, closed browser tab, dropped network) left
    the turn running server-side to completion with nothing left to consume
    the result: wasted compute, and for design turns, wasted tool/LLM spend.
    Raises ``asyncio.CancelledError`` when cancelled, same as the underlying
    task -- callers decide how to respond to that.
    """
    task: asyncio.Task[_T] = asyncio.ensure_future(coro)

    async def _watch() -> None:
        while not task.done():
            if await is_disconnected():
                task.cancel()
                return
            await asyncio.sleep(poll_interval)

    watcher = asyncio.ensure_future(_watch())
    try:
        return await task
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


# ---------------------------------------------------------------------------
# Module-level backend & router
# ---------------------------------------------------------------------------

_backend: ChatBackend = InMemoryChatBackend.create()

router = APIRouter(prefix="/v1/chat", tags=["chat"])


def init_chat_backend(backend: ChatBackend) -> None:
    """Replace the default in-memory backend with a production backend.

    Called by the API Gateway lifespan after determining the storage backend.
    """
    global _backend  # noqa: PLW0603
    _backend = backend
    logger.info("chat_backend_initialized", backend_type=type(backend).__name__)


# Legacy alias — kept for backward compatibility with tests that import `store`
store = _backend

# ---------------------------------------------------------------------------
# Module-level singletons for agent invocation
# ---------------------------------------------------------------------------

_twin = InMemoryTwinAPI.create()
_mcp_bridge: McpBridge = InMemoryMcpBridge()


def init_mcp_bridge(bridge: McpBridge) -> None:
    """Replace the default InMemoryMcpBridge with a real bridge.

    Called by the API Gateway lifespan after bootstrapping the tool registry.
    """
    global _mcp_bridge  # noqa: PLW0603
    _mcp_bridge = bridge
    logger.info("mcp_bridge_initialized", bridge_type=type(bridge).__name__)


def get_mcp_bridge() -> McpBridge:
    """The currently-active MCP bridge (used by the harness capability routes)."""
    return _mcp_bridge


def init_twin(twin: object) -> None:
    """Replace the default InMemoryTwinAPI with the orchestrator's twin.

    Called by the API Gateway lifespan so chat routes share state with agents.
    """
    global _twin  # noqa: PLW0603
    _twin = twin  # type: ignore[assignment]
    logger.info("twin_initialized", twin_type=type(twin).__name__)


def _make_message_response(msg: ChatMessageRecord) -> MessageResponse:
    """Convert a ``ChatMessageRecord`` to a ``MessageResponse``."""
    return MessageResponse(
        id=msg.id,
        thread_id=msg.thread_id,
        actor_id=msg.actor_id,
        actor_kind=msg.actor_kind,
        content=msg.content,
        status=msg.status,
        graph_ref_node=msg.graph_ref_node,
        graph_ref_type=msg.graph_ref_type,
        graph_ref_label=msg.graph_ref_label,
        created_at=msg.created_at,
        updated_at=msg.updated_at,
    )


# actor_kind (as stored) -> LLM chat role. Only these two carry into context;
# system/error messages are left out.
_HISTORY_ROLE = {"user": "user", "agent": "assistant"}
_HISTORY_LIMIT = 20  # most-recent turns fed back as context


_PROJECT_WP_LIMIT = 30  # most work products listed in the project brief


async def _project_brief(thread: ChatThreadRecord) -> list[dict[str, str]]:
    """A leading context turn describing the thread's project (empty if none).

    When a thread is scoped to a project (``scope_kind == "project"``), the agent
    should reason over the project's digital thread — its existing work products —
    and persist new CAD/decisions back into it. The agent loop can't be handed the
    project object, so we frame it as the earliest ``history`` exchange: a synthetic
    user turn stating the project context, plus an assistant acknowledgement. This
    reaches both the native-tool and ReAct loops without touching harness core.
    """
    if thread.scope_kind != "project" or not thread.scope_entity_id:
        return []
    project = await get_project_backend().get_project(thread.scope_entity_id)
    if project is None:
        return []

    lines = [
        f"You are working inside the MetaForge project **{project.name}** "
        f"(project_id `{project.id}`, status {project.status}).",
    ]
    if project.description:
        lines.append(f"Project intent: {project.description}")

    wps = project.work_products[:_PROJECT_WP_LIMIT]
    if wps:
        lines.append(f"\nExisting work products in this project ({len(project.work_products)}):")
        for wp in wps:
            lines.append(f"- {wp.name} — {wp.type} (status {wp.status})")
        if len(project.work_products) > _PROJECT_WP_LIMIT:
            lines.append(f"- …and {len(project.work_products) - _PROJECT_WP_LIMIT} more")
    else:
        lines.append("\nThis project has no work products yet.")

    lines.append(
        f"\nTo save any CAD model or design decision into this project, pass "
        f'`project_id="{project.id}"` when you call `twin.commit_geometry` or '
        f"`twin.record_decision`. Ground your answers in the work products above."
    )
    brief = "\n".join(lines)
    return [
        {"role": "user", "content": f"[project context]\n{brief}"},
        {
            "role": "assistant",
            "content": (
                f"Understood — I'm working within project {project.name} "
                f"({project.id}) and will scope new work products to it."
            ),
        },
    ]


async def _context_availability(thread: ChatThreadRecord) -> dict[str, int]:
    """Totals for the context meter: what *exists* vs. what the brief/history show.

    Lets ``context.stats`` report "30 of 42 work products" / "20 of 57 turns" so a
    client can see what was trimmed to fit the window. Cheap best-effort reads;
    returns only the keys it can resolve.
    """
    avail: dict[str, int] = {}
    if thread.scope_kind == "project" and thread.scope_entity_id:
        project = await get_project_backend().get_project(thread.scope_entity_id)
        if project is not None:
            total = len(project.work_products)
            avail["work_products_total"] = total
            avail["work_products_shown"] = min(total, _PROJECT_WP_LIMIT)
    msgs = await _backend.get_messages(thread.id)
    avail["history_turns_total"] = max(0, len(msgs) - 1)  # exclude the current turn
    return avail


async def _thread_history(thread_id: str) -> list[dict[str, str]]:
    """Prior conversation for *thread_id* as [{role, content}], oldest first.

    The current user turn has already been persisted by the caller, so the last
    stored message is dropped — it is passed to the harness separately as the
    goal. Capped to the most recent ``_HISTORY_LIMIT`` turns.
    """
    msgs = await _backend.get_messages(thread_id)
    prior = msgs[:-1] if msgs else []
    out: list[dict[str, str]] = []
    for m in prior:
        role = _HISTORY_ROLE.get(m.actor_kind)
        if role and m.content and (m.status or "ok") != "error":
            out.append({"role": role, "content": m.content})
    return out[-_HISTORY_LIMIT:]


async def _invoke_agent(
    thread: ChatThreadRecord,
    user_content: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    tools: list[str] | None = None,
) -> ChatMessageRecord | None:
    """Attempt to route *user_content* to a domain agent and return its response.

    Returns ``None`` when no LLM is configured or no agent is registered
    for the thread's ``scope_kind``.  Returns a *system* error message
    record when the agent raises an exception.
    """
    with tracer.start_as_current_span("chat.invoke_agent") as span:
        span.set_attribute("scope_kind", thread.scope_kind)

        # Harness-backed path (MET-548, flagged) FIRST: the harness has its own
        # registry-based provider config (any of ~32 providers), so it must NOT
        # be gated by the legacy pydantic-ai ``is_llm_available()`` check, which
        # only recognizes 'openai'/'anthropic'. Gating it there silently skipped
        # the agent for openrouter / openai-codex / etc.
        if chat_harness_enabled():
            span.set_attribute("chat_backend", "harness")
            now = datetime.now(UTC)
            try:

                async def _on_delta(delta: str) -> None:
                    await notify_message_delta(thread.id, delta)

                async def _on_step(step: dict[str, object]) -> None:
                    await notify_agent_step(thread.id, step, "harness-agent")

                async def _on_context(stats: dict[str, object]) -> None:
                    await notify_context_stats(thread.id, stats)

                await notify_agent_typing(thread.id, "harness-agent")
                # Project-scoped threads lead with a project brief so the agent
                # reasons over the digital thread and scopes new work to it.
                history = await _project_brief(thread) + await _thread_history(thread.id)
                availability = await _context_availability(thread)
                text = await run_chat_turn_streaming(
                    user_content,
                    on_delta=_on_delta,
                    on_step=_on_step,
                    on_context=_on_context,
                    session_id=thread.id,
                    mcp_bridge=_mcp_bridge,
                    provider=provider,
                    model=model,
                    enabled_tools=tools,
                    history=history,
                    availability=availability,
                )
                await notify_agent_done(thread.id, "harness-agent")
                return ChatMessageRecord(
                    id=str(uuid4()),
                    thread_id=thread.id,
                    actor_id="harness-agent",
                    actor_kind="agent",
                    content=text,
                    created_at=now,
                    updated_at=now,
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.error("harness_chat_failed", error=str(exc))
                return ChatMessageRecord(
                    id=str(uuid4()),
                    thread_id=thread.id,
                    actor_id="system",
                    actor_kind="system",
                    content=f"Harness error: {exc}",
                    status="error",
                    created_at=now,
                    updated_at=now,
                )

        # Legacy pydantic-ai path (harness disabled): this one only supports
        # 'openai'/'anthropic', so keep its availability gate here.
        if not is_llm_available():
            logger.debug("llm_not_available_skipping_agent")
            span.set_attribute("skipped", True)
            return None

        agent = default_router.get_agent(
            scope_kind=thread.scope_kind,
            twin=_twin,
            mcp_bridge=_mcp_bridge,
        )

        if agent is None:
            logger.debug(
                "no_agent_for_scope",
                scope_kind=thread.scope_kind,
            )
            span.set_attribute("skipped", True)
            return None

        now = datetime.now(UTC)

        try:
            project_id = ""
            work_product_id = ""
            if thread.scope_kind == "project" and thread.scope_entity_id:
                project = await get_project_backend().get_project(thread.scope_entity_id)
                if project and project.work_products:
                    project_id = thread.scope_entity_id
                    work_product_id = project.work_products[0].id

            deps = MechanicalAgentDeps(
                twin=_twin,
                mcp_bridge=_mcp_bridge,
                session_id=str(uuid4()),
                branch="main",
                project_id=project_id,
                work_product_id=work_product_id,
            )

            llm_model = get_llm_model()
            result = await run_agent(prompt=user_content, deps=deps, model=llm_model)

            analysis = result.get("analysis", {})
            summary = analysis.get("summary", "")
            recommendations = result.get("recommendations", [])

            parts: list[str] = []
            if summary:
                parts.append(summary)
            else:
                passed = result.get("overall_passed", True)
                stress = result.get("max_stress_mpa", 0.0)
                region = result.get("critical_region", "")
                parts.append(f"**Analysis {'passed' if passed else 'failed'}.**")
                if stress:
                    parts.append(f"Max stress: {stress:.1f} MPa.")
                if region:
                    parts.append(f"Critical region: {region}.")
            if recommendations:
                parts.append("\n**Recommendations:**")
                for rec in recommendations:
                    parts.append(f"- {rec}")

            response_text = " ".join(parts) if parts else "Agent analysis complete."

            logger.info(
                "agent_response_generated",
                scope_kind=thread.scope_kind,
                overall_passed=result.get("overall_passed"),
            )
            span.set_attribute("agent_responded", True)

            return ChatMessageRecord(
                id=str(uuid4()),
                thread_id=thread.id,
                actor_id="mechanical-agent",
                actor_kind="agent",
                content=response_text,
                created_at=now,
                updated_at=now,
            )

        except Exception as exc:
            span.record_exception(exc)
            logger.error(
                "agent_invocation_failed",
                scope_kind=thread.scope_kind,
                error=str(exc),
            )
            return ChatMessageRecord(
                id=str(uuid4()),
                thread_id=thread.id,
                actor_id="system",
                actor_kind="system",
                content=f"Agent error: {exc}",
                status="error",
                created_at=now,
                updated_at=now,
            )


# ---------------------------------------------------------------------------
# Channel endpoints
# ---------------------------------------------------------------------------


@router.get("/channels", response_model=ChannelListResponse)
async def list_channels() -> ChannelListResponse:
    """Return all available chat channels."""
    channels_list = await _backend.list_channels()
    channels = [
        ChannelResponse(
            id=ch.id,
            name=ch.name,
            scope_kind=ch.scope_kind,
            created_at=ch.created_at,
        )
        for ch in channels_list
    ]
    return ChannelListResponse(channels=channels)


# ---------------------------------------------------------------------------
# Thread endpoints
# ---------------------------------------------------------------------------


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(
    channel_id: str | None = Query(default=None, description="Filter by channel ID"),
    scope_kind: str | None = Query(default=None, description="Filter by scope kind"),
    entity_id: str | None = Query(default=None, description="Filter by scope entity ID"),
    include_archived: bool = Query(default=False, description="Include archived threads"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(default=20, ge=1, le=100, description="Results per page"),
) -> ThreadListResponse:
    """List threads with optional filtering and pagination."""
    page_threads, total = await _backend.list_threads(
        channel_id=channel_id,
        scope_kind=scope_kind,
        entity_id=entity_id,
        include_archived=include_archived,
        page=page,
        per_page=per_page,
    )

    summaries = [
        ThreadSummaryResponse(
            id=t.id,
            channel_id=t.channel_id,
            scope_kind=t.scope_kind,
            scope_entity_id=t.scope_entity_id,
            title=t.title,
            archived=t.archived,
            created_at=t.created_at,
            last_message_at=t.last_message_at,
            message_count=await _backend.message_count(t.id),
        )
        for t in page_threads
    ]

    return ThreadListResponse(
        threads=summaries,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: str) -> ThreadResponse:
    """Return a single thread with all its messages."""
    thread = await _backend.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    msgs = await _backend.get_messages(thread_id)
    return ThreadResponse(
        id=thread.id,
        channel_id=thread.channel_id,
        scope_kind=thread.scope_kind,
        scope_entity_id=thread.scope_entity_id,
        title=thread.title,
        archived=thread.archived,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        messages=[_make_message_response(m) for m in msgs],
    )


@router.post("/threads", response_model=ThreadResponse, status_code=201)
async def create_thread(body: CreateThreadRequest) -> ThreadResponse:
    """Create a new thread, optionally with an initial message."""
    channel = await _backend.channel_for_scope(body.scope_kind)
    if channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"No channel found for scope_kind={body.scope_kind!r}",
        )

    thread_id_short = str(uuid4())[:8]
    title = body.title or f"Thread {thread_id_short}"

    thread = await _backend.create_thread(
        channel_id=channel.id,
        scope_kind=body.scope_kind,
        scope_entity_id=body.scope_entity_id,
        title=title,
    )

    messages: list[MessageResponse] = []

    if body.initial_message:
        msg = await _backend.add_message(
            thread_id=thread.id,
            actor_id="system",
            actor_kind="system",
            content=body.initial_message,
        )
        messages.append(_make_message_response(msg))

    return ThreadResponse(
        id=thread.id,
        channel_id=thread.channel_id,
        scope_kind=thread.scope_kind,
        scope_entity_id=thread.scope_entity_id,
        title=thread.title,
        archived=thread.archived,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Message endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageResponse,
    status_code=201,
)
async def send_message(
    thread_id: str, body: SendMessageRequest, request: Request
) -> MessageResponse:
    """Append a message to an existing thread.

    After persisting the user message, the handler routes it to the
    appropriate domain agent (when an LLM is configured).  The agent's
    response is inserted into the thread automatically.
    """
    thread = await _backend.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    msg = await _backend.add_message(
        thread_id=thread_id,
        actor_id=body.actor_id,
        actor_kind=body.actor_kind,
        content=body.content,
        graph_ref_node=body.graph_ref_node,
        graph_ref_type=body.graph_ref_type,
        graph_ref_label=body.graph_ref_label,
    )

    # --- Agent invocation (async) ----------------------------------------
    if body.actor_kind == "user":
        try:
            agent_msg = await _run_cancellable_on_disconnect(
                request.is_disconnected,
                _invoke_agent(
                    thread, body.content, provider=body.provider, model=body.model, tools=body.tools
                ),
            )
        except asyncio.CancelledError:
            # The client is gone -- no response will ever be read, so there's
            # nothing left to do but stop the turn and record why.
            logger.warning("chat_turn_cancelled_client_disconnected", thread_id=thread_id)
            return _make_message_response(msg)
        if agent_msg is not None:
            await _backend.add_message(
                thread_id=thread_id,
                actor_id=agent_msg.actor_id,
                actor_kind=agent_msg.actor_kind,
                content=agent_msg.content,
                status=agent_msg.status,
            )

    return _make_message_response(msg)


# ---------------------------------------------------------------------------
# SSE streaming endpoint
# ---------------------------------------------------------------------------


@router.get("/threads/{thread_id}/stream")
async def stream_thread_events(thread_id: str) -> StreamingResponse:
    """Stream real-time events for a chat thread via Server-Sent Events.

    The client receives events as they occur:

    - ``message.created`` -- a new message was added
    - ``agent.typing``    -- an agent is processing
    - ``context.stats``   -- the turn's context-window snapshot: tokens used vs.
      the model's window, broken down by system prompt / project brief / history /
      tool schemas / message, with included-vs-available counts (harness turns)
    - ``agent.step``      -- one reasoning/tool-call step in the agent's trace
    - ``message.delta``   -- one token/chunk of the streaming answer
    - ``agent.done``      -- an agent finished
    - ``error``           -- an error occurred

    The connection stays open until the client disconnects or the server
    closes the stream.
    """
    thread = await _backend.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    logger.info("sse_stream_requested", thread_id=thread_id)

    return StreamingResponse(
        stream_thread(thread_id, manager=stream_manager),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
