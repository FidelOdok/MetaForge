"""Session-store decorator that deposits an experience on completion (MET-567).

There are three independent callers of ``complete_session``: the REST route
(``PATCH /v1/sessions/{id}`` — used by ``metaforge-capture``, the Claude Code
hooks, and ``forge``), the ``session.complete`` MCP tool
(``tool_registry/tools/session/adapter.py``), and the sidecar's idle-rollover
of an implicit capture session (``metaforge/mcp/capture.py``). Hooking the
memory deposit into each one separately would leave the next caller to
rediscover the requirement, so it is hooked *once*, here, by wrapping the
store both bootstraps already build.

The wrapper is deliberately transparent: every method delegates, unknown
attributes pass through via ``__getattr__``, and the deposit runs after the
inner store has committed, best-effort. A memory-tier failure can therefore
never turn a successful session completion into an error.
"""

from __future__ import annotations

from typing import Any

import structlog

from api_gateway.sessions.backend import AgentSessionStore
from api_gateway.sessions.schemas import SessionResponse

logger = structlog.get_logger(__name__)


class ExperienceBridgingSessionStore(AgentSessionStore):
    """Delegating ``AgentSessionStore`` that records completed sessions as experiences."""

    def __init__(self, inner: AgentSessionStore, bridge: Any) -> None:
        self._inner = inner
        self._bridge = bridge

    @property
    def inner(self) -> AgentSessionStore:
        """The wrapped store (for callers that need the concrete backend)."""
        return self._inner

    # -- delegation ----------------------------------------------------------

    async def create_session(
        self,
        *,
        agent_code: str,
        task_type: str,
        title: str | None = None,
        project_id: str | None = None,
        source: str = "external",
    ) -> SessionResponse:
        return await self._inner.create_session(
            agent_code=agent_code,
            task_type=task_type,
            title=title,
            project_id=project_id,
            source=source,
        )

    async def append_event(
        self,
        session_id: str,
        *,
        type: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> tuple[str, int]:
        return await self._inner.append_event(session_id, type=type, message=message, data=data)

    async def get_session(self, session_id: str) -> SessionResponse | None:
        return await self._inner.get_session(session_id)

    async def list_sessions(self, project_id: str | None = None) -> list[SessionResponse]:
        return await self._inner.list_sessions(project_id)

    async def abandon_stale_sessions(self, *, older_than_seconds: float) -> int:
        # The bulk sweep transitions rows with a single UPDATE and never
        # materialises them, so there is nothing to summarise here. Abandoned
        # sessions are also the least informative kind — their driver vanished
        # mid-trajectory — so skipping the deposit loses little.
        return await self._inner.abandon_stale_sessions(older_than_seconds=older_than_seconds)

    # -- the one behaviour this wrapper adds ---------------------------------

    async def complete_session(
        self,
        session_id: str,
        *,
        status: str,
        summary: str | None = None,
    ) -> SessionResponse:
        session = await self._inner.complete_session(session_id, status=status, summary=summary)
        try:
            await self._bridge.on_session_completed(session)
        except Exception as exc:  # noqa: BLE001 — the deposit is never load-bearing
            logger.warning(
                "session_experience_bridge_failed", session_id=session_id, error=str(exc)
            )
        return session

    def __getattr__(self, name: str) -> Any:
        # Pass through anything the ABC doesn't declare (e.g. a backend's own
        # ``close``) so wrapping stays invisible to callers.
        return getattr(self._inner, name)


def wrap_with_experience_bridge(
    store: Any,
    memory_store: Any,
    embeddings: Any,
) -> Any:
    """Wrap ``store`` so completed sessions deposit experiences.

    Returns ``store`` unchanged when any dependency is missing (no session
    store, no experience store, or no embedder) — the deposit is an
    enhancement, never a boot requirement.
    """
    if store is None or memory_store is None or embeddings is None:
        return store
    try:
        from digital_twin.memory.experience_recorder import MemoryExperienceRecorder
        from digital_twin.memory.session_bridge import SessionExperienceBridge

        bridge = SessionExperienceBridge(
            MemoryExperienceRecorder(store=memory_store, embeddings=embeddings)
        )
    except Exception as exc:  # noqa: BLE001 — degrade to the bare store
        logger.warning("session_experience_bridge_wiring_failed", error=str(exc))
        return store
    logger.info("session_experience_bridge_wired")
    return ExperienceBridgingSessionStore(store, bridge)
