"""Chat REST endpoints for the MetaForge Gateway.

Provides CRUD operations on chat channels, threads, and messages.
All state is held in an in-memory ``ChatStore`` for now; production
will swap in a PostgreSQL-backed implementation.

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

from api_gateway.chat.agent_router import default_router
from api_gateway.chat.models import (
    ChatChannelRecord,
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
from domain_agents.base_agent import is_llm_available
from domain_agents.mechanical.pydantic_ai_agent import (
    MechanicalAgentDeps,
    run_agent,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.chat.routes")

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"name": "Session Chat", "scope_kind": "session"},
    {"name": "Approval Chat", "scope_kind": "approval"},
    {"name": "BOM Discussion", "scope_kind": "bom-entry"},
    {"name": "Digital Twin", "scope_kind": "digital-twin-node"},
    {"name": "Project Chat", "scope_kind": "project"},
]


class ChatStore:
    """Dict-backed chat storage (channels, threads, messages).

    This is intentionally simple -- production code will use PostgreSQL
    via SQLAlchemy / asyncpg.
    """

    def __init__(self) -> None:
        self.channels: dict[str, ChatChannelRecord] = {}
        self.threads: dict[str, ChatThreadRecord] = {}
        self.messages: dict[str, list[ChatMessageRecord]] = {}

    @classmethod
    def create(cls) -> ChatStore:
        """Return a new store pre-populated with the default channels."""
        store = cls()
        for ch in _DEFAULT_CHANNELS:
            channel = ChatChannelRecord(
                id=str(uuid4()),
                name=ch["name"],
                scope_kind=ch["scope_kind"],
            )
            store.channels[channel.id] = channel
        return store

    # -- helpers ----------------------------------------------------------

    def channel_for_scope(self, scope_kind: str) -> ChatChannelRecord | None:
        """Return the first channel matching *scope_kind*, or ``None``."""
        for ch in self.channels.values():
            if ch.scope_kind == scope_kind:
                return ch
        return None

    def message_count(self, thread_id: str) -> int:
        return len(self.messages.get(thread_id, []))


# ---------------------------------------------------------------------------
# Module-level store & router
# ---------------------------------------------------------------------------

store = ChatStore.create()

router = APIRouter(prefix="/v1/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# Module-level singletons for agent invocation
# ---------------------------------------------------------------------------

_twin = InMemoryTwinAPI.create()
_mcp_bridge = InMemoryMcpBridge()

# Register mock MCP responses so the mechanical agent can operate without
# real tool containers (Phase 1 in-memory testing).
_mcp_bridge.register_tool_response(
    "calculix.run_fea",
    {
        "max_von_mises": {"bracket_arm": 120.5, "mounting_hole": 85.3},
        "solver_time": 0.42,
    },
)
_mcp_bridge.register_tool_response(
    "freecad.mesh",
    {
        "mesh_file": "/tmp/mesh_output.inp",
        "num_nodes": 1024,
        "num_elements": 2048,
    },
)


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
            deps = MechanicalAgentDeps(
                twin=_twin,
                mcp_bridge=_mcp_bridge,
                session_id=str(uuid4()),
                branch="main",
            )

            result = await run_agent(prompt=user_content, deps=deps)

            response_text = (
                result.get("analysis", {}).get("summary", "")
                or f"Agent analysis complete. Passed: {result.get('overall_passed', True)}."
            )

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
def list_channels() -> ChannelListResponse:
    """Return all available chat channels."""
    channels = [
        ChannelResponse(
            id=ch.id,
            name=ch.name,
            scope_kind=ch.scope_kind,
            created_at=ch.created_at,
        )
        for ch in store.channels.values()
    ]
    return ChannelListResponse(channels=channels)


# ---------------------------------------------------------------------------
# Thread endpoints
# ---------------------------------------------------------------------------


@router.get("/threads", response_model=ThreadListResponse)
def list_threads(
    channel_id: str | None = Query(default=None, description="Filter by channel ID"),
    scope_kind: str | None = Query(default=None, description="Filter by scope kind"),
    entity_id: str | None = Query(default=None, description="Filter by scope entity ID"),
    include_archived: bool = Query(default=False, description="Include archived threads"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(default=20, ge=1, le=100, description="Results per page"),
) -> ThreadListResponse:
    """List threads with optional filtering and pagination."""
    threads = list(store.threads.values())

    # -- filtering --------------------------------------------------------
    if not include_archived:
        threads = [t for t in threads if not t.archived]
    if channel_id is not None:
        threads = [t for t in threads if t.channel_id == channel_id]
    if scope_kind is not None:
        threads = [t for t in threads if t.scope_kind == scope_kind]
    if entity_id is not None:
        threads = [t for t in threads if t.scope_entity_id == entity_id]

    # -- sort by last_message_at descending -------------------------------
    threads.sort(key=lambda t: t.last_message_at, reverse=True)

    total = len(threads)

    # -- pagination -------------------------------------------------------
    start = (page - 1) * per_page
    page_threads = threads[start : start + per_page]

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
            message_count=store.message_count(t.id),
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
def get_thread(thread_id: str) -> ThreadResponse:
    """Return a single thread with all its messages."""
    thread = store.threads.get(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    msgs = store.messages.get(thread_id, [])
    return ThreadResponse(
        id=thread.id,
        channel_id=thread.channel_id,
        scope_kind=thread.scope_kind,
        scope_entity_id=thread.scope_entity_id,
        title=thread.title,
        archived=thread.archived,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        messages=[
            MessageResponse(
                id=m.id,
                thread_id=m.thread_id,
                actor_id=m.actor_id,
                actor_kind=m.actor_kind,
                content=m.content,
                status=m.status,
                graph_ref_node=m.graph_ref_node,
                graph_ref_type=m.graph_ref_type,
                graph_ref_label=m.graph_ref_label,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in msgs
        ],
    )


@router.post("/threads", response_model=ThreadResponse, status_code=201)
def create_thread(body: CreateThreadRequest) -> ThreadResponse:
    """Create a new thread, optionally with an initial message."""
    # Resolve channel by scope_kind
    channel = store.channel_for_scope(body.scope_kind)
    if channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"No channel found for scope_kind={body.scope_kind!r}",
        )

    now = datetime.now(UTC)
    thread_id = str(uuid4())
    title = body.title or f"Thread {thread_id[:8]}"

    thread = ChatThreadRecord(
        id=thread_id,
        channel_id=channel.id,
        scope_kind=body.scope_kind,
        scope_entity_id=body.scope_entity_id,
        title=title,
        created_at=now,
        last_message_at=now,
    )
    store.threads[thread_id] = thread
    store.messages[thread_id] = []

    messages: list[MessageResponse] = []

    if body.initial_message:
        msg = ChatMessageRecord(
            id=str(uuid4()),
            thread_id=thread_id,
            actor_id="system",
            actor_kind="system",
            content=body.initial_message,
            created_at=now,
            updated_at=now,
        )
        store.messages[thread_id].append(msg)
        messages.append(
            MessageResponse(
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
        )

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
    thread = store.threads.get(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    now = datetime.now(UTC)
    msg = ChatMessageRecord(
        id=str(uuid4()),
        thread_id=thread_id,
        actor_id=body.actor_id,
        actor_kind=body.actor_kind,
        content=body.content,
        graph_ref_node=body.graph_ref_node,
        graph_ref_type=body.graph_ref_type,
        graph_ref_label=body.graph_ref_label,
        created_at=now,
        updated_at=now,
    )
    store.messages.setdefault(thread_id, []).append(msg)

    # Update thread timestamp
    thread.last_message_at = now

    # --- Agent invocation (async) ----------------------------------------
    if body.actor_kind == "user":
        agent_msg = await _invoke_agent(thread, body.content)
        if agent_msg is not None:
            store.messages[thread_id].append(agent_msg)
            thread.last_message_at = agent_msg.created_at

    return _make_message_response(msg)
