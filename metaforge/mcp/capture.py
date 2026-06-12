"""Server-side auto-capture of MCP tool calls into the agent-session store (MET-496).

The MCP server is the one component that sees every tool call, so it can
guarantee a baseline action timeline for *any* client (Claude Code, Cursor,
claude.ai web) with zero client cooperation — the enforced "Layer A" of the
session-capture stack (MET-492).

``SessionCapture`` is wired into ``UnifiedMcpServer._tool_call`` (the single
funnel for both the standard ``tools/call`` and legacy ``tool/call`` paths).
For every non-``session.*`` tool call it appends an ``action`` (or ``error``)
event to the bound agent session, lazily creating an implicit session and
rolling it over after an idle window. If the client drives the explicit
``session.*`` tools (MET-494), ``session.start`` takes over the binding so
captured actions attach to the client's own session — no duplicates.

Hard guarantee: capture never fails the underlying tool call. Every store
interaction is wrapped; failures are logged, not raised.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_SESSION_TOOL_PREFIX = "session."
_MAX_ARG_SUMMARY = 500
_REDACT_HINTS = ("key", "secret", "token", "password", "credential")


def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Compact, redacted one-line-ish summary of tool args for the event payload.

    Truncates large strings/blobs (CAD scripts, file bytes) and redacts any
    field whose name hints at a credential.
    """
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if any(hint in key.lower() for hint in _REDACT_HINTS):
            out[key] = "<redacted>"
        elif isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = (
                value
                if len(value) <= _MAX_ARG_SUMMARY
                else value[:_MAX_ARG_SUMMARY] + f"…(+{len(value) - _MAX_ARG_SUMMARY})"
            )
        else:
            blob = json.dumps(value, default=str)
            out[key] = blob if len(blob) <= _MAX_ARG_SUMMARY else blob[:_MAX_ARG_SUMMARY] + "…"
    return out


class SessionCapture:
    """Records MCP tool calls into an :class:`AgentSessionStore`.

    Process-level implicit session with idle rollover (binding-strategy
    fallback #3 from MET-496 — per-process + inactivity window). Header /
    OAuth-subject binding is a follow-up; ``agent_code`` defaults to ``mcp``.
    """

    def __init__(
        self,
        store: Any,
        *,
        agent_code: str = "mcp",
        task_type: str = "mcp-session",
        idle_rollover_seconds: float = 1800.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._agent_code = agent_code
        self._task_type = task_type
        self._idle = timedelta(seconds=idle_rollover_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._implicit_id: str | None = None
        self._explicit_id: str | None = None
        self._last_activity: datetime | None = None

    async def on_tool_call(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        status: str,
        duration_ms: float,
        result: Any = None,
        error: Any = None,
    ) -> None:
        """Record one tool call. Never raises — capture must not break the call."""
        try:
            await self._on_tool_call(
                tool_id,
                arguments,
                status=status,
                duration_ms=duration_ms,
                result=result,
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 — capture is best-effort, never fatal
            logger.warning("session_capture_failed", tool_id=tool_id, error=str(exc))

    async def _on_tool_call(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        status: str,
        duration_ms: float,
        result: Any,
        error: Any,
    ) -> None:
        now = self._clock()

        # session.* tools are excluded from action capture, but session.start
        # / session.complete drive the explicit-session takeover (MET-494).
        if tool_id.startswith(_SESSION_TOOL_PREFIX):
            if status == "ok" and tool_id == "session.start":
                sid = self._extract_session_id(result)
                if sid:
                    self._explicit_id = sid
                    logger.info("session_capture_takeover", session_id=sid)
            elif tool_id == "session.complete":
                self._explicit_id = None
            return

        session_id = await self._target_session(now)
        if session_id is None:
            return
        self._last_activity = now

        event_type = "action" if status == "ok" else "error"
        message = tool_id if status == "ok" else f"{tool_id} failed"
        data: dict[str, Any] = {
            "tool_id": tool_id,
            "status": status,
            "duration_ms": round(duration_ms, 2),
            "args": _summarize_args(arguments),
            "captured_by": "mcp-server",
        }
        if error is not None:
            data["error"] = str(error)[:_MAX_ARG_SUMMARY]
        await self._store.append_event(session_id, type=event_type, message=message, data=data)

    async def _target_session(self, now: datetime) -> str | None:
        if self._explicit_id is not None:
            return self._explicit_id

        # Roll the implicit session over after an idle window.
        if (
            self._implicit_id is not None
            and self._last_activity is not None
            and (now - self._last_activity) > self._idle
        ):
            try:
                await self._store.complete_session(
                    self._implicit_id, status="completed", summary="idle rollover"
                )
            except Exception as exc:  # noqa: BLE001 — best-effort close
                logger.warning("session_capture_rollover_failed", error=str(exc))
            self._implicit_id = None

        if self._implicit_id is None:
            session = await self._store.create_session(
                agent_code=self._agent_code, task_type=self._task_type, source="external"
            )
            self._implicit_id = session.id
        return self._implicit_id

    @staticmethod
    def _extract_session_id(result: Any) -> str | None:
        # Adapter results are enveloped as {tool_id, status, data: {...}};
        # session.start's session_id lives in ``data``. Fall back to the top
        # level for un-enveloped callers/tests.
        if isinstance(result, dict):
            data = result.get("data")
            payload = data if isinstance(data, dict) else result
            sid = payload.get("session_id") or payload.get("id")
            if isinstance(sid, str):
                return sid
        return None
