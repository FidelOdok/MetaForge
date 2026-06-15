#!/usr/bin/env python3
"""Claude Code → MetaForge session-capture hook adapter (MET-497).

Thin translation layer: reads a Claude Code hook payload on stdin and
delegates to the client-agnostic capture core (sibling ``metaforge_capture``).
Lives here (tracked) rather than under ``.claude/`` because ``.claude/`` is
git-ignored — see ``claude_code/README.md`` for the one-line install that
points ``.claude/settings.json`` at this file.

Registered for three events:
* ``PostToolUse`` (matcher ``mcp__metaforge__.*``) → an ``action`` event
* ``Stop``                                        → transcript delta → thoughts
* ``SessionEnd``                                  → complete the session

Capture is bound to an *active project* resolved from the session's ``cwd``
(MET-501): ``PostToolUse``/``Stop`` capture only when a project is active, and
the event is attributed to it. No active project → no capture (most Claude
sessions aren't project work). ``SessionEnd`` still completes any sessions that
were opened. Set the active project with ``metaforge-capture use <project_id>``.

Never raises and always exits 0 — capture must not break the turn. Config via
``METAFORGE_GATEWAY_URL`` / ``METAFORGE_SESSION_CAPTURE=off`` (see the core).

Note: until Mcp-Session-Id binding lands (MET-496 follow-up) this hook owns a
separate session from the server-side auto-capture implicit session, so
mcp__metaforge actions may appear in both. The hook session is the complete
one (actions + reasoning).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_CLIENT = "claude-code"
_AGENT_CODE = "claude-code"
_TASK_TYPE = "claude-code-session"
_MAX = 4000


def _load_core() -> Any:
    core = Path(__file__).resolve().parent / "metaforge_capture.py"
    spec = importlib.util.spec_from_file_location("metaforge_capture", core)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(payload, dict):
        return 0

    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid:
        return 0

    core = _load_core()
    if core is None or not core.capture_enabled():
        return 0

    try:
        client = core.CaptureClient(_CLIENT)
        event = payload.get("hook_event_name", "")
        # Resolve the active project from the session's working directory.
        # Capture is compulsory-bound: no active project → don't capture.
        cwd = payload.get("cwd")
        project_id = core.read_active_project(cwd if isinstance(cwd, str) else None)
        if event in ("PostToolUse", "Stop") and not project_id:
            return 0
        if event == "PostToolUse":
            tool = str(payload.get("tool_name", "") or "tool")
            tool_input = payload.get("tool_input")
            summary = json.dumps(tool_input, default=str)[:_MAX] if tool_input is not None else ""
            client.push_event(
                sid,
                type="action",
                message=tool,
                data={"source": "claude-code-hook", "tool": tool, "tool_input": summary},
                agent_code=_AGENT_CODE,
                task_type=_TASK_TYPE,
                project_id=project_id,
            )
        elif event == "Stop":
            transcript = payload.get("transcript_path")
            if isinstance(transcript, str) and transcript:
                client.push_transcript_delta(
                    sid,
                    transcript,
                    agent_code=_AGENT_CODE,
                    task_type=_TASK_TYPE,
                    project_id=project_id,
                )
        elif event == "SessionEnd":
            client.complete(sid, status="completed")
    except Exception:  # noqa: BLE001 — capture is best-effort, never fatal
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
