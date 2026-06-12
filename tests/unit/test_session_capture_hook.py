"""Claude Code hook adapter dispatch (MET-497).

Loads .claude/hooks/metaforge_session_push.py, fakes the capture core, and
feeds recorded Claude Code hook payloads on stdin — asserting each
hook_event_name maps to the right core call. Never-fail behaviour included.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

_HOOK = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "session_capture"
    / "claude_code_adapter.py"
)


def _load_hook():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("claude_code_adapter", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Recorder:
    def __init__(self, *a: Any, **k: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def push_event(self, sid: str, **kw: Any) -> None:
        self.calls.append(("push_event", {"sid": sid, **kw}))

    def push_transcript_delta(self, sid: str, transcript: str, **kw: Any) -> None:
        self.calls.append(("push_transcript_delta", {"sid": sid, "transcript": transcript}))

    def complete(self, sid: str, **kw: Any) -> None:
        self.calls.append(("complete", {"sid": sid, **kw}))


class _FakeCore:
    def __init__(self) -> None:
        self.recorder = _Recorder()

    def capture_enabled(self) -> bool:
        return True

    def CaptureClient(self, *a: Any, **k: Any) -> _Recorder:  # noqa: N802
        return self.recorder


def _run(hook, payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    core = _FakeCore()
    monkeypatch.setattr(hook, "_load_core", lambda: core)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = hook.main()
    assert rc == 0
    return core.recorder


def test_post_tool_use_pushes_action(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook()
    rec = _run(
        hook,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "cc-1",
            "tool_name": "mcp__metaforge__twin_query_cypher",
            "tool_input": {"cypher": "MATCH (n) RETURN n"},
        },
        monkeypatch,
    )
    assert rec.calls[0][0] == "push_event"
    assert rec.calls[0][1]["type"] == "action"
    assert rec.calls[0][1]["message"] == "mcp__metaforge__twin_query_cypher"


def test_stop_pushes_transcript_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook()
    rec = _run(
        hook,
        {"hook_event_name": "Stop", "session_id": "cc-1", "transcript_path": "/tmp/t.jsonl"},
        monkeypatch,
    )
    assert rec.calls == [("push_transcript_delta", {"sid": "cc-1", "transcript": "/tmp/t.jsonl"})]


def test_session_end_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook()
    rec = _run(
        hook,
        {"hook_event_name": "SessionEnd", "session_id": "cc-1"},
        monkeypatch,
    )
    assert rec.calls[0][0] == "complete"


def test_missing_session_id_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook()
    rec = _run(hook, {"hook_event_name": "Stop"}, monkeypatch)
    assert rec.calls == []


def test_garbage_stdin_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook()
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hook.main() == 0
