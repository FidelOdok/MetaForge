"""Bridge captured agent sessions into the experience store (MET-567).

Session capture (MET-492 / 496 / 594) records what agents actually did — every
tool call, thought, and decision — into ``agent_sessions`` and
``agent_session_events``. The memory tier (MET-453) learns from
``ExperienceMemory`` rows in ``agent_experiences``. Nothing connected the two:
experiences were written only by ``AGENT_TASK_*`` events off the orchestrator's
scheduler, so every MCP-driven, CLI-driven, and chat-driven session — which is
to say nearly all real usage — deposited nothing, and retrieval had nothing to
retrieve.

This module turns a completed session into one experience row: a distilled
summary of the trajectory (tools used, failures hit, decisions taken), scored
for importance and embedded so ``retrieve_similar_experience`` can surface it
when a later agent faces the same kind of work.

One row per session, not one per event, is deliberate. The lesson worth
recalling is "how did this piece of work go", and per-event rows would swamp
the corpus with individual tool calls that carry no transferable signal.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import structlog

from digital_twin.memory.importance import (
    DEFAULT_WEIGHTS,
    ImportanceWeights,
    score_importance,
)
from observability.tracing import get_tracer
from orchestrator.event_bus.events import Event, EventType

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.session_bridge")

MAX_SUMMARY_CHARS = 2000
"""Cap on the embedded summary. Embedding models truncate anyway, and a
runaway session (hundreds of tool calls) would otherwise bury its own signal
under a wall of repeated tool names."""

MAX_TOOLS_LISTED = 12
MAX_ERRORS_LISTED = 3
MAX_DECISIONS_LISTED = 5
_MAX_LINE_CHARS = 240

_TERMINAL_SUCCESS = {"completed"}
"""Session statuses that count as a successful trajectory. ``failed`` and
``abandoned`` (the stale sweep) are both honest failures to learn from."""


class ExperienceRecorderLike(Protocol):
    """The slice of ``MemoryExperienceRecorder`` this bridge needs."""

    async def record(
        self,
        *,
        run_id: str,
        step_id: str,
        agent_code: str,
        task_type: str,
        success: bool,
        duration_seconds: float,
        result_summary: str,
        error: str | None = ...,
        project_id: UUID | None = ...,
        importance: float = ...,
        metadata: dict[str, Any] | None = ...,
    ) -> None: ...


def _events_of(session: Any) -> list[Any]:
    events = getattr(session, "events", None) or []
    return list(events)


def _event_field(event: Any, name: str, default: Any = None) -> Any:
    """Read a field off a pydantic event or a plain dict, interchangeably."""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _event_data(event: Any) -> dict[str, Any]:
    data = _event_field(event, "data")
    return data if isinstance(data, dict) else {}


def _tool_id_of(event: Any) -> str:
    return str(_event_data(event).get("tool_id") or "")


def _is_error(event: Any) -> bool:
    return (
        str(_event_field(event, "type", "")) == "error"
        or _event_data(event).get("status") == "error"
    )


def _clip(text: str, limit: int = _MAX_LINE_CHARS) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def summarize_session(session: Any) -> str:
    """Render a completed session as the text that gets embedded.

    Written for retrieval, not for display: the tool inventory, the failures,
    and the decisions are what make a past session findable from a future
    goal, so they lead. The dashboard already renders the full timeline; this
    is the lossy, searchable digest of it.
    """
    events = _events_of(session)
    status = str(getattr(session, "status", "") or "unknown")
    agent_code = str(getattr(session, "agent_code", "") or "agent")
    task_type = str(getattr(session, "task_type", "") or "session")

    tools: Counter[str] = Counter()
    errors: list[str] = []
    decisions: list[str] = []
    thoughts = 0
    for event in events:
        etype = str(_event_field(event, "type", "") or "")
        message = str(_event_field(event, "message", "") or "")
        tool_id = _tool_id_of(event)
        if tool_id:
            tools[tool_id] += 1
        if _is_error(event):
            detail = str(_event_data(event).get("error") or "")
            label = tool_id or message
            errors.append(_clip(f"{label}: {detail}" if detail else label))
        elif etype == "decision":
            decisions.append(_clip(message))
        elif etype == "thought":
            thoughts += 1

    lines = [f"Agent session: {task_type} by {agent_code} — {status}."]
    summary = getattr(session, "summary", None)
    if summary:
        lines.append(_clip(str(summary)))
    if tools:
        rendered = ", ".join(
            f"{name} x{count}" if count > 1 else name
            for name, count in tools.most_common(MAX_TOOLS_LISTED)
        )
        extra = len(tools) - MAX_TOOLS_LISTED
        if extra > 0:
            rendered += f", +{extra} more"
        lines.append(f"Tools used: {rendered}")
    else:
        lines.append("Tools used: none")
    if decisions:
        lines.append("Decisions: " + " | ".join(decisions[:MAX_DECISIONS_LISTED]))
    if errors:
        lines.append(f"Failures ({len(errors)}): " + " | ".join(errors[:MAX_ERRORS_LISTED]))
    lines.append(
        f"Trajectory: {len(events)} event(s), {sum(tools.values())} tool call(s), "
        f"{thoughts} recorded thought(s)."
    )
    text = "\n".join(lines)
    return text if len(text) <= MAX_SUMMARY_CHARS else text[: MAX_SUMMARY_CHARS - 1] + "…"


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def session_duration_seconds(session: Any) -> float:
    """Wall-clock length of the session; ``0.0`` when timestamps are unusable."""
    started = _parse_iso(getattr(session, "started_at", None))
    completed = _parse_iso(getattr(session, "completed_at", None)) or datetime.now(UTC)
    if started is None:
        return 0.0
    return max(0.0, (completed - started).total_seconds())


def session_importance(
    session: Any,
    *,
    now: datetime | None = None,
    weights: ImportanceWeights = DEFAULT_WEIGHTS,
) -> float:
    """Score a session with the same function that scores agent-task events.

    Reusing :func:`score_importance` instead of inventing a session-specific
    formula keeps one importance scale across the whole corpus — otherwise the
    consolidation pass's ``min_importance`` floor would mean two different
    things depending on which deposit path wrote the row.
    """
    status = str(getattr(session, "status", "") or "")
    success = status in _TERMINAL_SUCCESS
    events = _events_of(session)
    tool_calls = sum(1 for e in events if _tool_id_of(e))
    reference = now or datetime.now(UTC)
    started = _parse_iso(getattr(session, "started_at", None)) or reference
    completed = _parse_iso(getattr(session, "completed_at", None)) or started
    event = Event(
        id=str(getattr(session, "id", "") or "session"),
        type=EventType.AGENT_TASK_COMPLETED if success else EventType.AGENT_TASK_FAILED,
        timestamp=completed.isoformat(),
        source="session_capture",
        data={
            "run_id": str(getattr(session, "id", "") or ""),
            "step_id": "session",
            "agent_code": str(getattr(session, "agent_code", "") or ""),
            "task_type": str(getattr(session, "task_type", "") or ""),
            "result": {"tool_calls": tool_calls} if success and tool_calls else {},
            "result_summary": str(getattr(session, "summary", "") or ""),
            "duration": session_duration_seconds(session),
            "error": None if success else status,
        },
    )
    return score_importance(event, now=reference, weights=weights).total


def _coerce_project_id(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


class SessionExperienceBridge:
    """Deposits one experience row per completed session.

    Delegates the embed-and-store half to a ``MemoryExperienceRecorder`` so
    the fail-soft contract (a flaky embedder or store never breaks the caller)
    lives in exactly one place.
    """

    def __init__(
        self,
        recorder: ExperienceRecorderLike,
        *,
        min_events: int = 1,
    ) -> None:
        self._recorder = recorder
        # A session with no events recorded nothing worth learning from —
        # typically an implicit capture session opened by a single read-only
        # tool call and rolled over on idle.
        self._min_events = min_events

    async def on_session_completed(self, session: Any) -> bool:
        """Record ``session`` as an experience; returns whether a row was written.

        Never raises: this runs on the tail of a session-completion request,
        and a memory-tier problem must not fail the completion itself.
        """
        with tracer.start_as_current_span("memory.session_bridge") as span:
            try:
                session_id = str(getattr(session, "id", "") or "")
                events = _events_of(session)
                span.set_attribute("session.id", session_id)
                span.set_attribute("session.event_count", len(events))
                if not session_id:
                    return False
                if len(events) < self._min_events:
                    logger.debug(
                        "session_experience_skipped",
                        session_id=session_id,
                        reason="no_events",
                    )
                    return False

                status = str(getattr(session, "status", "") or "")
                success = status in _TERMINAL_SUCCESS
                tools = sorted({_tool_id_of(e) for e in events if _tool_id_of(e)})
                error_count = sum(1 for e in events if _is_error(e))
                await self._recorder.record(
                    run_id=session_id,
                    step_id="session",
                    agent_code=str(getattr(session, "agent_code", "") or "agent"),
                    task_type=str(getattr(session, "task_type", "") or "session"),
                    success=success,
                    duration_seconds=session_duration_seconds(session),
                    result_summary=summarize_session(session),
                    error=None if success else (status or "failed"),
                    project_id=_coerce_project_id(getattr(session, "project_id", None)),
                    importance=session_importance(session),
                    metadata={
                        "session_id": session_id,
                        "session_status": status,
                        "session_source": str(getattr(session, "source", "") or ""),
                        "event_count": len(events),
                        "tools_used": tools,
                        "error_count": error_count,
                        "recorded_by": "session_experience_bridge",
                    },
                )
                logger.info(
                    "session_experience_recorded",
                    session_id=session_id,
                    status=status,
                    event_count=len(events),
                    tools=len(tools),
                )
                return True
            except Exception as exc:  # noqa: BLE001 — deposit is best-effort
                span.record_exception(exc)
                logger.warning("session_experience_failed", error=str(exc))
                return False
