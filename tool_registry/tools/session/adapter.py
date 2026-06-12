"""MCP adapter exposing agent-session capture over the wire (MET-494).

Lets an external agent (Claude Code, Cursor, …) record its own narrative —
thoughts, decisions, the tool-call timeline — into the agent-session store
the gateway and sidecar share (MET-493). This is the explicit "Layer C"
surface; the server-side auto-capture middleware (MET-496) takes its
``session.start`` result as the takeover binding so auto-captured actions
attach to the agent's own session.

Mirrors ``ProjectServer`` (MET-427): an ``McpToolServer`` subclass with a
late-binding store. To respect the layer rule (``tool_registry`` may not
import ``api_gateway``) the store is consumed through a structural
``AgentSessionStoreLike`` protocol — ``api_gateway.sessions.backend``'s
stores satisfy it unchanged.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.session")

_EVENT_TYPES = ["thought", "action", "decision", "observation", "error", "result"]
_TERMINAL_STATUSES = ["completed", "failed"]


@runtime_checkable
class SessionLike(Protocol):
    """Structural shape of a created session (``SessionResponse`` satisfies it)."""

    id: str


@runtime_checkable
class AgentSessionStoreLike(Protocol):
    """Subset of ``api_gateway.sessions.backend.AgentSessionStore`` used here."""

    async def create_session(
        self,
        *,
        agent_code: str,
        task_type: str,
        title: str | None = ...,
        project_id: str | None = ...,
        source: str = ...,
    ) -> SessionLike: ...

    async def append_event(
        self,
        session_id: str,
        *,
        type: str,
        message: str,
        data: dict[str, Any] | None = ...,
    ) -> tuple[str, int]: ...

    async def complete_session(
        self,
        session_id: str,
        *,
        status: str,
        summary: str | None = ...,
    ) -> SessionLike: ...


class SessionServer(McpToolServer):
    """MCP adapter around an ``AgentSessionStoreLike`` instance.

    Constructor takes an optional store so registry bootstrap can be lazy;
    ``set_store()`` is the late-binding hook (mirrors ``ProjectServer``).
    """

    def __init__(self, store: AgentSessionStoreLike | None = None) -> None:
        super().__init__(adapter_id="session", version="0.1.0")
        self._store: AgentSessionStoreLike | None = store
        self._register_tools()

    def set_store(self, store: AgentSessionStoreLike) -> None:
        self._store = store
        logger.info("session_mcp_store_bound", store=type(store).__name__)

    @property
    def store(self) -> AgentSessionStoreLike:
        if self._store is None:
            raise RuntimeError(
                "SessionServer.store was called before set_store(); ensure "
                "the agent_session_store is wired into bootstrap."
            )
        return self._store

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        limits = ResourceLimits(max_memory_mb=128, max_cpu_seconds=10)

        self.register_tool(
            manifest=ToolManifest(
                tool_id="session.start",
                adapter_id="session",
                name="Start Agent Session",
                description=(
                    "Open an agent session to record your narrative (thoughts, "
                    "decisions, tool-call timeline). Returns the session id to "
                    "pass to session.log_event / session.complete. Auto-captured "
                    "tool actions attach to this session once started."
                ),
                capability="session_capture",
                input_schema={
                    "type": "object",
                    "properties": {
                        "agent_code": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Driving agent id (e.g. claude-code).",
                        },
                        "task_type": {
                            "type": "string",
                            "minLength": 1,
                            "description": "What the session does (design, debug, review).",
                        },
                        "title": {
                            "type": "string",
                            "description": "Optional human-readable title.",
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Optional project UUID to associate the session with.",
                        },
                    },
                    "required": ["agent_code", "task_type"],
                },
                output_schema={
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                },
                phase=1,
                resource_limits=limits,
            ),
            handler=self.handle_start,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="session.log_event",
                adapter_id="session",
                name="Log Session Event",
                description=(
                    "Append one event to a session timeline. Use type=thought "
                    "for reasoning, decision for a design choice, action for a "
                    "step taken, observation/error/result as fitting."
                ),
                capability="session_capture",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session id from session.start.",
                        },
                        "type": {
                            "type": "string",
                            "enum": _EVENT_TYPES,
                            "description": "Event kind.",
                        },
                        "message": {
                            "type": "string",
                            "minLength": 1,
                            "description": "The event text (the thought / decision / action).",
                        },
                        "data": {
                            "type": "object",
                            "description": "Optional structured payload (rationale, refs, ids).",
                        },
                    },
                    "required": ["session_id", "type", "message"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "seq": {"type": "integer"},
                    },
                },
                phase=1,
                resource_limits=limits,
            ),
            handler=self.handle_log_event,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="session.complete",
                adapter_id="session",
                name="Complete Agent Session",
                description="Close a session with a terminal status and optional summary.",
                capability="session_capture",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session id from session.start.",
                        },
                        "status": {
                            "type": "string",
                            "enum": _TERMINAL_STATUSES,
                            "description": "Terminal status.",
                        },
                        "summary": {"type": "string", "description": "Optional closing summary."},
                    },
                    "required": ["session_id", "status"],
                },
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                },
                phase=1,
                resource_limits=limits,
            ),
            handler=self.handle_complete,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("session.mcp.start") as span:
            agent_code = arguments.get("agent_code")
            task_type = arguments.get("task_type")
            if not agent_code or not isinstance(agent_code, str):
                raise ValueError("session.start: 'agent_code' is required (non-empty string)")
            if not task_type or not isinstance(task_type, str):
                raise ValueError("session.start: 'task_type' is required (non-empty string)")
            title = arguments.get("title")
            project_id = arguments.get("project_id")
            span.set_attribute("session.agent_code", agent_code)
            session = await self.store.create_session(
                agent_code=agent_code,
                task_type=task_type,
                title=title if isinstance(title, str) else None,
                project_id=project_id if isinstance(project_id, str) else None,
            )
            logger.info("session_mcp_start", session_id=session.id, agent_code=agent_code)
            return {"session_id": session.id}

    async def handle_log_event(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("session.mcp.log_event") as span:
            session_id = arguments.get("session_id")
            event_type = arguments.get("type")
            message = arguments.get("message")
            if not session_id or not isinstance(session_id, str):
                raise ValueError("session.log_event: 'session_id' is required (string)")
            if event_type not in _EVENT_TYPES:
                raise ValueError(f"session.log_event: 'type' must be one of {_EVENT_TYPES}")
            if not message or not isinstance(message, str):
                raise ValueError("session.log_event: 'message' is required (non-empty string)")
            data = arguments.get("data")
            if data is not None and not isinstance(data, dict):
                raise ValueError("session.log_event: 'data' must be an object when provided")
            span.set_attribute("session.id", session_id)
            span.set_attribute("session.event_type", event_type)
            event_id, seq = await self.store.append_event(
                session_id, type=event_type, message=message, data=data
            )
            logger.info("session_mcp_log_event", session_id=session_id, seq=seq, type=event_type)
            return {"event_id": event_id, "seq": seq}

    async def handle_complete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("session.mcp.complete") as span:
            session_id = arguments.get("session_id")
            status = arguments.get("status")
            if not session_id or not isinstance(session_id, str):
                raise ValueError("session.complete: 'session_id' is required (string)")
            if status not in _TERMINAL_STATUSES:
                raise ValueError(f"session.complete: 'status' must be one of {_TERMINAL_STATUSES}")
            summary = arguments.get("summary")
            span.set_attribute("session.id", session_id)
            await self.store.complete_session(
                session_id,
                status=status,
                summary=summary if isinstance(summary, str) else None,
            )
            logger.info("session_mcp_complete", session_id=session_id, status=status)
            return {"ok": True}
