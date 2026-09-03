"""Chat-side experience deposit for the memory tier (MET-567).

MET-454 wired a ``MemoryExperienceRecorder`` into exactly one consumer:
``MechanicalAgent``. Everything else — every chat turn, every ``forge chat``
session, every design-flow phase that drives the harness — ran with no
recorder at all, so ``agent_experiences`` only ever filled from the
orchestrator's Temporal path. Retrieval, consolidation, and insight synthesis
all sat downstream of a store that production traffic never wrote to.

This module is the chat path's deposit, wired from the gateway lifespan like
the other layer-4 singletons and called on the tail of a turn.

Two deliberate filters keep the corpus worth searching:

* only turns that actually **called a tool** are recorded — a turn where the
  model just answered has no trajectory to learn from, and the design-flow
  handlers' one-shot extraction prompts (``max_steps=1``, no bridge) would
  otherwise flood the store with prompt-engineering noise;
* the recorded summary leads with the tools and failures, since that is what
  makes a past turn findable from a future goal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from digital_twin.memory.importance import score_importance
from orchestrator.event_bus.events import Event, EventType

logger = structlog.get_logger(__name__)

_recorder: Any = None

MAX_GOAL_CHARS = 400
MAX_TOOLS_LISTED = 12
MAX_ERRORS_LISTED = 3
_MAX_ERROR_CHARS = 200
AGENT_CODE = "chat-harness"


def init_chat_experience_recorder(memory_store: Any, embeddings: Any) -> None:
    """Wire the chat experience recorder from a bootstrap (either dep ``None`` disables)."""
    global _recorder  # noqa: PLW0603
    if memory_store is None or embeddings is None:
        _recorder = None
        logger.info("chat_experience_recorder_disabled", reason="missing_dependency")
        return
    try:
        from digital_twin.memory.experience_recorder import MemoryExperienceRecorder

        _recorder = MemoryExperienceRecorder(store=memory_store, embeddings=embeddings)
    except Exception as exc:  # noqa: BLE001 — chat must boot without memory
        _recorder = None
        logger.warning("chat_experience_recorder_init_failed", error=str(exc))
        return
    logger.info("chat_experience_recorder_initialized")


def chat_experience_enabled() -> bool:
    return _recorder is not None


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _tool_calls(steps: Any) -> list[tuple[str, str | None]]:
    """``(tool_name, error)`` for every step that invoked a tool."""
    out: list[tuple[str, str | None]] = []
    for step in steps or []:
        call = getattr(step, "tool_call", None)
        if call is None:
            continue
        name = str(getattr(call, "name", "") or "")
        if not name:
            continue
        error = getattr(step, "error", None)
        out.append((name, str(error) if error else None))
    return out


def summarize_turn(
    *,
    user_content: str,
    steps: Any,
    reply: str,
    stop_reason: str | None = None,
) -> str:
    """The text that gets embedded for one chat turn."""
    calls = _tool_calls(steps)
    counts: dict[str, int] = {}
    for name, _ in calls:
        counts[name] = counts.get(name, 0) + 1
    errors = [f"{name}: {_clip(err, _MAX_ERROR_CHARS)}" for name, err in calls if err]

    lines = [f"Goal: {_clip(user_content, MAX_GOAL_CHARS)}"]
    if counts:
        listed = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TOOLS_LISTED]
        rendered = ", ".join(f"{n} x{c}" if c > 1 else n for n, c in listed)
        extra = len(counts) - len(listed)
        if extra > 0:
            rendered += f", +{extra} more"
        lines.append(f"Tools used: {rendered}")
    if errors:
        lines.append(f"Failures ({len(errors)}): " + " | ".join(errors[:MAX_ERRORS_LISTED]))
    if stop_reason and stop_reason != "done":
        lines.append(f"Ended early: {stop_reason}")
    if reply:
        lines.append(f"Outcome: {_clip(reply, MAX_GOAL_CHARS)}")
    return "\n".join(lines)


def _coerce_project_id(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def turn_importance(
    *,
    steps: Any,
    success: bool,
    duration_seconds: float,
    summary: str,
    now: datetime | None = None,
) -> float:
    """Score a turn on the same scale as an ``AGENT_TASK_*`` event.

    A chat turn is not an agent-task event, but the consolidation pass applies
    one ``min_importance`` floor across every row in the corpus — so scoring it
    with a bespoke formula would silently change what that floor means
    depending on which path deposited the row.
    """
    reference = now or datetime.now(UTC)
    calls = _tool_calls(steps)
    event = Event(
        id=str(uuid4()),
        type=EventType.AGENT_TASK_COMPLETED if success else EventType.AGENT_TASK_FAILED,
        timestamp=reference.isoformat(),
        source="chat",
        data={
            "run_id": AGENT_CODE,
            "step_id": "turn",
            "agent_code": AGENT_CODE,
            "task_type": "chat_turn",
            "result": {"tool_calls": len(calls)} if success and calls else {},
            "result_summary": summary,
            "duration": duration_seconds,
            "error": None if success else "turn_failed",
        },
    )
    return score_importance(event, now=reference).total


async def record_chat_experience(
    *,
    thread_id: str,
    user_content: str,
    reply: str,
    steps: Any,
    status: str,
    stop_reason: str | None,
    duration_seconds: float,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    path: str = "native",
) -> bool:
    """Deposit one experience for a completed chat turn. Never raises.

    Returns whether a row was written — ``False`` when no recorder is wired or
    the turn called no tools (see the module docstring for why).
    """
    if _recorder is None:
        return False
    calls = _tool_calls(steps)
    if not calls:
        return False
    try:
        errors = [err for _, err in calls if err]
        success = status == "completed" and not errors
        summary = summarize_turn(
            user_content=user_content, steps=steps, reply=reply, stop_reason=stop_reason
        )
        await _recorder.record(
            run_id=thread_id,
            step_id="turn",
            agent_code=AGENT_CODE,
            task_type="chat_turn",
            success=success,
            duration_seconds=duration_seconds,
            result_summary=summary,
            error=_clip(errors[0], _MAX_ERROR_CHARS) if errors else None,
            project_id=_coerce_project_id(project_id),
            importance=turn_importance(
                steps=steps,
                success=success,
                duration_seconds=duration_seconds,
                summary=summary,
            ),
            metadata={
                "thread_id": thread_id,
                "tool_calls": len(calls),
                "tools_used": sorted({name for name, _ in calls}),
                "error_count": len(errors),
                "stop_reason": stop_reason or "",
                "provider": provider or "",
                "model": model or "",
                "harness_path": path,
                "recorded_by": "chat_experience_adapter",
            },
        )
    except Exception as exc:  # noqa: BLE001 — the deposit never breaks a turn
        logger.warning("chat_experience_record_failed", thread_id=thread_id, error=str(exc))
        return False
    logger.info(
        "chat_experience_recorded",
        thread_id=thread_id,
        tool_calls=len(calls),
        project_id=project_id,
    )
    return True
