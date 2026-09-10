"""Minimal stub MetaForge gateway for hermetic TUI QA.

Implements only the endpoints the `forge` TUI touches at startup and during a
chat turn, so the real binary can be exercised without a live gateway. The chat
stream emits scripted SSE:

- normal: enveloped `message.delta` frames spelling a reply, then `agent.done`.
- malformed (message contains the ``__malformed__`` sentinel): enveloped
  `message.delta` frames whose ``data`` has *no* ``delta`` field, reproducing the
  exact "deltas arrived but 0 characters" payload/parse mismatch behind the
  historical "(no reply)" bug — so QA can assert the cause is surfaced.

Run standalone (`python3 stub_gateway.py`) or via `start_stub()` from the QA
harness.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MALFORMED = "__malformed__"
# Projects the stub serves, so QA can drive `/project` / `--project` by name.
PROJECTS = [
    {"id": "p1", "name": "QA Project", "status": "active"},
    {"id": "p2", "name": "QA Gimbal", "status": "active"},
]
# Every thread the client created, in order — QA reads this back from
# `GET /__threads` to assert the *wire* scope, not just what the UI claims.
_threads: list[dict[str, object]] = []
# Stream the reply but deliberately omit `agent.done`, reproducing a completion
# event lost on an SSE reconnect. A correct client finalizes the turn anyway
# (POST-resolve fallback) instead of hanging on "thinking" forever.
DROP_DONE = "__drop_done__"
# Script a LONG agentic turn: a stack of agent.thinking / agent.step events and
# then a tall multi-line streamed answer — the shape of a real 20+-step CAD
# turn. Reproduces the "glitching while thinking" class of bug where the live
# (non-Static) region outgrows the terminal and Ink strands garbage frames
# into scrollback on every repaint.
LONG_TURN = "__long_turn__"
REPLY_TOKENS = ["Hello", "! ", "This ", "is ", "the ", "QA ", "stub ", "reply."]

# Per-thread queues bridge POST /messages -> the open GET /stream (like the real
# gateway's pub/sub). A posted message's content lands here; the stream drains it.
_pending: dict[str, queue.Queue[str]] = {}
_lock = threading.Lock()


def _q(tid: str) -> queue.Queue[str]:
    with _lock:
        return _pending.setdefault(tid, queue.Queue())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:  # keep QA output clean
        pass

    def _json(self, code: int, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        p = self.path
        if p == "/health":
            return self._json(200, {"status": "healthy", "version": "qa-stub"})
        if p == "/v1/projects":
            return self._json(200, {"projects": PROJECTS})
        if p == "/__threads":  # QA introspection, not a gateway route
            return self._json(200, {"threads": _threads})
        if p == "/v1/runs":
            return self._json(200, {"runs": []})
        if p == "/v1/twin/nodes":
            return self._json(200, {"nodes": []})
        if p.endswith("/stream"):
            return self._stream(p.split("/threads/")[1].split("/")[0])
        if "/v1/chat/threads/" in p:
            return self._json(200, {"messages": []})
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        p = self.path
        if p == "/v1/chat/threads":
            # A distinct id per thread: switching project creates a *new* thread,
            # and reusing one id would let the old stream serve the new one.
            tid = f"qa-thread-{len(_threads) + 1}"
            _threads.append(
                {
                    "id": tid,
                    "scope_kind": data.get("scope_kind"),
                    "scope_entity_id": data.get("scope_entity_id"),
                }
            )
            return self._json(201, {"id": tid})
        if p.endswith("/messages"):
            tid = p.split("/threads/")[1].split("/")[0]
            _q(tid).put(str(data.get("content", "")))
            return self._json(201, {"id": "m1"})
        return self._json(404, {"error": "not found"})

    def _stream(self, tid: str) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        # Stay open for the thread's lifetime; script one reply per posted message.
        while True:
            try:
                content = _q(tid).get(timeout=120)
            except queue.Empty:
                return
            self._emit("agent.typing", {"agent_id": "qa"}, tid)
            if LONG_TURN in content:
                if not self._long_turn(tid):
                    return
            elif MALFORMED in content:
                # Enveloped delta events with NO `delta` field -> parse to "".
                for _ in range(2):
                    if not self._emit("message.delta", {"agent_id": "agent"}, tid):
                        return
                    time.sleep(0.05)
            else:
                for tok in REPLY_TOKENS:
                    if not self._emit("message.delta", {"delta": tok, "agent_id": "agent"}, tid):
                        return
                    time.sleep(0.05)
            # A dropped-completion turn streams its reply but never signals done.
            if DROP_DONE not in content:
                self._emit("agent.done", {"agent_id": "qa"}, tid)

    def _long_turn(self, tid: str) -> bool:
        """A 12-step agentic turn followed by a ~40-line streamed answer,
        paced so QA can capture the screen mid-turn."""
        for i in range(12):
            for j in range(6):  # live reasoning draft, cleared by each step
                if not self._emit(
                    "agent.thinking",
                    {"delta": f"considering approach {i}.{j} for the fixture… ", "kind": "draft"},
                    tid,
                ):
                    return False
                time.sleep(0.01)
            if not self._emit("agent.action_started", {"tool": f"freecad.op_{i}"}, tid):
                return False
            time.sleep(0.05)
            step = {
                "index": i,
                "thought": f"step {i}: refine the bracket geometry and re-check clearances",
                "tool": f"freecad.op_{i}",
                "arguments": {"session": "qa", "op": i, "part": f"bracket_{i}"},
                "observation": "ok",
            }
            if not self._emit("agent.step", {"step": step}, tid):
                return False
            time.sleep(0.05)
        for i in range(40):  # tall final answer: ~40 rendered lines
            tok = f"- line {i:02d}: the bracket wall thickness was validated against the spec\n"
            if not self._emit("message.delta", {"delta": tok, "agent_id": "agent"}, tid):
                return False
            time.sleep(0.04)
        return True

    def _emit(self, event: str, data: dict, tid: str) -> bool:
        env = {"data": data, "thread_id": tid, "timestamp": "qa"}
        try:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(env)}\n\n".encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False


def start_stub(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    server, url = start_stub(port)
    print(f"stub gateway on {url}  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
