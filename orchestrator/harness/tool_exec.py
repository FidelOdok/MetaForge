"""Loop-agnostic tool-execution behaviour shared by both agent loops (MET-569).

MET-569 landed four hardening properties, but two of them — the same-turn
duplicate guard and structured error results — were implemented inside
``run_native_tools`` and so applied to the native tool-calling path only.
``native_tools_enabled()`` returns False for any provider whose adapter cannot
parse native ``tool_calls`` (Gemini, Bedrock, …), and those turns fall back to
the JSON-ReAct loop — where a repeated call re-executed the tool and an MCP
adapter's error envelope was flattened back to ``str(exc)``.

Losing a correctness property because of which provider a deployment picked is
a bad trade, and neither property is native-specific: a duplicate call is
wasteful on any path, and a structured error is more actionable on any path.
So both live here, and both loops call in.

Not lifted: parallel batched execution stays in ``native_tools`` because it has
no ReAct analogue — the JSON-ReAct protocol emits exactly one tool call per
step, so there is never a batch to parallelise.
"""

from __future__ import annotations

import json
from typing import Any

from orchestrator.harness.validation import ToolValidationError

CACHED_NOTE = (
    "identical call already made this turn -- the previous successful result is "
    "reused verbatim and the tool was NOT executed again"
)


def render_json(value: Any) -> str:
    """JSON, falling back to ``str`` for anything unserialisable."""
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def dedup_key(name: str, arguments: dict[str, Any]) -> str:
    """Stable identity for one (tool, arguments) pair within a turn.

    Byte-identical arguments only. A retry that *adds* arguments (observed in
    the wild: ``project.create {name}`` then
    ``{name, description, status}``) is a genuinely different call and must not
    be collapsed.
    """
    try:
        return json.dumps({"tool": name, "arguments": arguments}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return f"{name}:{arguments!r}"


def cached_view(observation: Any) -> dict[str, Any]:
    """The envelope a reused result is presented as.

    Used for BOTH the model's copy and the recorded trace step, so the reuse is
    visible to the live step feed, the durable session log, and
    ``score_sessions`` replay — not only to the model.
    """
    return {"cached": True, "note": CACHED_NOTE, "result": observation}


def error_content(exc: Exception) -> str:
    """Render a failed tool call as a structured result the model can act on.

    Before MET-569 this was ``f"ERROR: {exc}"``. An MCP adapter's error
    envelope — its status, its message, and any hint it carried about what to
    do instead — was flattened into one opaque line, so a model could not tell
    "the container is down, stop trying" from "that argument was wrong, fix it".
    """
    if isinstance(exc, ToolValidationError):
        return render_json(exc.to_payload())
    payload: dict[str, Any] = {"status": "error", "error": str(exc)}
    tool_id = getattr(exc, "tool_id", None)
    if isinstance(tool_id, str) and tool_id:
        payload["tool"] = tool_id
    envelope = getattr(exc, "payload", None)
    if isinstance(envelope, dict) and envelope:
        payload["details"] = envelope
    return render_json(payload)


class TurnToolCache:
    """Successful ``(tool, arguments)`` results for the duration of one turn.

    Turn-scoped deliberately: the same call in a *later* turn is usually the
    user asking for current state, which is a legitimate re-read. Failures are
    never stored — whatever caused them (an adapter restarting, a lock
    clearing) may have changed by the time the model tries again.
    """

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def key(self, name: str, arguments: dict[str, Any]) -> str:
        return dedup_key(name, arguments)

    def has(self, key: str) -> bool:
        return key in self._results

    def get(self, key: str) -> Any:
        return self._results[key]

    def put(self, key: str, observation: Any) -> None:
        self._results[key] = observation

    def __len__(self) -> int:
        return len(self._results)
