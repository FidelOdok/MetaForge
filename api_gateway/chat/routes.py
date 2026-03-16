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

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api_gateway.chat.agent_router import default_router
from api_gateway.chat.backend import ChatBackend, InMemoryChatBackend
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
from api_gateway.chat.streaming import stream_manager, stream_thread
from api_gateway.projects.routes import _backend as _project_backend
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


async def _invoke_agent(
    thread: ChatThreadRecord,
    user_content: str,
) -> ChatMessageRecord | None:
    """Attempt to route *user_content* to a domain agent and return its response.

    Returns ``None`` when no LLM is configured or no agent is registered
    for the thread's ``scope_kind``.  Returns a *system* error message
    record when the agent raises an exception.
    """
    with tracer.start_as_current_span("chat.invoke_agent") as span:
        span.set_attribute("scope_kind", thread.scope_kind)

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
                project = await _project_backend.get_project(thread.scope_entity_id)
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
async def send_message(thread_id: str, body: SendMessageRequest) -> MessageResponse:
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
        agent_msg = await _invoke_agent(thread, body.content)
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
