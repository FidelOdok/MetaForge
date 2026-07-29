"""Unit tests for the SSE context-window stats (MET-10). Network-free.

Covers the model→window lookup, the per-turn context snapshot breakdown
(system / project brief / history / tools / message with included-vs-available
counts), and the `context.stats` SSE event helper + type.
"""

from __future__ import annotations

import json

import pytest

from api_gateway.chat.harness_backend import (
    compute_context_stats,
    context_window_for,
)
from api_gateway.chat.streaming import (
    ChatStreamManager,
    StreamEvent,
    StreamEventType,
    notify_context_stats,
)

# --- model → context window ------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-4-8", 200_000),
        ("claude-opus-4-8[1m]", 1_000_000),  # the 1M-context variant wins
        ("gpt-4o", 128_000),
        ("gpt-4.1-mini", 1_000_000),
        ("gemini-2.0-flash", 1_000_000),
        ("some-unknown-local-model", 128_000),  # safe default
    ],
)
def test_context_window_for(model: str, expected: int) -> None:
    assert context_window_for("anthropic", model) == expected


def test_context_window_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_CONTEXT_WINDOW", "40000")
    assert context_window_for("anthropic", "claude-opus-4-8") == 40_000


# --- a fake runtime exposing tools like the harness runtime does ------------


class _Tool:
    def __init__(self, name: str, schema: dict) -> None:
        self.name = name
        self.description = f"desc for {name}"
        self.input_schema = schema


class _Tools:
    def __init__(self, tools: list[_Tool]) -> None:
        self._tools = tools

    def all_tools(self) -> list[_Tool]:
        return self._tools


class _Runtime:
    def __init__(self, tools: list[_Tool]) -> None:
        self.tools = _Tools(tools)


def _runtime(n: int = 3) -> _Runtime:
    return _Runtime([_Tool(f"t{i}", {"type": "object", "properties": {}}) for i in range(n)])


# --- compute_context_stats -------------------------------------------------


def test_stats_breakdown_and_headroom() -> None:
    history = [
        {"role": "user", "content": "[project context]\nProject Foo with 42 work products"},
        {"role": "assistant", "content": "Understood, scoped to Foo."},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    stats = compute_context_stats(
        runtime=_runtime(3),
        system="You are MetaForge's assistant.",
        history=history,
        user_content="make a bracket",
        provider="anthropic",
        model="claude-opus-4-8",
        tools_available=48,
        availability={
            "work_products_total": 42,
            "work_products_shown": 30,
            "history_turns_total": 57,
        },
    )

    assert stats["window"] == 200_000
    assert stats["estimated"] is True
    # used is the sum of the component tokens, and headroom is window - used.
    assert stats["used"] == sum(c["tokens"] for c in stats["components"])
    assert stats["available"] == stats["window"] - stats["used"]
    assert 0.0 <= stats["utilization"] <= 1.0

    comps = {c["key"]: c for c in stats["components"]}
    assert set(comps) == {"system", "project_brief", "history", "tools", "message"}

    # The leading "[project context]" pair is bucketed as the brief, not history.
    assert comps["project_brief"]["items_included"] == 30
    assert comps["project_brief"]["items_available"] == 42
    # Two real conversation turns remain after splitting off the brief pair.
    assert comps["history"]["items_included"] == 2
    assert comps["history"]["items_available"] == 57
    # Tools: 3 registered/enabled out of 48 available.
    assert comps["tools"]["items_included"] == 3
    assert comps["tools"]["items_available"] == 48
    assert all(c["tokens"] >= 0 for c in stats["components"])


def test_stats_without_project_or_availability() -> None:
    """No project brief and no availability totals: brief bucket is omitted."""
    stats = compute_context_stats(
        runtime=_runtime(0),
        system="sys",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        user_content="and now?",
        provider="openai",
        model="gpt-4o",
        tools_available=0,
    )
    keys = {c["key"] for c in stats["components"]}
    assert "project_brief" not in keys
    history = next(c for c in stats["components"] if c["key"] == "history")
    assert history["items_included"] == 2
    assert "items_available" not in history  # no total supplied


def test_more_tools_increases_used_tokens() -> None:
    common = dict(
        system="sys",
        history=None,
        user_content="q",
        provider="anthropic",
        model="claude-opus-4-8",
        tools_available=10,
    )
    few = compute_context_stats(runtime=_runtime(1), **common)
    many = compute_context_stats(runtime=_runtime(9), **common)
    assert many["used"] > few["used"]


# --- SSE event -------------------------------------------------------------


def test_context_stats_event_type_and_serialization() -> None:
    assert StreamEventType.CONTEXT_STATS == "context.stats"
    event = StreamEvent(
        event=StreamEventType.CONTEXT_STATS,
        data={"window": 200000, "used": 1234, "available": 198766},
        thread_id="thread-1",
    )
    sse = event.to_sse()
    assert "event: context.stats" in sse
    # payload is valid JSON carrying the metrics
    data_line = next(ln for ln in sse.splitlines() if ln.startswith("data:"))
    payload = json.loads(data_line[len("data:") :].strip())
    assert payload["data"]["available"] == 198766


@pytest.mark.asyncio
async def test_notify_context_stats_broadcasts(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.chat.streaming as streaming

    mgr = ChatStreamManager()
    monkeypatch.setattr(streaming, "stream_manager", mgr)
    queue = mgr.subscribe("thread-9")

    sent = await notify_context_stats("thread-9", {"window": 128000, "used": 10})
    assert sent == 1
    event = await queue.get()
    assert event is not None
    assert event.event == StreamEventType.CONTEXT_STATS
    assert event.data["window"] == 128000
