"""Client-agnostic capture core (MET-497).

Exercises CaptureClient against a fake gateway (httpx.MockTransport) with an
injected state_root, plus the CLI's never-fail guarantees and a contract test
that emitted events validate against MET-493's SessionEventCreateRequest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tools.session_capture import metaforge_capture as mc
from tools.session_capture.metaforge_capture import CaptureClient


class _FakeGateway:
    """Records requests and serves canned session-API responses."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self._seq: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if request.method == "POST" and path == "/v1/sessions":
            return httpx.Response(201, json={"id": "sess-1", "status": "running"})
        if request.method == "POST" and path.endswith("/events"):
            sid = path.split("/")[3]
            self._seq[sid] = self._seq.get(sid, 0) + 1
            return httpx.Response(201, json={"event_id": "ev", "seq": self._seq[sid]})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "sess-1", "status": body.get("status")})
        return httpx.Response(404, json={})

    def events(self) -> list[dict[str, Any]]:
        return [b for (m, p, b) in self.requests if m == "POST" and p.endswith("/events")]

    def session_posts(self) -> int:
        return sum(1 for (m, p, _) in self.requests if m == "POST" and p == "/v1/sessions")


def _client(gw: _FakeGateway, tmp: Path) -> CaptureClient:
    http = httpx.Client(base_url="http://gw.test", transport=httpx.MockTransport(gw.handler))
    return CaptureClient("claude-code", http=http, state_root=tmp)


class TestCaptureClient:
    def test_ensure_session_is_lazy_and_cached(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        sid1 = c.ensure_session("cc-1", agent_code="claude-code", task_type="design")
        sid2 = c.ensure_session("cc-1", agent_code="claude-code", task_type="design")
        assert sid1 == sid2 == "sess-1"
        assert gw.session_posts() == 1  # created once, then cached

    def test_push_event_creates_then_posts(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        ack = c.push_event("cc-1", type="action", message="did a thing", data={"k": "v"})
        assert ack["seq"] == 1
        assert gw.session_posts() == 1
        assert gw.events()[0] == {"type": "action", "message": "did a thing", "data": {"k": "v"}}

    def test_invalid_type_coerced(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        c.push_event("cc-1", type="bogus", message="x")
        assert gw.events()[0]["type"] == "observation"

    def test_transcript_delta_extracts_assistant_thoughts(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        tpath = tmp_path / "t.jsonl"
        lines = [
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking A"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking B"}]}},
        ]
        tpath.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

        pushed = c.push_transcript_delta("cc-1", str(tpath))
        assert pushed == 2
        msgs = [e["message"] for e in gw.events()]
        assert msgs == ["thinking A", "thinking B"]
        assert all(e["type"] == "thought" for e in gw.events())

    def test_transcript_delta_only_new_bytes(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        tpath = tmp_path / "t.jsonl"
        tpath.write_text(
            json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}
            )
            + "\n"
        )
        assert c.push_transcript_delta("cc-1", str(tpath)) == 1
        # No new bytes → nothing pushed.
        assert c.push_transcript_delta("cc-1", str(tpath)) == 0
        # Append one more assistant line → only that one is pushed.
        with tpath.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "second"}]},
                    }
                )
                + "\n"
            )
        assert c.push_transcript_delta("cc-1", str(tpath)) == 1
        assert [e["message"] for e in gw.events()] == ["first", "second"]

    def test_complete_patches(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        c.ensure_session("cc-1", agent_code="cc", task_type="t")
        assert c.complete("cc-1", status="completed", summary="done") is True
        assert any(m == "PATCH" for (m, _p, _b) in gw.requests)

    def test_complete_without_session_is_noop(self, tmp_path: Path) -> None:
        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        assert c.complete("never-started") is False

    def test_contract_events_validate_against_schema(self, tmp_path: Path) -> None:
        """Every emitted event body must satisfy MET-493's request schema."""
        from api_gateway.sessions.schemas import SessionEventCreateRequest

        gw = _FakeGateway()
        c = _client(gw, tmp_path)
        c.push_event("cc-1", type="decision", message="chose X", data={"why": "best"})
        tpath = tmp_path / "t.jsonl"
        tpath.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "reasoned"}]},
                }
            )
            + "\n"
        )
        c.push_transcript_delta("cc-1", str(tpath))

        for body in gw.events():
            SessionEventCreateRequest(**body)  # raises if the contract is violated


class TestCli:
    def test_kill_switch_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_SESSION_CAPTURE", "off")

        def _boom(*a: Any, **k: Any) -> None:
            raise AssertionError("CaptureClient must not be constructed when disabled")

        monkeypatch.setattr(mc, "CaptureClient", _boom)
        assert (
            mc.main(
                [
                    "--client",
                    "cc",
                    "--session",
                    "s",
                    "push-event",
                    "--type",
                    "thought",
                    "--message",
                    "m",
                ]
            )
            == 0
        )

    def test_failure_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("METAFORGE_SESSION_CAPTURE", raising=False)

        class _Raising:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def push_event(self, *a: Any, **k: Any) -> None:
                raise RuntimeError("gateway down")

        monkeypatch.setattr(mc, "CaptureClient", _Raising)
        rc = mc.main(
            [
                "--client",
                "cc",
                "--session",
                "s",
                "push-event",
                "--type",
                "thought",
                "--message",
                "m",
            ]
        )
        assert rc == 0
