#!/usr/bin/env python3
"""Claude Code lifecycle hooks shipped with the MetaForge plugin (FORGE-539).

Two events, both of which only *add context*; neither blocks anything:

``session-start``
    Gives the agent MetaForge's working rules once per session: ask rather
    than guess engineering values, never approve your own work, compile the
    intent and check capabilities before proposing, verify before calling a
    design done.

``post-tool-use``
    After ``flow.propose`` or ``flow.patch`` comes back held for a person,
    restates that nothing runs until somebody answers the named approval. The
    tool result already says so; agents still poll, and a reminder at the
    moment of the result is cheaper than a correction afterwards.

Standard library only, so it runs from the installed plugin with no
MetaForge on the path. Every failure is swallowed and the hook exits 0: a
hook that breaks a session is worse than no hook. ``METAFORGE_PLUGIN_HOOKS=off``
turns both off.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

SESSION_RULES = (
    "MetaForge plugin rules for this session:\n"
    "- Never invent an engineering value (load, material, tolerance, route, budget). "
    "Ask the user; 'unknown' is a valid answer.\n"
    "- Before proposing a design flow: flow.compile_intent, then flow.capabilities. "
    "Propose with template AND operations together (never template alone).\n"
    "- Proposals, patches, gates and waivers are approved by a person. No tool "
    "approves; report the approval id and stop.\n"
    "- A design is done only when flow.lifecycle / flow.verify_completion says "
    "COMPLETED_VERIFIED. PARTIALLY_COMPLETED is not done.\n"
    "- 'unknown' phase state is not 'pending'; no_data is a gap, not a pass.\n"
    "Skills: intent-to-verified-design (procedure), workflow-lifecycle (reasoning)."
)

_HELD_TOOL = re.compile(r"flow[._](propose|patch)$")


def _emit(event: str, context: str) -> None:
    print(
        json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}})
    )


def _response_dict(raw: Any) -> dict[str, Any]:
    """The tool's JSON result, from whatever shape the client handed the hook."""
    if isinstance(raw, dict):
        if isinstance(raw.get("data"), dict):
            return raw["data"]
        content = raw.get("content")
        if isinstance(content, list):
            for block in content:
                text = block.get("text") if isinstance(block, dict) else None
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        return _response_dict(parsed)
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return _response_dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(raw, list):
        return _response_dict({"content": raw})
    return {}


def post_tool_use(payload: dict[str, Any]) -> str | None:
    """The reminder for a held flow result, or ``None`` when there is nothing to add."""
    tool = str(payload.get("tool_name") or "")
    if not _HELD_TOOL.search(tool):
        return None
    result = _response_dict(payload.get("tool_response"))
    if result.get("status") != "proposed":
        return None
    approval = result.get("approval_id") or "?"
    rerun = result.get("rerun")
    extra = f" It would re-run: {', '.join(rerun)}." if isinstance(rerun, list) and rerun else ""
    return (
        f"MetaForge: approval '{approval}' is held for a person.{extra} Nothing runs or "
        "changes until they answer it. Report the approval id and stop; do not poll, "
        "and do not look for a way to approve it."
    )


def main(argv: list[str]) -> int:
    if (os.environ.get("METAFORGE_PLUGIN_HOOKS") or "").strip().lower() in {"off", "0", "false"}:
        return 0
    event = argv[1] if len(argv) > 1 else ""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if event == "session-start":
            _emit("SessionStart", SESSION_RULES)
        elif event == "post-tool-use":
            message = post_tool_use(payload)
            if message:
                _emit("PostToolUse", message)
    except Exception:  # noqa: BLE001 - a hook never breaks the session
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
