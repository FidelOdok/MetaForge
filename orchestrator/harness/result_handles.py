"""Large tool results as a summary plus a handle (FORGE-479).

A tool result goes into the model's context on every later call of the turn,
so a 60 kB mesh, FEA field dump, BOM table or Cypher row set is paid for again
at each step. Most of the time the model needs a fact from it (a count, a
status, one field), not the whole thing.

Above a configured size the result is therefore stored here and the model gets
a short summary plus a handle. It reads more on demand through the
``read_tool_result`` native tool, a window at a time, or a single top-level key.

Nothing is dropped silently: the summary states the full size and how to read
the rest, and an evicted or unknown handle is an error that says so. The full
value is still on the recorded trace step; only the model's copy is replaced.

Layer-2 module: stdlib + structlog only.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Env var overriding the inline limit, in characters of rendered JSON.
INLINE_LIMIT_ENV = "METAFORGE_TOOL_RESULT_INLINE_CHARS"
#: A result at or under this renders inline (about 2k tokens).
DEFAULT_INLINE_LIMIT = 8_000
#: Env var for the default read_tool_result window, in characters.
READ_CHARS_ENV = "METAFORGE_TOOL_RESULT_READ_CHARS"
#: A read that names no limit returns this many characters (about 750 tokens). Every
#: window stays in the context for the rest of the turn, so the default is small; a read
#: may still ask for up to the inline limit (2026-11-11).
DEFAULT_READ_CHARS = 3_000
#: Oldest results are evicted past this many, and reading one says so.
MAX_STORED_RESULTS = 64
#: Name of the native tool that reads a stored result.
READER_TOOL_NAME = "read_tool_result"

_SUMMARY_ITEMS = 3
_SUMMARY_ITEM_CHARS = 240
_SUMMARY_STRING_CHARS = 400
_SUMMARY_KEYS = 24


def inline_limit() -> int:
    """The configured inline limit; a bad value falls back to the default."""
    raw = os.environ.get(INLINE_LIMIT_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logger.warning("tool_result_inline_limit_invalid", value=raw)
            return DEFAULT_INLINE_LIMIT
        if value > 0:
            return value
    return DEFAULT_INLINE_LIMIT


def read_chars() -> int:
    """The default read window, never above the inline limit; a bad value falls back."""
    raw = os.environ.get(READ_CHARS_ENV, "").strip()
    value = DEFAULT_READ_CHARS
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            logger.warning("tool_result_read_chars_invalid", value=raw)
        else:
            if parsed > 0:
                value = parsed
    return min(value, inline_limit())


def _render(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}...[{len(text) - limit} more chars]"


def _describe(value: Any) -> Any:
    """A bounded stand-in for ``value``: sizes for containers, clipped scalars."""
    if isinstance(value, str):
        return _clip(value, _SUMMARY_STRING_CHARS)
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "first": [_clip(_render(v), _SUMMARY_ITEM_CHARS) for v in value[:_SUMMARY_ITEMS]],
        }
    if isinstance(value, dict):
        return {"type": "object", "keys": len(value)}
    return value


def summarize(value: Any) -> Any:
    """What the model sees in place of a large result.

    Top-level keys of an object each get a bounded description (a list reports
    its length and first items, a string a clipped prefix, a nested object its
    key count), so a ``{"items": [...], "total": N}`` envelope keeps its
    ``total``. A bare list or string is described the same way.
    """
    if isinstance(value, dict):
        keys = list(value)
        out: dict[str, Any] = {k: _describe(value[k]) for k in keys[:_SUMMARY_KEYS]}
        if len(keys) > _SUMMARY_KEYS:
            out["..."] = f"{len(keys) - _SUMMARY_KEYS} more keys"
        return out
    return _describe(value)


@dataclass(frozen=True)
class StoredResult:
    handle: str
    tool: str
    value: Any
    size: int


class ResultStore:
    """Large tool results for one runtime, addressable by handle."""

    def __init__(self, max_entries: int = MAX_STORED_RESULTS) -> None:
        self._max = max_entries
        self._items: OrderedDict[str, StoredResult] = OrderedDict()
        self._evicted: set[str] = set()
        self._counter = 0

    def __len__(self) -> int:
        return len(self._items)

    def put(self, tool: str, value: Any, size: int) -> StoredResult:
        self._counter += 1
        handle = f"result-{self._counter}"
        item = StoredResult(handle=handle, tool=tool, value=value, size=size)
        self._items[handle] = item
        while len(self._items) > self._max:
            old, _ = self._items.popitem(last=False)
            self._evicted.add(old)
        return item

    def get(self, handle: str) -> StoredResult:
        item = self._items.get(handle)
        if item is not None:
            return item
        if handle in self._evicted:
            raise KeyError(
                f"result handle {handle!r} was evicted (only the newest "
                f"{self._max} large results are kept); re-run the tool"
            )
        raise KeyError(f"unknown result handle {handle!r}; handles look like 'result-3'")

    def read(
        self,
        handle: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        """A window of a stored result, or of one of its top-level keys."""
        item = self.get(handle)
        cap = inline_limit()
        width = read_chars() if limit is None else max(1, min(int(limit), cap))
        start = max(0, int(offset))
        value = item.value
        if key is not None:
            if not isinstance(value, dict) or key not in value:
                available = list(value)[:_SUMMARY_KEYS] if isinstance(value, dict) else []
                raise KeyError(f"{handle} has no top-level key {key!r}; keys: {available}")
            value = value[key]
        text = value if isinstance(value, str) else _render(value)
        end = start + width
        out: dict[str, Any] = {
            "handle": handle,
            "total_chars": len(text),
            "offset": start,
            "content": text[start:end],
            "next_offset": end if end < len(text) else None,
        }
        if key is not None:
            out["key"] = key
        return out

    def offer(self, tool: str, value: Any) -> str | None:
        """The model-facing text for ``value`` if it is over the limit, else None.

        Callers render inline themselves when this returns None, so results at
        or under the limit are byte-identical to what they were before.
        """
        if tool == READER_TOOL_NAME:
            return None
        text = _render(value)
        if len(text) <= inline_limit():
            return None
        item = self.put(tool, value, len(text))
        logger.info("tool_result_handled", tool=tool, handle=item.handle, chars=item.size)
        return _render(
            {
                "result_handle": item.handle,
                "tool": tool,
                "size_chars": item.size,
                "summary": summarize(value),
                "note": (
                    f"Result is {item.size} chars, too large to inline. Read more with "
                    f"{READER_TOOL_NAME}(handle='{item.handle}', offset=0) or pass "
                    "key='<top-level key>' for one field. A read returns "
                    f"{read_chars()} chars unless you pass limit (up to "
                    f"{inline_limit()}). Only read what you need."
                ),
            }
        )


READER_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "handle": {"type": "string", "description": "A result_handle from an earlier tool result."},
        "offset": {"type": "integer", "description": "Character offset to start at (default 0)."},
        "limit": {
            "type": "integer",
            "description": (
                "Max characters to return. Omit for a small default window; larger "
                "values are capped."
            ),
        },
        "key": {"type": "string", "description": "Read one top-level key of the result instead."},
    },
    "required": ["handle"],
}


# -- clearing old results from a long turn (MetaForge, 2026-11-11) ------------------------

#: Env var for the threshold, in characters of old results; 0 turns clearing off.
CLEAR_CHARS_ENV = "METAFORGE_TOOL_RESULT_CLEAR_CHARS"
#: Old results (all but the newest few) are cleared once they pass this many characters
#: together (about 12k tokens). High on purpose: clearing breaks the prompt cache after the
#: first cleared message, so it should happen a few times in a long turn, not every step.
DEFAULT_CLEAR_CHARS = 48_000
#: The newest results stay verbatim: the model is usually still working from them.
KEEP_RECENT_RESULTS = 3
#: A result this small stays: its stub would save little.
MIN_CLEAR_CHARS = 1_000


def clear_chars() -> int:
    """The configured clearing threshold; 0 means never clear."""
    raw = os.environ.get(CLEAR_CHARS_ENV, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("tool_result_clear_chars_invalid", value=raw)
    return DEFAULT_CLEAR_CHARS


def _is_stub(content: str) -> bool:
    return content.startswith('{"cleared": true')


def _existing_handle(content: str) -> str | None:
    """The handle of a result that is already a summary plus a handle (``offer``)."""
    if not content.startswith("{") or '"result_handle"' not in content:
        return None
    try:
        value = json.loads(content)
    except ValueError:
        return None
    handle = value.get("result_handle") if isinstance(value, dict) else None
    return handle if isinstance(handle, str) else None


def clear_stale_results(
    messages: list[dict[str, Any]],
    store: ResultStore,
    *,
    keep_recent: int = KEEP_RECENT_RESULTS,
    min_chars: int = MIN_CLEAR_CHARS,
) -> list[dict[str, Any]]:
    """``messages`` with old tool results cleared, once they pass the threshold together.

    Old means every ``tool`` message but the newest ``keep_recent``. When those, counting
    only results of at least ``min_chars``, pass :func:`clear_chars` characters, each is
    stored behind a handle (or keeps the one it already has) and replaced by a stub naming
    the tool, the size and how to read it again with ``read_tool_result``. The tool call
    and the model's own words about the result stay. Returns ``messages`` itself when
    nothing is cleared; never changes it in place.
    """
    limit = clear_chars()
    if limit <= 0:
        return messages
    names: dict[str, str] = {}
    for m in messages:
        for call in m.get("tool_calls") or []:
            names[str(call.get("id"))] = str((call.get("function") or {}).get("name") or "tool")
    results = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old = results[: max(0, len(results) - keep_recent)]
    targets = [
        i
        for i in old
        if isinstance(messages[i].get("content"), str)
        and len(messages[i]["content"]) >= min_chars
        and not _is_stub(messages[i]["content"])
    ]
    total = sum(len(messages[i]["content"]) for i in targets)
    if total < limit:
        return messages
    out = list(messages)
    for i in targets:
        content = messages[i]["content"]
        tool = names.get(str(messages[i].get("tool_call_id")), "tool")
        handle = _existing_handle(content) or store.put(tool, content, len(content)).handle
        stub = {
            "cleared": True,
            "tool": tool,
            "result_handle": handle,
            "size_chars": len(content),
            "note": (
                "This result was cleared from the conversation to save context; what you "
                "concluded from it above still stands. Read it again with "
                f"{READER_TOOL_NAME}(handle='{handle}') only if you need a detail you did "
                "not note."
            ),
        }
        out[i] = {**messages[i], "content": json.dumps(stub)}
    logger.info(
        "tool_results_cleared",
        results=len(targets),
        chars=total,
        kept_recent=min(keep_recent, len(results)),
    )
    return out
