"""Unit tests for `forge chat` (MET-556).

Exercise the REPL foundation with a stubbed client — no gateway, no network.
"""

from __future__ import annotations

import argparse
from typing import Any

from cli.forge_cli.chat import _agent_replies_after, _render_stream, handle_chat


class StubClient:
    """Minimal duck-typed ForgeClient for the chat handler."""

    def __init__(self, *, thread_messages: list[dict[str, Any]] | None = None) -> None:
        self.thread_messages = thread_messages or []
        self.sent: list[str] = []
        self.created = False

    def create_thread(self, scope_kind: str, scope_entity_id: str, **_: Any) -> dict[str, Any]:
        self.created = True
        assert scope_kind == "assistant"
        return {"id": "t-123"}

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        return {"id": thread_id, "messages": self.thread_messages}

    def send_message(self, thread_id: str, content: str, **_: Any) -> dict[str, Any]:
        self.sent.append(content)
        return {"id": "u-1", "thread_id": thread_id, "actor_kind": "user", "content": content}


def _args(**over: Any) -> argparse.Namespace:
    base = dict(
        message="hello",
        thread=None,
        session=None,
        title=None,
        provider=None,
        model=None,
        timeout=120.0,
        no_color=True,
        no_stream=True,  # unit tests exercise the non-streaming path
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_agent_replies_after_collects_agent_messages_following_user() -> None:
    messages = [
        {"id": "u-1", "actor_kind": "user", "content": "hi"},
        {"id": "a-1", "actor_kind": "agent", "content": "hello there"},
        {"id": "a-2", "actor_kind": "agent", "content": "anything else?"},
    ]
    replies = _agent_replies_after(messages, "u-1")
    assert [m["id"] for m in replies] == ["a-1", "a-2"]


def test_agent_replies_after_falls_back_to_trailing_agent_messages() -> None:
    messages = [
        {"id": "x", "actor_kind": "user", "content": "hi"},
        {"id": "a-1", "actor_kind": "agent", "content": "reply"},
    ]
    # user_msg_id not present → trailing agent messages
    replies = _agent_replies_after(messages, "missing")
    assert [m["id"] for m in replies] == ["a-1"]


def test_agent_replies_after_ignores_user_after_reply() -> None:
    messages = [
        {"id": "u-1", "actor_kind": "user", "content": "hi"},
        {"id": "a-1", "actor_kind": "agent", "content": "reply"},
        {"id": "u-2", "actor_kind": "user", "content": "next"},
    ]
    replies = _agent_replies_after(messages, "u-1")
    assert [m["id"] for m in replies] == ["a-1"]


def test_handle_chat_oneshot_creates_thread_and_sends(capsys: Any) -> None:
    client = StubClient(
        thread_messages=[
            {"id": "u-1", "actor_kind": "user", "content": "hello"},
            {"id": "a-1", "actor_kind": "agent", "content": "Hi! I am MetaForge."},
        ]
    )
    handle_chat(_args(), client)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert client.created is True
    assert client.sent == ["hello"]
    assert "Hi! I am MetaForge." in out


def test_handle_chat_oneshot_reuses_thread_when_given(capsys: Any) -> None:
    client = StubClient(
        thread_messages=[
            {"id": "u-1", "actor_kind": "user", "content": "hello"},
            {"id": "a-1", "actor_kind": "agent", "content": "reusing"},
        ]
    )
    handle_chat(_args(thread="t-existing"), client)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert client.created is False  # reused, not created
    assert "reusing" in out


def test_handle_chat_reports_no_reply_gracefully(capsys: Any) -> None:
    client = StubClient(thread_messages=[{"id": "u-1", "actor_kind": "user", "content": "hello"}])
    handle_chat(_args(), client)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "no reply" in out.lower()


# --- streaming renderer (MET-557) ------------------------------------------


def test_render_stream_assembles_deltas_and_stops_on_done(capsys: Any) -> None:
    events = [
        {"event": "agent.typing", "data": {"agent_id": "harness-agent"}},
        {"event": "message.delta", "data": {"delta": "Hello"}},
        {"event": "message.delta", "data": {"delta": ", world"}},
        {"event": "agent.done", "data": {"agent_id": "harness-agent"}},
        {"event": "message.delta", "data": {"delta": "IGNORED after done"}},
    ]
    text = _render_stream(events, color=False)
    out = capsys.readouterr().out
    assert text == "Hello, world"
    assert "Hello, world" in out
    assert "IGNORED" not in out  # stopped at agent.done


def test_render_stream_renders_tool_timeline() -> None:
    events = [
        {
            "event": "agent.step",
            "data": {
                "step": {
                    "index": 0,
                    "tool": "twin_get_node",
                    "arguments": {"node_id": "abc"},
                    "observation": {"ok": True},
                    "error": None,
                    "final": False,
                }
            },
        },
        {"event": "agent.done", "data": {}},
    ]
    text = _render_stream(events, color=False)
    assert text == ""  # no answer deltas, just a tool step


def test_render_stream_shows_tool_error(capsys: Any) -> None:
    events = [
        {
            "event": "agent.step",
            "data": {
                "step": {
                    "tool": "calculix_run_fea",
                    "arguments": {},
                    "error": "adapter down (-32001)",
                    "final": False,
                }
            },
        },
        {"event": "agent.done", "data": {}},
    ]
    _render_stream(events, color=False)
    out = capsys.readouterr().out
    assert "calculix_run_fea" in out
    assert "-32001" in out


def test_render_stream_skips_final_step() -> None:
    events = [
        {"event": "agent.step", "data": {"step": {"tool": None, "final": True}}},
        {"event": "agent.done", "data": {}},
    ]
    # final step carries no tool line; nothing to assert beyond no crash
    assert _render_stream(events, color=False) == ""
