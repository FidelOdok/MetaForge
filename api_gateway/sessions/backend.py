"""Agent-session storage backends — in-memory and PostgreSQL (MET-493).

Stores externally-recorded agent sessions (MCP/CLI agents writing their
own narrative + decision timeline). Internal Temporal ``WorkflowRun``s are
NOT stored here — the sessions routes merge them in at read time.

Mirrors the project-backend pattern (``api_gateway/projects/backend.py``):
an ``AgentSessionStore`` ABC with ``InMemoryAgentSessionStore`` and
``PgAgentSessionStore``, selected by :func:`create_agent_session_store`
based on ``DATABASE_URL``. Both the gateway and the ``mcp-http`` sidecar
build the store from the same env, so they share one Postgres table.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from api_gateway.sessions.schemas import SessionEventResponse, SessionResponse
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.sessions.backend")


class SessionNotFoundError(Exception):
    """Raised when an operation targets a session id that does not exist."""


class SessionClosedError(Exception):
    """Raised when appending to / completing an already-completed session."""


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class AgentSessionStore(ABC):
    """Async interface for externally-recorded agent sessions."""

    @abstractmethod
    async def create_session(
        self,
        *,
        agent_code: str,
        task_type: str,
        title: str | None = None,
        project_id: str | None = None,
        source: str = "external",
    ) -> SessionResponse: ...

    @abstractmethod
    async def append_event(
        self,
        session_id: str,
        *,
        type: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> tuple[str, int]:
        """Append an event; return ``(event_id, seq)``. ``seq`` is a
        server-assigned monotonic counter within the session.

        Raises :class:`SessionNotFoundError` / :class:`SessionClosedError`.
        """
        ...

    @abstractmethod
    async def complete_session(
        self,
        session_id: str,
        *,
        status: str,
        summary: str | None = None,
    ) -> SessionResponse:
        """Close a session. Raises if unknown or already completed."""
        ...

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionResponse | None: ...

    @abstractmethod
    async def list_sessions(self) -> list[SessionResponse]: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryAgentSessionStore(AgentSessionStore):
    """Dict-backed agent-session storage (development / tests)."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionResponse] = {}

    @classmethod
    def create(cls) -> InMemoryAgentSessionStore:
        return cls()

    async def create_session(
        self,
        *,
        agent_code: str,
        task_type: str,
        title: str | None = None,
        project_id: str | None = None,
        source: str = "external",
    ) -> SessionResponse:
        session_id = str(uuid4())
        # title / project_id are persisted by the Pg row for future use but
        # are not part of SessionResponse today (dashboard doesn't read them),
        # so the in-memory store accepts and drops them — same observable shape.
        session = SessionResponse(
            id=session_id,
            agent_code=agent_code,
            task_type=task_type,
            status="running",
            started_at=_now_iso(),
            completed_at=None,
            events=[],
            run_id=None,
            summary=None,
            source=source,
        )
        self._sessions[session_id] = session
        logger.info("agent_session_created", session_id=session_id, agent_code=agent_code)
        return session

    async def append_event(
        self,
        session_id: str,
        *,
        type: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> tuple[str, int]:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.status != "running":
            raise SessionClosedError(session_id)
        seq = len(session.events) + 1
        event_id = str(uuid4())
        session.events.append(
            SessionEventResponse(
                id=event_id,
                timestamp=_now_iso(),
                type=type,
                agent_code=session.agent_code,
                message=message,
                data=data or {},
            )
        )
        logger.info("agent_session_event_appended", session_id=session_id, seq=seq, type=type)
        return event_id, seq

    async def complete_session(
        self,
        session_id: str,
        *,
        status: str,
        summary: str | None = None,
    ) -> SessionResponse:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.status != "running":
            raise SessionClosedError(session_id)
        session.status = status
        session.summary = summary
        session.completed_at = _now_iso()
        logger.info("agent_session_completed", session_id=session_id, status=status)
        return session

    async def get_session(self, session_id: str) -> SessionResponse | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[SessionResponse]:
        return list(self._sessions.values())


# ---------------------------------------------------------------------------
# PostgreSQL implementation
# ---------------------------------------------------------------------------


class PgAgentSessionStore(AgentSessionStore):
    """PostgreSQL-backed agent-session storage via SQLAlchemy async sessions."""

    async def create_session(
        self,
        *,
        agent_code: str,
        task_type: str,
        title: str | None = None,
        project_id: str | None = None,
        source: str = "external",
    ) -> SessionResponse:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AgentSessionRow

        session_id = str(uuid4())
        now = datetime.now(UTC)
        async with get_session() as session:
            row = AgentSessionRow(
                id=session_id,
                agent_code=agent_code,
                task_type=task_type,
                title=title,
                project_id=project_id,
                status="running",
                summary=None,
                source=source,
                started_at=now,
                completed_at=None,
            )
            session.add(row)
            await session.flush()
            logger.info("agent_session_created_pg", session_id=session_id, agent_code=agent_code)
            return self._row_to_response(row, [])

    async def append_event(
        self,
        session_id: str,
        *,
        type: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> tuple[str, int]:
        from sqlalchemy import func, select

        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AgentSessionEventRow, AgentSessionRow

        async with get_session() as session:
            row = await session.get(AgentSessionRow, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)
            if row.status != "running":
                raise SessionClosedError(session_id)
            max_seq = await session.scalar(
                select(func.max(AgentSessionEventRow.seq)).where(
                    AgentSessionEventRow.session_id == session_id
                )
            )
            seq = int(max_seq or 0) + 1
            event_id = str(uuid4())
            session.add(
                AgentSessionEventRow(
                    id=event_id,
                    session_id=session_id,
                    seq=seq,
                    type=type,
                    message=message,
                    data=data or {},
                    timestamp=datetime.now(UTC),
                )
            )
            await session.flush()
            logger.info(
                "agent_session_event_appended_pg", session_id=session_id, seq=seq, type=type
            )
            return event_id, seq

    async def complete_session(
        self,
        session_id: str,
        *,
        status: str,
        summary: str | None = None,
    ) -> SessionResponse:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AgentSessionRow

        async with get_session() as session:
            row = await session.get(AgentSessionRow, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)
            if row.status != "running":
                raise SessionClosedError(session_id)
            row.status = status
            row.summary = summary
            row.completed_at = datetime.now(UTC)
            await session.flush()
            events = await self._load_events(session, session_id)
            logger.info("agent_session_completed_pg", session_id=session_id, status=status)
            return self._row_to_response(row, events)

    async def get_session(self, session_id: str) -> SessionResponse | None:
        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AgentSessionRow

        async with get_session() as session:
            row = await session.get(AgentSessionRow, session_id)
            if row is None:
                return None
            events = await self._load_events(session, session_id)
            return self._row_to_response(row, events)

    async def list_sessions(self) -> list[SessionResponse]:
        from sqlalchemy import select

        from api_gateway.db.engine import get_session
        from api_gateway.db.models import AgentSessionRow

        async with get_session() as session:
            stmt = select(AgentSessionRow).order_by(AgentSessionRow.started_at.desc())
            rows = (await session.execute(stmt)).scalars().all()
            out: list[SessionResponse] = []
            for row in rows:
                events = await self._load_events(session, row.id)
                out.append(self._row_to_response(row, events))
            return out

    @staticmethod
    async def _load_events(session: Any, session_id: str) -> Any:
        from sqlalchemy import select

        from api_gateway.db.models import AgentSessionEventRow

        stmt = (
            select(AgentSessionEventRow)
            .where(AgentSessionEventRow.session_id == session_id)
            .order_by(AgentSessionEventRow.seq.asc())
        )
        return (await session.execute(stmt)).scalars().all()

    @staticmethod
    def _row_to_response(row: Any, event_rows: Any) -> SessionResponse:
        def _iso(value: object) -> str | None:
            if value is None:
                return None
            return value.isoformat() if hasattr(value, "isoformat") else str(value)

        return SessionResponse(
            id=row.id,
            agent_code=row.agent_code,
            task_type=row.task_type,
            status=row.status,
            started_at=_iso(row.started_at) or "",
            completed_at=_iso(row.completed_at),
            events=[
                SessionEventResponse(
                    id=ev.id,
                    timestamp=_iso(ev.timestamp) or "",
                    type=ev.type,
                    agent_code=row.agent_code,
                    message=ev.message,
                    data=ev.data or {},
                )
                for ev in event_rows
            ],
            run_id=None,
            summary=row.summary,
            source=row.source,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def create_agent_session_store() -> AgentSessionStore:
    """Create the appropriate agent-session store based on environment."""
    try:
        from api_gateway.db import HAS_SQLALCHEMY
        from api_gateway.db.engine import get_engine

        if HAS_SQLALCHEMY and get_engine() is not None:
            logger.info("agent_session_store_pg_initialized")
            return PgAgentSessionStore()
    except Exception as exc:  # noqa: BLE001 — degrade to in-memory
        logger.warning("agent_session_store_pg_failed_fallback", error=str(exc))

    logger.info("agent_session_store_in_memory_initialized")
    return InMemoryAgentSessionStore.create()
