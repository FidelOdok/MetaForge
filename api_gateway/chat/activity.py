"""handle_chat_message Temporal activity -- context assembly and response generation.

This module implements the core Temporal activity for processing chat messages
in MetaForge. It assembles context based on the chat scope, generates a
response (stubbed for now), and persists the result.

Publishes ChatTypingEvent events at start/end of processing.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from api_gateway.chat.models import ChatMessageRecord
from orchestrator.event_bus.events import ChatTypingEvent, EventType


class ChatContextAssembler:
    """Assembles context for chat responses based on the scope kind.

    Each scope_kind maps to a different context shape reflecting the
    entities relevant to that scope (session traces, approval diffs,
    BOM properties, digital-twin node data, or project-level summaries).
    """

    SUPPORTED_SCOPES: frozenset[str] = frozenset(
        {
            "session",
            "approval",
            "bom-entry",
            "digital-twin-node",
            "project",
        }
    )

    def assemble(self, scope_kind: str, scope_entity_id: str) -> dict[str, Any]:
        """Return a context dict appropriate for the given scope_kind.

        Parameters
        ----------
        scope_kind:
            One of the supported scope kinds (session, approval, bom-entry,
            digital-twin-node, project).
        scope_entity_id:
            The identifier of the scoped entity (e.g. session ID, approval ID).

        Returns
        -------
        dict[str, Any]
            Context dictionary whose shape depends on scope_kind.

        Raises
        ------
        ValueError
            If scope_kind is not one of the supported values.
        """
        if scope_kind not in self.SUPPORTED_SCOPES:
            raise ValueError(
                f"Unsupported scope_kind: {scope_kind!r}. "
                f"Must be one of {sorted(self.SUPPORTED_SCOPES)}."
            )

        handler = getattr(self, f"_assemble_{scope_kind.replace('-', '_')}")
        return handler(scope_entity_id)

    # ------------------------------------------------------------------
    # Scope-specific assemblers (stubs -- will query Neo4j in production)
    # ------------------------------------------------------------------

    def _assemble_session(self, entity_id: str) -> dict[str, Any]:
        """Session scope: traces, artifacts, recent mutations."""
        return {
            "scope_kind": "session",
            "session_id": entity_id,
            "traces": [],
            "artifacts": [],
            "recent_mutations": [],
        }

    def _assemble_approval(self, entity_id: str) -> dict[str, Any]:
        """Approval scope: diffs, description, session context."""
        return {
            "scope_kind": "approval",
            "approval_id": entity_id,
            "diffs": [],
            "description": "",
            "session_context": {},
        }

    def _assemble_bom_entry(self, entity_id: str) -> dict[str, Any]:
        """BOM entry scope: properties, alternates, supply chain risk."""
        return {
            "scope_kind": "bom-entry",
            "bom_entry_id": entity_id,
            "properties": {},
            "alternates": [],
            "supply_chain_risk": "unknown",
            "graph_neighbors": [],
        }

    def _assemble_digital_twin_node(self, entity_id: str) -> dict[str, Any]:
        """Digital-twin-node scope: properties, requirements, simulation."""
        return {
            "scope_kind": "digital-twin-node",
            "node_id": entity_id,
            "properties": {},
            "connected_requirements": [],
            "simulation_results": [],
        }

    def _assemble_project(self, entity_id: str) -> dict[str, Any]:
        """Project scope: recent sessions, gate readiness, open approvals."""
        return {
            "scope_kind": "project",
            "project_id": entity_id,
            "recent_sessions": [],
            "gate_readiness": {},
            "open_approvals": [],
        }


# ---------------------------------------------------------------------------
# Pydantic models for the Temporal activity contract
# ---------------------------------------------------------------------------


class HandleChatMessageInput(BaseModel):
    """Input contract for the handle_chat_message Temporal activity."""

    thread_id: str = Field(description="ID of the chat thread")
    actor_id: str = Field(description="ID of the message sender")
    actor_kind: str = Field(description="Kind of sender: user, agent, or system")
    content: str = Field(description="Message content from the sender")
    scope_kind: str = Field(
        description="Scope kind determining context assembly strategy"
    )
    scope_entity_id: str = Field(
        description="Entity ID within the scope (e.g. session ID, node ID)"
    )


class HandleChatMessageOutput(BaseModel):
    """Output contract for the handle_chat_message Temporal activity."""

    message_id: str = Field(description="UUID of the generated response message")
    response_content: str = Field(description="The generated response text")
    context_used: dict[str, Any] = Field(
        description="Context dict that was assembled for response generation"
    )
    duration_ms: float = Field(
        description="Wall-clock duration of the activity in milliseconds"
    )


# ---------------------------------------------------------------------------
# Temporal activity
# ---------------------------------------------------------------------------

# Module-level assembler instance (can be replaced in tests)
_assembler = ChatContextAssembler()


def _build_typing_event(
    thread_id: str,
    actor_id: str,
    is_typing: bool,
) -> ChatTypingEvent:
    """Build a ChatTypingEvent for the given thread/actor."""
    return ChatTypingEvent(
        id=str(uuid4()),
        type=EventType.CHAT_AGENT_TYPING,
        timestamp=datetime.now(UTC).isoformat(),
        source="chat-activity",
        thread_id=thread_id,
        actor_id=actor_id,
        agent_code="chat-responder",
        is_typing=is_typing,
    )


def _generate_response(content: str, context: dict[str, Any]) -> str:
    """Generate a chat response (stub).

    In production this will call the LLM with streaming and publish
    ``chat.message.chunk`` events. For now it returns a formatted summary.
    """
    scope_kind = context.get("scope_kind", "unknown")
    context_keys = sorted(k for k in context if k != "scope_kind")
    return (
        f"[stub] Received message in {scope_kind} scope. "
        f"Context keys: {', '.join(context_keys)}. "
        f"User said: {content}"
    )


async def handle_chat_message(
    input: HandleChatMessageInput,  # noqa: A002
    *,
    assembler: ChatContextAssembler | None = None,
) -> HandleChatMessageOutput:
    """Temporal activity: process an incoming chat message.

    Steps:
    1. Assemble context via ChatContextAssembler
    2. Publish ChatTypingEvent (start)
    3. Generate response (stub for now)
    4. Create ChatMessageRecord for the response
    5. Publish ChatTypingEvent (stop)
    6. Return HandleChatMessageOutput

    Parameters
    ----------
    input:
        The activity input containing message details and scope.
    assembler:
        Optional assembler override (useful for testing).

    Returns
    -------
    HandleChatMessageOutput
        The generated response with metadata.
    """
    start = time.monotonic()
    ctx_assembler = assembler or _assembler

    # 1. Assemble context
    context = ctx_assembler.assemble(input.scope_kind, input.scope_entity_id)

    # 2. Build typing-start event (stub -- would publish to Kafka)
    _typing_start = _build_typing_event(  # noqa: F841
        thread_id=input.thread_id,
        actor_id="system",
        is_typing=True,
    )

    # 3. Generate response
    response_text = _generate_response(input.content, context)

    # 4. Persist response message record (stub -- would write to PostgreSQL)
    message_id = str(uuid4())
    _record = ChatMessageRecord(  # noqa: F841
        id=message_id,
        thread_id=input.thread_id,
        actor_id="system",
        actor_kind="system",
        content=response_text,
        status="sent",
    )

    # 5. Build typing-stop event (stub -- would publish to Kafka)
    _typing_stop = _build_typing_event(  # noqa: F841
        thread_id=input.thread_id,
        actor_id="system",
        is_typing=False,
    )

    elapsed_ms = (time.monotonic() - start) * 1000.0

    # 6. Return output
    return HandleChatMessageOutput(
        message_id=message_id,
        response_content=response_text,
        context_used=context,
        duration_ms=elapsed_ms,
    )
