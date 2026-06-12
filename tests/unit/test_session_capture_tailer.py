"""Transcript tailer + per-client parsers (MET-498).

Parser registry + Claude Code / Codex parsers, and the parser-driven
``CaptureClient.push_delta`` tailer against a fake gateway — including
cursor restart-safety and the shared event contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from tools.session_capture import parsers
from tools.session_capture.metaforge_capture import CaptureClient


class _FakeGateway:
    def __init__(self) -> None:
        self.events_posted: list[dict[str, Any]] = []
        self._seq = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/sessions":
            return httpx.Response(201, json={"id": "sess-1", "status": "running"})
        if request.method == "POST" and path.endswith("/events"):
            self._seq += 1
            self.events_posted.append(json.loads(request.content))
            return httpx.Response(201, json={"event_id": "ev", "seq": self._seq})
        return httpx.Response(404, json={})


def _client(gw: _FakeGateway, tmp: Path) -> CaptureClient:
    http = httpx.Client(base_url="http://gw.test", transport=httpx.MockTransport(gw.handler))
    return CaptureClient("tail-test", http=http, state_root=tmp)


class TestParsers:
    def test_registry(self) -> None:
        assert parsers.get_parser("claude-code") is not None
        assert parsers.get_parser("codex") is not None
        assert parsers.get_parser("nope") is None

    def test_claude_code_text_and_tool_use(self) -> None:
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "reasoning here"},
                    {"type": "tool_use", "name": "mcp__metaforge__project_list"},
                ]
            },
        }
        out = parsers.claude_code_parser(entry)
        assert out[0][0] == "thought" and out[0][1] == "reasoning here"
        assert out[1][0] == "action" and out[1][1] == "mcp__metaforge__project_list"

    def test_claude_code_ignores_non_assistant(self) -> None:
        assert parsers.claude_code_parser({"type": "user", "message": {"content": "hi"}}) == []

    def test_codex_assistant_text(self) -> None:
        # top-level role/content
        out = parsers.codex_parser({"role": "assistant", "content": "codex thought"})
        assert out == [("thought", "codex thought", {"source": "codex-tail"})]
        # nested message with output_text blocks
        nested = {
            "message": {"role": "assistant", "content": [{"type": "output_text", "text": "nested"}]}
        }
        assert parsers.codex_parser(nested)[0][1] == "nested"

    def test_codex_ignores_user(self) -> None:
        assert parsers.codex_parser({"role": "user", "content": "x"}) == []


class TestTailer:
    def test_push_delta_emits_parser_events(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        tpath = tmp_path / "cc-abc.jsonl"
        tpath.write_text(
            json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "A"}]}}
            )
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "twin.query_cypher"}]},
                }
            )
            + "\n"
        )
        pushed = c.push_delta("cc-abc", str(tpath), parsers.claude_code_parser)
        assert pushed == 2
        types = [e["type"] for e in gw.events_posted]
        assert types == ["thought", "action"]

    def test_cursor_restart_safety(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        tpath = tmp_path / "cc-xyz.jsonl"
        tpath.write_text(
            json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "one"}]}}
            )
            + "\n"
        )
        assert c.push_delta("cc-xyz", str(tpath), parsers.claude_code_parser) == 1
        # No new bytes → nothing re-emitted.
        assert c.push_delta("cc-xyz", str(tpath), parsers.claude_code_parser) == 0
        # A fresh client (simulating a restarted tailer) reads the persisted
        # cursor and still doesn't re-emit the old line.
        c2 = _client(gw, tmp_path)
        assert c2.push_delta("cc-xyz", str(tpath), parsers.claude_code_parser) == 0
        assert len(gw.events_posted) == 1

    def test_contract_tailer_events_validate(self, tmp_path: Path) -> None:
        from api_gateway.sessions.schemas import SessionEventCreateRequest

        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        tpath = tmp_path / "codex-1.jsonl"
        tpath.write_text(json.dumps({"role": "assistant", "content": "decision rationale"}) + "\n")
        c.push_delta("codex-1", str(tpath), parsers.codex_parser)
        for body in gw.events_posted:
            SessionEventCreateRequest(**body)
