"""Event → text transformation for embedding.

Embedding models are sensitive to surface form: ``"agent x failed"``
and ``"task x errored"`` end up far apart in vector space even though
they describe the same thing. Centralizing the serialization here gives
us one canonical text shape so that retrieval is reproducible across
runs and adapters.
"""

from __future__ import annotations

from typing import Any

from orchestrator.event_bus.events import Event, EventType

_OUTCOME_FROM_EVENT_TYPE: dict[EventType, str] = {
    EventType.AGENT_TASK_STARTED: "started",
    EventType.AGENT_TASK_COMPLETED: "completed",
    EventType.AGENT_TASK_FAILED: "failed",
}


def event_to_text(event: Event) -> str:
    """Render an ``AGENT_TASK_*`` event as a deterministic embedding string.

    The output is a single line of ``key=value`` pairs in a fixed order so
    that re-embedding the same event always yields the same vector, and
    different events keep stable lexical structure for BM25 mixed search.
    """
    data: dict[str, Any] = event.data or {}
    outcome = _OUTCOME_FROM_EVENT_TYPE.get(event.type, str(event.type))

    parts: list[str] = [
        f"agent={data.get('agent_code', '') or 'unknown'}",
        f"outcome={outcome}",
        f"run={data.get('run_id', '')}",
        f"step={data.get('step_id', '')}",
    ]

    task_type = data.get("task_type")
    if task_type:
        parts.append(f"task_type={task_type}")

    duration = data.get("duration") or data.get("duration_seconds")
    if duration is not None:
        try:
            parts.append(f"duration={float(duration):.3f}s")
        except (TypeError, ValueError):
            pass

    result_summary = _summarize_result(data.get("result"))
    if result_summary:
        parts.append(f"result={result_summary}")

    error = data.get("error")
    if error:
        parts.append(f"error={_collapse_whitespace(str(error))}")

    return " ".join(parts)


def _summarize_result(result: Any) -> str:
    """Compress a result payload into a short, embedding-friendly string."""
    if result is None:
        return ""
    if isinstance(result, str):
        return _collapse_whitespace(result)[:200]
    if isinstance(result, dict):
        # Prefer human-meaningful keys; fall back to the first few entries.
        for key in ("summary", "message", "status", "outcome"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return _collapse_whitespace(value)[:200]
        snippet = ", ".join(
            f"{k}={_collapse_whitespace(str(v))[:40]}" for k, v in list(result.items())[:3]
        )
        return snippet[:200]
    return _collapse_whitespace(str(result))[:200]


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())
