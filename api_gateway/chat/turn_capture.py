"""Chat-turn Action-Observation capture (MET-594).

The OpenHands/OpenCode pattern: the SSE stream is the ephemeral UI feed; the
durable record is an event log. MetaForge has that log (``agent_sessions`` /
``agent_session_events``, MET-492/493) and the MCP sidecar fills it for
external agents — but chat-turn steps were SSE-only, vanishing when the
stream ended. This module tees the MET-590 live step feed into the session
store, one session per chat thread, so:

- chat trajectories become durable and reviewable (sessions UI),
- ``evals/score_sessions.py`` scores production chat behavior (the events
  carry the exact ``{tool_id, status, args}`` shape its rubric replays),
- the persisted log survives client disconnects the stream cannot.

Everything here is best-effort by contract: capture must never fail, block,
or slow a turn (errors log and drop).
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_store: Any = None
# thread_id -> session_id. In-memory: a gateway restart starts a fresh session
# for the same thread (the old one is retired by the stale-session sweep),
# which is the honest record — the driver that produced it is gone.
_sessions: dict[str, str] = {}

_MAX_OBS_DIGEST = 500
_MAX_THOUGHT = 2000


def init_turn_capture(store: Any) -> None:
    """Wire the agent-session store from the gateway lifespan (None disables)."""
    global _store  # noqa: PLW0603
    _store = store
    _sessions.clear()
    logger.info("chat_turn_capture_initialized", enabled=store is not None)


async def _session_for(thread_id: str, project_id: str | None) -> str | None:
    if _store is None:
        return None
    cached = _sessions.get(thread_id)
    if cached:
        return cached
    session = await _store.create_session(
        agent_code="chat-harness",
        task_type="chat",
        title=f"chat thread {thread_id[:8]}",
        project_id=project_id,
        source="chat",
    )
    _sessions[thread_id] = session.id
    logger.info("chat_turn_session_created", thread_id=thread_id, session_id=session.id)
    return session.id


async def capture_step(thread_id: str, project_id: str | None, step: dict[str, Any]) -> None:
    """Persist one live step as an ``action`` event (best-effort).

    The ``data`` shape mirrors the MCP sidecar's Layer-A capture so
    ``score_sessions.session_to_turn`` replays chat trajectories unchanged:
    ``tool_id`` / ``status`` / ``args``, plus a bounded observation digest and
    the step's reasoning for human review.
    """
    if _store is None:
        return
    tool = step.get("tool")
    if not tool:
        return  # final reasoning steps carry no action; the reply is persisted
    try:
        session_id = await _session_for(thread_id, project_id)
        if session_id is None:
            return
        error = step.get("error")
        data: dict[str, object] = {
            "tool_id": str(tool),
            "status": "error" if error else "ok",
            "args": step.get("arguments") or {},
        }
        if error:
            data["error"] = str(error)[:_MAX_OBS_DIGEST]
        observation = step.get("observation")
        if observation is not None:
            data["observation_digest"] = str(observation)[:_MAX_OBS_DIGEST]
        thought = step.get("thought")
        if thought:
            data["thought"] = str(thought)[:_MAX_THOUGHT]
        await _store.append_event(session_id, type="action", message=str(tool), data=data)
    except Exception as exc:  # noqa: BLE001 — capture must never break a turn
        logger.warning("chat_turn_capture_failed", thread_id=thread_id, error=str(exc))


async def capture_turn_done(
    thread_id: str, project_id: str | None, *, status: str, steps: int
) -> None:
    """Persist a turn-boundary marker (best-effort). The session stays open —
    a thread is an ongoing body of work; the stale sweep retires it when its
    gateway goes away."""
    if _store is None:
        return
    try:
        session_id = await _session_for(thread_id, project_id)
        if session_id is None:
            return
        await _store.append_event(
            session_id,
            type="observation",
            message=f"turn {status}",
            data={"status": status, "steps": steps},
        )
    except Exception as exc:  # noqa: BLE001 — capture must never break a turn
        logger.warning("chat_turn_capture_failed", thread_id=thread_id, error=str(exc))
