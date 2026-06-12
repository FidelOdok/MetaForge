"""Transcript parsers for the capture tailer (MET-498).

Each agent CLI writes its session transcript in its own JSONL shape. A parser
turns ONE transcript entry (already ``json.loads``-ed) into a list of
normalized capture events ``(type, message, data)`` — the same vocabulary the
core and the session API speak (thought / action / decision / …).

The tailer (``metaforge_capture.tail``) reads a transcript from a byte cursor
and runs the registered parser per line, so adding a new client is a single
parser function + a registry entry — no other code changes.

stdlib only. Parsers are intentionally lenient: anything they don't recognise
yields ``[]`` rather than raising, so unknown/garbled lines are skipped.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_MAX_TEXT = 8000

# (event_type, message, data)
EventTuple = tuple[str, str, dict[str, Any]]
Parser = Callable[[dict[str, Any]], list[EventTuple]]


def _texts_from_content(content: Any) -> list[str]:
    """Pull text out of a content field that may be a str or a list of blocks."""
    out: list[str] = []
    if isinstance(content, str):
        if content.strip():
            out.append(content.strip()[:_MAX_TEXT])
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("text", "output_text") and isinstance(block.get("text"), str):
                if block["text"].strip():
                    out.append(block["text"].strip()[:_MAX_TEXT])
    return out


def claude_code_parser(entry: dict[str, Any], source: str = "claude-code-tail") -> list[EventTuple]:
    """Claude Code transcript: ``{type:"assistant", message:{content:[blocks]}}``.

    ``text`` blocks → ``thought`` events, ``tool_use`` blocks → ``action`` events.
    """
    if entry.get("type") != "assistant":
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    events: list[EventTuple] = []
    if isinstance(content, str):
        for text in _texts_from_content(content):
            events.append(("thought", text, {"source": source}))
        return events
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                events.append(("thought", block["text"].strip()[:_MAX_TEXT], {"source": source}))
            elif btype == "tool_use" and block.get("name"):
                events.append(
                    ("action", str(block["name"]), {"source": source, "tool": block["name"]})
                )
    return events


def codex_parser(entry: dict[str, Any], source: str = "codex-tail") -> list[EventTuple]:
    """Codex CLI rollout JSONL (best-effort — schema varies across versions).

    Handles the common shapes: a top-level ``role``/``content`` record or a
    nested ``message`` with ``role``/``content``; assistant text → ``thought``.
    Unknown shapes yield no events (the tailer skips them).
    """
    role = entry.get("role")
    content = entry.get("content")
    if role is None and isinstance(entry.get("message"), dict):
        msg = entry["message"]
        role = msg.get("role")
        content = msg.get("content")
    entry_type = entry.get("type")
    if role != "assistant" and entry_type not in ("assistant", "message"):
        return []
    return [("thought", text, {"source": source}) for text in _texts_from_content(content)]


PARSERS: dict[str, Parser] = {
    "claude-code": claude_code_parser,
    "codex": codex_parser,
}


def get_parser(client: str) -> Parser | None:
    """Return the parser registered for ``client`` (None if unknown)."""
    return PARSERS.get(client)
